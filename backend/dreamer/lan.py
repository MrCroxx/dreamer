"""Explicit, single-shot local dispatch. Never changes printer operating mode.

Protocol references: https://github.com/Doridian/OpenBambuAPI and
https://github.com/bambulab/BambuStudio. Firmware support requires device testing.
"""

import ftplib
import hashlib
import json
import secrets
import socket
import ssl
import struct
import sys
import time
import zipfile
from pathlib import Path

from .config import Settings


def tls_context(settings):
    context = ssl.create_default_context(cafile=settings.printer_ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Legacy BBL roots lack the extensions required by Python 3.13's strict mode.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _field(value):
    value = value.encode()
    return struct.pack("!H", len(value)) + value


def _packet(kind, body):
    size, length = len(body), bytearray()
    while True:
        digit, size = size % 128, size // 128
        length.append(digit | (128 if size else 0))
        if not size:
            return bytes([kind]) + length + body


class MQTT:
    """A bounded MQTT 3.1.1 session without reconnects or publish retries."""

    def __init__(self, settings):
        self.settings = settings
        self.sock = None

    def __enter__(self):
        raw = socket.create_connection((self.settings.printer_host, 8883), timeout=10)
        try:
            self.sock = tls_context(self.settings).wrap_socket(
                raw, server_hostname=self.settings.printer_serial
            )
            self.sock.sendall(
                _packet(
                    0x10,
                    _field("MQTT")
                    + bytes([4, 0xC2])
                    + struct.pack("!H", 600)
                    + _field("dreamer-" + secrets.token_hex(8))
                    + _field("bblp")
                    + _field(self.settings.printer_access_code),
                )
            )
            kind, body = self.receive(time.monotonic() + 10)
            if kind != 0x20 or body != bytes([0, 0]):
                raise ValueError("Printer MQTT authentication was refused")
            self.sock.sendall(
                _packet(
                    0x82,
                    struct.pack("!H", 1)
                    + _field(f"device/{self.settings.printer_serial}/report")
                    + bytes([0]),
                )
            )
            deadline = time.monotonic() + 10
            while True:
                kind, body = self.receive(deadline)
                if kind == 0x90:
                    if body != bytes([0, 1, 0]):
                        raise ValueError("Printer MQTT subscription was refused")
                    break
            return self
        except BaseException:
            (self.sock or raw).close()
            raise

    def __exit__(self, *_):
        self.sock.close()

    def receive(self, deadline):
        def exact(size):
            result = bytearray()
            while len(result) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Printer response timed out")
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(size - len(result))
                if not chunk:
                    raise ConnectionError("Printer connection closed")
                result.extend(chunk)
            return bytes(result)

        kind, size = exact(1)[0], 0
        for index in range(4):
            digit = exact(1)[0]
            size += (digit & 127) * 128**index
            if size > 2_000_000:
                raise ValueError("Printer MQTT packet exceeds the limit")
            if not digit & 128:
                return kind, exact(size)
        raise ValueError("Invalid MQTT packet length")

    def publish(self, payload):
        # QoS 0 deliberately avoids retransmitting a physical print command.
        self.sock.sendall(
            _packet(
                0x30,
                _field(f"device/{self.settings.printer_serial}/request")
                + json.dumps(payload).encode(),
            )
        )

    def report(self, deadline):
        while True:
            kind, body = self.receive(deadline)
            if kind != 0x30:  # Ignore retained or unsolicited QoS packets.
                continue
            length = int.from_bytes(body[:2], "big")
            topic = body[2 : 2 + length].decode()
            if topic != f"device/{self.settings.printer_serial}/report":
                continue
            value = json.loads(body[2 + length :])
            if isinstance(value, dict):
                return value

    def status(self):
        # A new session prevents queued old telemetry from satisfying preflight.
        self.publish({"pushing": {"sequence_id": "0", "command": "pushall"}})
        deadline, status = time.monotonic() + 20, {}
        while True:
            value = self.report(deadline).get("print", {})
            if isinstance(value, dict):
                status.update(value)
            if {"gcode_state", "nozzle_diameter", "sdcard", "ams"} <= status.keys():
                return status

    def start(self, payload):
        sequence = payload["print"]["sequence_id"]
        self.publish(payload)
        deadline = time.monotonic() + 25
        while True:
            result = self.report(deadline).get("print", {})
            if (
                result.get("command") == "project_file"
                and str(result.get("sequence_id")) == sequence
            ):
                if str(result.get("result", "")).upper() != "SUCCESS":
                    raise ValueError("Printer rejected the print command")
                return


class FTPS(ftplib.FTP_TLS):
    """Implicit TLS with serial validation and control-session reuse for data."""

    def __init__(self, settings):
        super().__init__(context=tls_context(settings), timeout=30)
        self.serial = settings.printer_serial
        self.host = settings.printer_host
        raw = socket.create_connection((self.host, 990), timeout=self.timeout)
        try:
            self.sock = self.context.wrap_socket(raw, server_hostname=self.serial)
            self.af = self.sock.family
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            self.login("bblp", settings.printer_access_code)
            self.prot_p()
        except BaseException:
            raw.close()
            self.close()
            raise

    def ntransfercmd(self, cmd, rest=None):
        connection, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        try:
            connection = self.context.wrap_socket(
                connection, server_hostname=self.serial, session=self.sock.session
            )
            return connection, size
        except BaseException:
            connection.close()
            raise


def inspect_slice(path: Path, settings):
    with zipfile.ZipFile(path) as archive:
        gcodes = [n for n in archive.namelist() if n.endswith(".gcode")]
        if gcodes != ["Metadata/plate_1.gcode"]:
            raise ValueError("LAN dispatch requires exactly one sliced plate")
        project = json.loads(archive.read("Metadata/project_settings.config"))
        if project.get("printer_model") != "Bambu Lab A1 mini":
            raise ValueError("The LAN adapter currently supports A1 mini slices only")
        if project.get("filament_type") is None or len(project["filament_type"]) != 1:
            raise ValueError("LAN dispatch currently requires a single filament")
        if project.get("curr_bed_type") != settings.bed_type:
            raise ValueError("Slice build plate does not match the configured plate")
        nozzle = project.get("nozzle_diameter", [])
        if len(nozzle) != 1:
            raise ValueError("Slice nozzle configuration is missing or ambiguous")
        gcode = archive.read(gcodes[0])
        if not gcode:
            raise ValueError("Sliced G-code is empty")
        return {
            "nozzle": float(nozzle[0]),
            "material": project["filament_type"][0],
            "gcode_md5": hashlib.md5(gcode).hexdigest(),
        }


def slot_index(settings):
    if settings.filament_slot not in {"A1", "A2", "A3", "A4"}:
        raise ValueError("Select an AMS Lite slot from A1 through A4")
    return int(settings.filament_slot[1]) - 1


def preflight(status, sliced, settings):
    if status.get("gcode_state") not in {"IDLE", "FINISH"}:
        raise ValueError("Printer is busy or its idle state is unknown")
    if status.get("sdcard") is not True:
        raise ValueError("Printer SD card is unavailable")
    if status.get("print_error", 0) or status.get("hms"):
        raise ValueError("Clear printer errors before dispatch")
    if float(status.get("nozzle_diameter", 0)) != sliced["nozzle"]:
        raise ValueError("Printer nozzle does not match the slice")
    trays = []
    for unit in status.get("ams", {}).get("ams", []):
        if str(unit.get("id")) == "0":
            trays = unit.get("tray", [])
    tray = next((t for t in trays if str(t.get("id")) == str(slot_index(settings))), {})
    if tray.get("tray_type") != sliced["material"]:
        raise ValueError(
            "AMS Lite slot material is missing or does not match the slice"
        )


def upload(settings, path, remote):
    with FTPS(settings) as ftp:
        with path.open("rb") as stream:
            ftp.storbinary("STOR " + remote, stream)
        if ftp.size(remote) != path.stat().st_size:
            raise ValueError("Uploaded file size does not match")
        actual = hashlib.sha256()
        ftp.retrbinary("RETR " + remote, actual.update)
        if actual.hexdigest() != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Uploaded file checksum does not match")


def dispatch(settings, path, *, mqtt_factory=MQTT, uploader=upload):
    if not settings.print_enabled or settings.printer_transport != "lan":
        raise ValueError("LAN printing is disabled")
    sliced = inspect_slice(path, settings)
    slot = slot_index(settings)
    with mqtt_factory(settings) as mqtt:
        preflight(mqtt.status(), sliced, settings)
    # Unique names avoid overwriting another job or a file already being printed.
    remote = "dreamer-" + secrets.token_hex(16) + ".gcode.3mf"
    uploader(settings, path, remote)
    with mqtt_factory(settings) as mqtt:
        preflight(mqtt.status(), sliced, settings)
        mqtt.start(
            {
                "print": {
                    "sequence_id": str(secrets.randbelow(2**31 - 1) + 1),
                    "command": "project_file",
                    "param": "Metadata/plate_1.gcode",
                    "url": "ftp:///" + remote,
                    "subtask_name": remote.removesuffix(".gcode.3mf"),
                    "project_id": "0",
                    "profile_id": "0",
                    "task_id": "0",
                    "subtask_id": "0",
                    "md5": sliced["gcode_md5"],
                    "bed_type": "auto",
                    "timelapse": False,
                    "bed_leveling": True,
                    "flow_cali": True,
                    "vibration_cali": True,
                    "layer_inspect": True,
                    "use_ams": True,
                    "ams_mapping": [slot],
                }
            }
        )


if __name__ == "__main__":
    try:
        dispatch(Settings(), Path(sys.argv[1]))
    except Exception as exc:  # noqa: BLE001 - never expose transport secrets
        # Transport exceptions can contain credentials or device identifiers.
        print(
            f"LAN dispatch failed ({type(exc).__name__}); verify the printer before retrying."
        )
        raise SystemExit(1)
    print("Printer acknowledged the submitted job.")
