import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

from .commands import run_command
from .config import Settings


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slicer_ready(settings: Settings) -> bool:
    return bool(
        shutil.which(settings.slicer_bin)
        and all(
            Path(p).is_file()
            for p in [
                settings.machine_profile,
                settings.process_profile,
                settings.filament_profile,
            ]
            if p
        )
        and settings.machine_profile
        and settings.process_profile
        and settings.filament_profile
    )


def validate_profiles(settings: Settings):
    if not slicer_ready(settings):
        raise ValueError(
            "Install Bambu Studio and configure machine, process and filament profile paths"
        )
    for value in [
        settings.machine_profile,
        settings.process_profile,
        settings.filament_profile,
    ]:
        profile = json.loads(Path(value).read_text())
        if profile.get("inherits") or profile.get("include"):
            raise ValueError(
                "Slicer presets must be full flattened profiles, without inherits/include"
            )


def preset_names(settings: Settings) -> list[str]:
    names = []
    for value in (
        settings.machine_profile,
        settings.process_profile,
        settings.filament_profile,
    ):
        try:
            name = json.loads(Path(value).read_text()).get("name")
            if isinstance(name, str):
                names.append(name)
        except (OSError, ValueError, AttributeError):
            continue
    return names


async def slice_model(settings: Settings, directory: Path):
    validate_profiles(settings)
    output = directory / "print.gcode.3mf"
    if output.exists():
        output.unlink()
    await run_command(
        [
            settings.slicer_bin,
            "--load-settings",
            f"{Path(settings.machine_profile).resolve()};{Path(settings.process_profile).resolve()}",
            "--load-filaments",
            str(Path(settings.filament_profile).resolve()),
            "--curr-bed-type",
            settings.bed_type,
            "--arrange",
            "1",
            "--slice",
            "0",
            "--outputdir",
            str(directory),
            "--export-3mf",
            output.name,
            str(directory / "model.stl"),
        ],
        settings.command_timeout,
    )
    if not output.is_file() or not zipfile.is_zipfile(output):
        raise ValueError("Slicer did not produce a valid 3MF archive")
    with zipfile.ZipFile(output) as archive:
        if not any(
            name.endswith(".gcode") and archive.getinfo(name).file_size > 0
            for name in archive.namelist()
        ):
            raise ValueError(
                "3MF contains no sliced G-code; refusing to send a model-only archive"
            )
    return digest(output)


def print_ready(settings: Settings) -> bool:
    if settings.printer_transport == "lan":
        return bool(
            settings.print_enabled
            and settings.printer_name
            and settings.printer_host
            and settings.printer_serial
            and settings.printer_access_code
            and Path(settings.printer_ca_file).is_file()
            and slicer_ready(settings)
        )
    return bool(
        settings.print_enabled
        and settings.printer_name
        and shutil.which(settings.bambu_bin)
    )


async def send_print(settings: Settings, path: Path):
    if not print_ready(settings):
        raise ValueError(
            "Printing is disabled or the selected transport is not configured"
        )
    if settings.printer_transport == "lan":
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        for key, value in settings.model_dump(mode="json").items():
            env["DREAMER_" + key.upper()] = (
                value if isinstance(value, str) else json.dumps(value)
            )
        await run_command(
            [sys.executable, "-m", "dreamer.lan", str(path.resolve())],
            settings.command_timeout,
            env=env,
        )
        return
    # This CLI performs its own printer/material/nozzle/plate preflight before dispatch.
    await run_command(
        [
            settings.bambu_bin,
            "--printer",
            settings.printer_name,
            "--no-input",
            "print",
            "send",
            str(path),
            "--slot",
            settings.filament_slot,
            "--confirm",
        ],
        settings.command_timeout,
    )
