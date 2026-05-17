import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


APP_NAME = "musical-wrapper"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.getenv("MUSICAL_CONFIG_PATH", str(DEFAULT_CONFIG_DIR / "config.yml")))
CONFIG_EXAMPLE_PATH = Path(f"{CONFIG_PATH}.example")
LOG_DIR = Path(os.getenv("MUSICAL_LOG_DIR", "/app/Logs"))
SUPPORTED_FORMATS = {"wav", "mp3"}
SUPPORTED_OUTPUT_MODES = {"user", "device"}


@dataclass(frozen=True)
class MusicalSettings:
    musicgen_api_base: str
    s3_endpoint_url: str
    s3_region_name: str
    s3_bucket: str
    s3_prefix: str
    public_base_url: str
    s3_access_key_env: str
    s3_secret_key_env: str
    log_dir: Path
    min_duration: float
    max_duration: float
    default_duration: float
    default_format: str


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    user: Optional[str] = None
    extra_body: dict[str, Any] = Field(default_factory=dict)


def _config_value(config: dict[str, Any], section: str, key: str, legacy_key: str, default: Any = None) -> Any:
    section_config = config.get(section)
    if isinstance(section_config, dict) and key in section_config:
        return section_config[key]
    return config.get(legacy_key, default)


def _duration_value(config: dict[str, Any], key: str, legacy_key: str) -> Any:
    generation_config = config.get("generation")
    if isinstance(generation_config, dict):
        duration_config = generation_config.get("duration_seconds")
        if isinstance(duration_config, dict) and key in duration_config:
            return duration_config[key]
    return config[legacy_key]


def load_settings() -> MusicalSettings:
    config_source = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    if not config_source.exists():
        raise RuntimeError(f"Missing musical config: {CONFIG_PATH}")

    with config_source.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise RuntimeError(f"{config_source} must contain a YAML mapping.")

    s3_public_base_url = _config_value(config, "s3", "public_base_url", "public_base_url", "")
    public_base_url = str(os.getenv("S3_STORAGE_PUBLIC_BASE_URL", s3_public_base_url)).rstrip("/")
    if not public_base_url:
        raise RuntimeError("S3 public base URL must be provided in config or S3_STORAGE_PUBLIC_BASE_URL.")

    default_format = str(_config_value(config, "generation", "default_format", "default_format")).lower()
    if default_format not in SUPPORTED_FORMATS:
        raise RuntimeError(f"Unsupported default_format: {default_format}")

    min_duration = float(_duration_value(config, "min", "min_duration_seconds"))
    max_duration = float(_duration_value(config, "max", "max_duration_seconds"))
    default_duration = float(_duration_value(config, "default", "default_duration_seconds"))
    if not min_duration <= default_duration <= max_duration:
        raise RuntimeError("default_duration_seconds must be within the configured min/max duration bounds.")

    return MusicalSettings(
        musicgen_api_base=str(config["musicgen_api_base"]).rstrip("/"),
        s3_endpoint_url=str(_config_value(config, "s3", "endpoint", "s3_endpoint_url")).rstrip("/"),
        s3_region_name=str(_config_value(config, "s3", "region", "s3_region_name", "us-east-1")),
        s3_bucket=str(_config_value(config, "s3", "bucket", "s3_bucket")),
        s3_prefix=str(_config_value(config, "s3", "prefix", "s3_prefix")),
        public_base_url=public_base_url,
        s3_access_key_env=str(_config_value(config, "s3", "access_key_env", "s3_access_key_env", "STORAGE_MUSIC_USER_LOGIN")),
        s3_secret_key_env=str(
            _config_value(config, "s3", "secret_key_env", "s3_secret_key_env", "STORAGE_MUSIC_USER_PASSWORD")
        ),
        log_dir=Path(_config_value(config, "folders", "logs", "log_dir", LOG_DIR)),
        min_duration=min_duration,
        max_duration=max_duration,
        default_duration=default_duration,
        default_format=default_format,
    )
