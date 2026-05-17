import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


APP_NAME = "voice-wrapper"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.getenv("VOICE_CONFIG_PATH", str(DEFAULT_CONFIG_DIR / "config.yml")))
CONFIG_EXAMPLE_PATH = Path(f"{CONFIG_PATH}.example")
LOG_DIR = Path(os.getenv("VOICE_LOG_DIR", "/app/logs"))
CACHE_DIR = Path(os.getenv("VOICE_CACHE_DIR", "/app/Cache"))
DEFAULT_MODEL_NAME = "elina"
INTERNAL_MODEL_NAME = "OmniVoice"
ACCEPTED_MODEL_NAMES = {DEFAULT_MODEL_NAME, INTERNAL_MODEL_NAME}
SUPPORTED_FORMATS = {"mp3", "opus", "aac", "flac"}
MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
REFERENCE_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
}


@dataclass(frozen=True)
class VoiceReference:
    ref_text: str
    ref_audio_url: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None
    language: Optional[str] = None


@dataclass(frozen=True)
class VoiceWrapperSettings:
    omnivoice_api_base: str
    default_voice: str
    voices: dict[str, VoiceReference]
    cache_dir: Path
    log_dir: Path
    s3_endpoint_url: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_region_name: str = "us-east-1"
    s3_access_key_env: str = "STORAGE_VOICE_USER_LOGIN"
    s3_secret_key_env: str = "STORAGE_VOICE_USER_PASSWORD"


@dataclass(frozen=True)
class ResolvedVoice:
    voice_id: str
    reference: VoiceReference
    fallback_used: bool
    requested_voice: str


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str = Field(min_length=1)
    voice: str = Field(default="amber", min_length=1)
    response_format: str = "mp3"
    speed: float = 1.0

    @field_validator("response_format")
    @classmethod
    def validate_response_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in SUPPORTED_FORMATS:
            raise ValueError(f"response_format must be one of: {sorted(SUPPORTED_FORMATS)}")
        return normalized

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, value: float) -> float:
        if value < 0.25 or value > 4.0:
            raise ValueError("speed must be within [0.25, 4.0].")
        return value


def load_settings() -> VoiceWrapperSettings:
    config_source = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    if not config_source.exists():
        raise RuntimeError(f"Missing voice config: {CONFIG_PATH}")

    with config_source.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise RuntimeError(f"{config_source} must contain a YAML mapping.")

    voice_config = config.get("voice") if isinstance(config.get("voice"), dict) else {}
    folders_config = config.get("folders") if isinstance(config.get("folders"), dict) else {}
    s3_config = config.get("s3") if isinstance(config.get("s3"), dict) else {}

    voices_config = voice_config.get("variants", config.get("voices"))
    if not isinstance(voices_config, dict) or not voices_config:
        raise RuntimeError("config.yml must define voice.variants with at least one voice.")

    voices: dict[str, VoiceReference] = {}
    shared_s3_bucket = s3_config.get("bucket", config.get("s3_bucket"))
    for voice_id, voice_data in voices_config.items():
        ref_audio_url = voice_data.get("ref_audio_url")
        s3_bucket = voice_data.get("s3_bucket", shared_s3_bucket)
        s3_key = voice_data.get("s3_key")
        if not ref_audio_url and not (s3_bucket and s3_key):
            raise RuntimeError(f"Voice {voice_id} must define ref_audio_url or s3_key with s3.bucket.")
        voices[str(voice_id)] = VoiceReference(
            ref_text=str(voice_data["ref_text"]),
            ref_audio_url=str(ref_audio_url) if ref_audio_url else None,
            s3_bucket=str(s3_bucket) if s3_bucket else None,
            s3_key=str(s3_key) if s3_key else None,
            language=voice_data.get("language"),
        )
    default_voice = str(voice_config.get("default", config.get("default_voice")))
    if default_voice not in voices:
        raise RuntimeError("default_voice must exist in voices.")

    return VoiceWrapperSettings(
        omnivoice_api_base=str(config["omnivoice_api_base"]).rstrip("/"),
        default_voice=default_voice,
        voices=voices,
        cache_dir=Path(folders_config.get("cache", config.get("cache_dir", CACHE_DIR))),
        log_dir=Path(folders_config.get("logs", config.get("log_dir", LOG_DIR))),
        s3_endpoint_url=(
            str(s3_config.get("endpoint", config.get("s3_endpoint_url"))).rstrip("/")
            if s3_config.get("endpoint", config.get("s3_endpoint_url"))
            else None
        ),
        s3_bucket=str(shared_s3_bucket) if shared_s3_bucket else None,
        s3_region_name=str(s3_config.get("region", config.get("s3_region_name", "us-east-1"))),
        s3_access_key_env=str(
            s3_config.get("access_key_env", config.get("s3_access_key_env", "STORAGE_VOICE_USER_LOGIN"))
        ),
        s3_secret_key_env=str(
            s3_config.get("secret_key_env", config.get("s3_secret_key_env", "STORAGE_VOICE_USER_PASSWORD"))
        ),
    )
