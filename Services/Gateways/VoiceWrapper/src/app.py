import argparse
import hashlib
import logging
import os
import subprocess
import tempfile
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import boto3
import httpx
import uvicorn
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from dto import (
    ACCEPTED_MODEL_NAMES,
    APP_NAME,
    DEFAULT_MODEL_NAME,
    INTERNAL_MODEL_NAME,
    MEDIA_TYPES,
    REFERENCE_MEDIA_TYPES,
    SUPPORTED_FORMATS,
    ResolvedVoice,
    SpeechRequest,
    VoiceReference,
    load_settings,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response


class ReferenceAudioUnavailable(RuntimeError):
    pass


def configure_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_dir / "voice-wrapper.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


SETTINGS = load_settings()
LOGGER = configure_logging(SETTINGS.log_dir)
app = FastAPI(title="OmniVoice Wrapper Gateway")


def resolve_voice(requested_voice: str) -> ResolvedVoice:
    voice = SETTINGS.voices.get(requested_voice)
    if voice is not None:
        return ResolvedVoice(requested_voice, voice, False, requested_voice)

    default = SETTINGS.voices.get(SETTINGS.default_voice)
    if default is None:
        raise HTTPException(status_code=502, detail="Default voice is not configured.")
    return ResolvedVoice(SETTINGS.default_voice, default, True, requested_voice)


def download_reference_audio(url: str) -> bytes:
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        raise ReferenceAudioUnavailable(str(exc)) from exc


def build_s3_client():
    if not SETTINGS.s3_endpoint_url:
        raise ReferenceAudioUnavailable("s3_endpoint_url is not configured.")

    access_key = os.getenv(SETTINGS.s3_access_key_env)
    secret_key = os.getenv(SETTINGS.s3_secret_key_env)
    if not access_key or not secret_key:
        raise ReferenceAudioUnavailable(
            f"S3 credentials are missing: {SETTINGS.s3_access_key_env}/{SETTINGS.s3_secret_key_env}."
        )

    return boto3.client(
        "s3",
        endpoint_url=SETTINGS.s3_endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=SETTINGS.s3_region_name,
        config=Config(signature_version="s3v4"),
    )


def download_reference_audio_from_s3(bucket: str, key: str) -> bytes:
    try:
        response = build_s3_client().get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except (BotoCoreError, ClientError, OSError) as exc:
        raise ReferenceAudioUnavailable(f"S3 object {bucket}/{key} unavailable: {exc}") from exc


def download_voice_reference(voice: VoiceReference) -> bytes:
    if voice.s3_bucket and voice.s3_key:
        return download_reference_audio_from_s3(voice.s3_bucket, voice.s3_key)
    if voice.ref_audio_url:
        return download_reference_audio(voice.ref_audio_url)
    raise ReferenceAudioUnavailable("Voice reference has no configured source.")


def _voice_source_id(voice: VoiceReference) -> str:
    if voice.s3_bucket and voice.s3_key:
        return f"s3://{voice.s3_bucket}/{voice.s3_key}"
    return voice.ref_audio_url or "unknown"


def _voice_source_suffix(voice: VoiceReference) -> str:
    source = voice.s3_key or voice.ref_audio_url or ""
    suffix = Path(source).suffix.lower()
    return suffix if suffix else ".wav"


def _media_type_for_reference(path: Path) -> str:
    return REFERENCE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _cache_path_for_voice(voice_id: str, voice: VoiceReference) -> Path:
    digest = hashlib.sha256(_voice_source_id(voice).encode("utf-8")).hexdigest()[:16]
    return SETTINGS.cache_dir / f"{voice_id}-{digest}{_voice_source_suffix(voice)}"


def fetch_reference_audio(voice_id: str, voice: VoiceReference) -> Path:
    SETTINGS.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _cache_path_for_voice(voice_id, voice)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    audio_bytes = download_voice_reference(voice)
    if not audio_bytes:
        raise ReferenceAudioUnavailable("Reference audio download returned no bytes.")
    cache_path.write_bytes(audio_bytes)
    return cache_path


def resolve_voice_with_available_reference(requested_voice: str) -> tuple[ResolvedVoice, Path]:
    resolved = resolve_voice(requested_voice)
    try:
        return resolved, fetch_reference_audio(resolved.voice_id, resolved.reference)
    except ReferenceAudioUnavailable as exc:
        LOGGER.warning("Voice %s unavailable: %s", resolved.voice_id, exc)
        if resolved.voice_id == SETTINGS.default_voice:
            raise HTTPException(status_code=502, detail="Default voice reference is unavailable.") from exc

    default = SETTINGS.voices.get(SETTINGS.default_voice)
    if default is None:
        raise HTTPException(status_code=502, detail="Default voice is not configured.")
    fallback = ResolvedVoice(SETTINGS.default_voice, default, True, requested_voice)
    try:
        return fallback, fetch_reference_audio(fallback.voice_id, fallback.reference)
    except ReferenceAudioUnavailable as exc:
        raise HTTPException(status_code=502, detail="Default voice reference is unavailable.") from exc


async def request_inference_audio(
    request: SpeechRequest,
    resolved_voice: ResolvedVoice,
    ref_audio_path: Path,
) -> tuple[bytes, str]:
    data = {
        "text": request.input,
        "ref_text": resolved_voice.reference.ref_text,
        "speed": str(request.speed),
    }
    if resolved_voice.reference.language:
        data["language"] = resolved_voice.reference.language

    timeout = max(120.0, len(request.input) * 0.5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with ref_audio_path.open("rb") as ref_audio_file:
            response = await client.post(
                f"{SETTINGS.omnivoice_api_base}/generate",
                data=data,
                files={"ref_audio": (ref_audio_path.name, ref_audio_file, _media_type_for_reference(ref_audio_path))},
            )

    if response.status_code != 200:
        LOGGER.error("OmniVoice inference failed: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="OmniVoice inference request failed.")

    return convert_wav(response.content, request.response_format), MEDIA_TYPES[request.response_format]


def convert_wav(wav_bytes: bytes, output_format: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as input_file:
        input_file.write(wav_bytes)
        input_path = input_file.name

    output_path = f"{input_path}.{output_format}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn"]
    if output_format == "mp3":
        command.extend(["-codec:a", "libmp3lame", "-q:a", "2"])
    elif output_format == "opus":
        command.extend(["-codec:a", "libopus"])
    elif output_format == "aac":
        command.extend(["-codec:a", "aac"])
    elif output_format == "flac":
        command.extend(["-codec:a", "flac"])
    command.append(output_path)

    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            LOGGER.error("ffmpeg conversion failed: %s", completed.stderr.strip())
            raise HTTPException(status_code=500, detail=f"Failed to convert WAV into {output_format}.")
        return Path(output_path).read_bytes()
    finally:
        for path in (input_path, output_path):
            if os.path.exists(path):
                os.remove(path)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "model_name": DEFAULT_MODEL_NAME,
            "internal_model_name": INTERNAL_MODEL_NAME,
            "omnivoice_api_base": SETTINGS.omnivoice_api_base,
            "default_voice": SETTINGS.default_voice,
            "voice_count": len(SETTINGS.voices),
            "supported_formats": sorted(SUPPORTED_FORMATS),
        }
    )


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": DEFAULT_MODEL_NAME,
                    "object": "model",
                    "owned_by": "self-hosted",
                    "root": INTERNAL_MODEL_NAME,
                }
            ],
        }
    )


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest) -> Response:
    if request.model not in ACCEPTED_MODEL_NAMES:
        raise HTTPException(status_code=400, detail=f"model must be one of: {sorted(ACCEPTED_MODEL_NAMES)}.")

    started = time.time()
    response_id = f"speech-{uuid.uuid4().hex}"
    resolved_voice, ref_audio_path = resolve_voice_with_available_reference(request.voice)
    LOGGER.info(
        "Speech request id=%s model=%s requested_voice=%s resolved_voice=%s format=%s fallback=%s",
        response_id,
        request.model,
        request.voice,
        resolved_voice.voice_id,
        request.response_format,
        resolved_voice.fallback_used,
    )

    audio_bytes, media_type = await request_inference_audio(request, resolved_voice, ref_audio_path)
    LOGGER.info("Speech complete id=%s elapsed=%.3fs bytes=%s", response_id, time.time() - started, len(audio_bytes))
    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={
            "X-Request-Id": response_id,
            "X-Requested-Voice": request.voice,
            "X-Resolved-Voice": resolved_voice.voice_id,
            "X-Voice-Fallback": str(resolved_voice.fallback_used).lower(),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible OmniVoice wrapper.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
