import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from logging.handlers import RotatingFileHandler
from typing import Any, Iterator, Optional
from urllib.parse import quote

import boto3
import httpx
from botocore.client import Config
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from dto import (
    APP_NAME,
    CONFIG_EXAMPLE_PATH,
    CONFIG_PATH,
    SUPPORTED_FORMATS,
    SUPPORTED_OUTPUT_MODES,
    ChatCompletionRequest,
    ChatMessage,
    load_settings,
)


SETTINGS = load_settings()


def configure_logging() -> logging.Logger:
    SETTINGS.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        SETTINGS.log_dir / "musical-wrapper.log",
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


LOGGER = configure_logging()


def build_s3_client():
    s3_access_key = os.getenv(SETTINGS.s3_access_key_env)
    s3_secret_key = os.getenv(SETTINGS.s3_secret_key_env)
    if not s3_access_key or not s3_secret_key:
        raise RuntimeError("S3 credentials must be provided in '.env' file.")

    return boto3.client(
        "s3",
        endpoint_url=SETTINGS.s3_endpoint_url,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=SETTINGS.s3_region_name,
        config=Config(signature_version="s3v4"),
    )


S3_CLIENT = build_s3_client()
app = FastAPI(title="Musical Wrapper Gateway")


def extract_prompt(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            if content.strip():
                return content.strip()
            continue
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        fragments.append(text)
            prompt = "\n".join(fragments).strip()
            if prompt:
                return prompt
    raise HTTPException(status_code=400, detail="No user text prompt was found in the request messages.")


def resolve_request_option(payload: ChatCompletionRequest, key: str, default: Any = None) -> Any:
    if key in payload.extra_body:
        return payload.extra_body[key]
    model_extra = payload.model_extra or {}
    return getattr(payload, key, model_extra.get(key, default))


def validate_duration(raw_duration: Any) -> float:
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="duration_seconds must be numeric.") from exc

    if duration < SETTINGS.min_duration or duration > SETTINGS.max_duration:
        raise HTTPException(
            status_code=400,
            detail=(
                f"duration_seconds must be within [{SETTINGS.min_duration}, {SETTINGS.max_duration}] seconds."
            ),
        )
    return duration


def validate_audio_format(raw_format: Any) -> str:
    if raw_format is None:
        return SETTINGS.default_format
    audio_format = str(raw_format).lower()
    if audio_format not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"audio_format must be one of: {sorted(SUPPORTED_FORMATS)}")
    return audio_format


def validate_output_mode(raw_mode: Any) -> str:
    if raw_mode is None:
        return "user"
    output_mode = str(raw_mode).lower()
    if output_mode not in SUPPORTED_OUTPUT_MODES:
        raise HTTPException(status_code=400, detail=f"output_mode must be one of: {sorted(SUPPORTED_OUTPUT_MODES)}")
    return output_mode


def ensure_bucket_exists() -> None:
    existing_buckets = {bucket["Name"] for bucket in S3_CLIENT.list_buckets().get("Buckets", [])}
    if SETTINGS.s3_bucket not in existing_buckets:
        S3_CLIENT.create_bucket(Bucket=SETTINGS.s3_bucket)


async def request_musicgen(prompt: str, duration_seconds: float, seed: Optional[int]) -> bytes:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "duration_seconds": duration_seconds,
    }
    if seed is not None:
        payload["seed"] = int(seed)

    timeout = max(60.0, duration_seconds * 20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{SETTINGS.musicgen_api_base}/generate", json=payload)

    if response.status_code != 200:
        LOGGER.error("MusicGen backend failed: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="MusicGen backend request failed.")

    return response.content


def convert_wav_to_mp3(wav_bytes: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as input_file:
        input_file.write(wav_bytes)
        input_path = input_file.name

    output_path = f"{input_path}.mp3"
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                input_path,
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                output_path,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            LOGGER.error("ffmpeg conversion failed: %s", completed.stderr.strip())
            raise HTTPException(status_code=500, detail="Failed to convert generated WAV into MP3.")

        with open(output_path, "rb") as output_file:
            return output_file.read()
    finally:
        for path in (input_path, output_path):
            if os.path.exists(path):
                os.remove(path)


def make_storage_key(audio_format: str) -> str:
    date_prefix = time.strftime("%Y/%m/%d")
    return f"{SETTINGS.s3_prefix.strip('/')}/{date_prefix}/{uuid.uuid4().hex}.{audio_format}"


def upload_audio(audio_bytes: bytes, key: str, audio_format: str) -> str:
    ensure_bucket_exists()
    content_type = "audio/mpeg" if audio_format == "mp3" else "audio/wav"
    S3_CLIENT.put_object(
        Bucket=SETTINGS.s3_bucket,
        Key=key,
        Body=audio_bytes,
        ContentType=content_type,
    )
    return f"{SETTINGS.public_base_url}/{quote(key)}"


def build_device_payload(
    *,
    prompt: str,
    duration_seconds: float,
    audio_format: str,
    seed: Optional[int],
    url: str,
    storage_key: str,
) -> dict[str, Any]:
    return {
        "type": "music_generation",
        "prompt": prompt,
        "url": url,
        "storage_key": storage_key,
        "duration_seconds": duration_seconds,
        "format": audio_format,
        "seed": seed,
    }


def build_user_message(device_payload: dict[str, Any]) -> str:
    lines = [
        "Music generation completed.",
        f"URL: {device_payload['url']}",
        f"Format: {device_payload['format']}",
        f"Duration: {device_payload['duration_seconds']}s",
    ]
    if device_payload["seed"] is not None:
        lines.append(f"Seed: {device_payload['seed']}")
    lines.append(f"Storage key: {device_payload['storage_key']}")
    return "\n".join(lines)


def build_chat_response(
    *,
    request_model: str,
    response_id: str,
    created: int,
    content: Any,
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def stream_chat_response(
    *,
    request_model: str,
    response_id: str,
    created: int,
    content: str,
) -> Iterator[str]:
    initial_chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "",
                },
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(initial_chunk)}\n\n"

    content_chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": content,
                },
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(content_chunk)}\n\n"

    final_chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "musicgen_api_base": SETTINGS.musicgen_api_base,
            "bucket": SETTINGS.s3_bucket,
            "public_base_url": SETTINGS.public_base_url,
            "config_path": str(CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH),
        }
    )


@app.post("/v1/chat/completions")
async def create_chat_completion(payload: ChatCompletionRequest):
    prompt = extract_prompt(payload.messages)
    duration_seconds = validate_duration(
        resolve_request_option(payload, "duration_seconds", SETTINGS.default_duration)
    )
    seed_value = resolve_request_option(payload, "seed")
    seed = int(seed_value) if seed_value is not None else None
    audio_format = validate_audio_format(resolve_request_option(payload, "audio_format", SETTINGS.default_format))
    output_mode = validate_output_mode(resolve_request_option(payload, "output_mode", "user"))

    LOGGER.info(
        "Received request model=%s user=%s duration=%s format=%s seed=%s output_mode=%s stream=%s",
        payload.model,
        payload.user,
        duration_seconds,
        audio_format,
        seed,
        output_mode,
        payload.stream,
    )

    wav_bytes = await request_musicgen(prompt, duration_seconds, seed)
    output_bytes = convert_wav_to_mp3(wav_bytes) if audio_format == "mp3" else wav_bytes
    storage_key = make_storage_key(audio_format)
    public_url = upload_audio(output_bytes, storage_key, audio_format)
    device_payload = build_device_payload(
        prompt=prompt,
        duration_seconds=duration_seconds,
        audio_format=audio_format,
        seed=seed,
        url=public_url,
        storage_key=storage_key,
    )

    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    if output_mode == "user":
        content: Any = build_user_message(device_payload)
        stream_content = content
    else:
        content = json.dumps(device_payload)
        stream_content = content

    LOGGER.info("Completed request model=%s storage_key=%s", payload.model, storage_key)

    if payload.stream:
        return StreamingResponse(
            stream_chat_response(
                request_model=payload.model,
                response_id=response_id,
                created=created,
                content=stream_content,
            ),
            media_type="text/event-stream",
        )

    response = build_chat_response(
        request_model=payload.model,
        response_id=response_id,
        created=created,
        content=content,
    )
    return JSONResponse(response)
