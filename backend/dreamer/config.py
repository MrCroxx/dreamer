from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DREAMER_", env_file=".env", extra="ignore"
    )
    data_dir: Path = Path("data")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_stream: bool = True
    live_preview: bool = True
    api_token: str = ""
    allowed_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ]
    slicer_bin: str = "bambu-studio"
    machine_profile: str = ""
    process_profile: str = ""
    filament_profile: str = ""
    bed_type: str = "Textured PEI Plate"
    bambu_bin: str = "bambu"
    printer_name: str = ""
    filament_slot: str = "A1"
    print_enabled: bool = False
    printer_transport: Literal["cli", "lan"] = "cli"
    printer_host: str = ""
    printer_serial: str = ""
    printer_access_code: str = ""
    printer_ca_file: str = str(Path(__file__).with_name("certs") / "bambu-ca.pem")
    build_volume: tuple[float, float, float] = (180, 180, 180)
    cad_timeout: int = Field(default=90, ge=1)
    command_timeout: int = Field(default=300, ge=1)
