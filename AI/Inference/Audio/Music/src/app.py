import argparse
import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from scipy.io import wavfile
from transformers import AutoProcessor, MusicgenForConditionalGeneration


DEFAULT_MODEL_NAME = "facebook/musicgen-small"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_GUIDANCE_SCALE = 3.0
DEFAULT_MAX_DURATION_SECONDS = 30.0
DEFAULT_MAX_NEW_TOKENS = 1503


@dataclass(frozen=True)
class RuntimeSettings:
    model_name: str
    served_model_name: str
    host: str
    port: int
    guidance_scale: float
    max_duration_seconds: float
    max_new_tokens_at_max_duration: int
    device: str


def build_runtime_settings(argv: Optional[list[str]] = None) -> RuntimeSettings:
    parser = argparse.ArgumentParser(description="OpenAI-compatible MusicGen inference server.")
    parser.add_argument("model", nargs="?", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--served-model-name", default=None)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--guidance-scale", type=float, default=DEFAULT_GUIDANCE_SCALE)
    parser.add_argument("--max-duration-seconds", type=float, default=DEFAULT_MAX_DURATION_SECONDS)
    parser.add_argument(
        "--max-new-tokens-at-max-duration",
        type=int,
        default=DEFAULT_MAX_NEW_TOKENS,
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)

    if args.guidance_scale <= 0.0:
        raise ValueError("--guidance-scale must be greater than 0.")
    if args.max_duration_seconds <= 0.0:
        raise ValueError("--max-duration-seconds must be greater than 0.")
    if args.max_new_tokens_at_max_duration <= 0:
        raise ValueError("--max-new-tokens-at-max-duration must be greater than 0.")
    if not 1 <= int(args.port) <= 65535:
        raise ValueError("--port must be within [1, 65535].")

    served_model_name = args.served_model_name or args.model.rsplit("/", 1)[-1]
    return RuntimeSettings(
        model_name=args.model,
        served_model_name=served_model_name,
        host=args.host,
        port=int(args.port),
        guidance_scale=float(args.guidance_scale),
        max_duration_seconds=float(args.max_duration_seconds),
        max_new_tokens_at_max_duration=int(args.max_new_tokens_at_max_duration),
        device=args.device,
    )


RUNTIME_SETTINGS = build_runtime_settings([])
MODEL: Optional[MusicgenForConditionalGeneration] = None
PROCESSOR: Optional[AutoProcessor] = None


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0.0)
    seed: Optional[int] = None


def _get_device() -> torch.device:
    if RUNTIME_SETTINGS.device == "cpu":
        return torch.device("cpu")
    if RUNTIME_SETTINGS.device == "cuda":
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _estimate_max_new_tokens(duration_seconds: float) -> int:
    scaled = int(
        round(
            (duration_seconds / RUNTIME_SETTINGS.max_duration_seconds)
            * RUNTIME_SETTINGS.max_new_tokens_at_max_duration
        )
    )
    return max(1, min(RUNTIME_SETTINGS.max_new_tokens_at_max_duration, scaled))


def _load_model() -> tuple[AutoProcessor, MusicgenForConditionalGeneration]:
    processor = AutoProcessor.from_pretrained(RUNTIME_SETTINGS.model_name)
    model = MusicgenForConditionalGeneration.from_pretrained(RUNTIME_SETTINGS.model_name)
    model.to(_get_device())
    model.eval()
    return processor, model


def _render_wav_bytes(audio: torch.Tensor, sample_rate: int) -> bytes:
    waveform = audio.detach().cpu().squeeze(0).numpy()
    waveform = np.clip(waveform, -1.0, 1.0)
    pcm16 = (waveform * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    wavfile.write(buffer, sample_rate, pcm16.T)
    return buffer.getvalue()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global MODEL, PROCESSOR
    PROCESSOR, MODEL = _load_model()
    yield


app = FastAPI(title="MusicGen Internal Inference", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "model_loaded": MODEL is not None,
            "model_name": RUNTIME_SETTINGS.model_name,
            "served_model_name": RUNTIME_SETTINGS.served_model_name,
            "cuda": torch.cuda.is_available(),
            "device": str(_get_device()),
            "max_duration_seconds": RUNTIME_SETTINGS.max_duration_seconds,
            "guidance_scale": RUNTIME_SETTINGS.guidance_scale,
        }
    )


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": RUNTIME_SETTINGS.served_model_name,
                    "object": "model",
                    "owned_by": "self-hosted",
                    "root": RUNTIME_SETTINGS.model_name,
                }
            ],
        }
    )


@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    if MODEL is None or PROCESSOR is None:
        raise HTTPException(status_code=503, detail="MusicGen model is not ready yet.")
    if request.duration_seconds > RUNTIME_SETTINGS.max_duration_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"duration_seconds must be within (0, {RUNTIME_SETTINGS.max_duration_seconds}] seconds."
            ),
        )

    if request.seed is not None:
        torch.manual_seed(request.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(request.seed)

    inputs = PROCESSOR(text=[request.prompt], padding=True, return_tensors="pt")
    inputs = {key: value.to(_get_device()) for key, value in inputs.items()}
    max_new_tokens = _estimate_max_new_tokens(float(request.duration_seconds))

    with torch.inference_mode():
        audio = MODEL.generate(
            **inputs,
            do_sample=True,
            guidance_scale=RUNTIME_SETTINGS.guidance_scale,
            max_new_tokens=max_new_tokens,
        )

    sample_rate = int(MODEL.config.audio_encoder.sampling_rate)
    wav_bytes = _render_wav_bytes(audio, sample_rate)
    headers = {
        "X-MusicGen-Sample-Rate": str(sample_rate),
        "X-MusicGen-Max-New-Tokens": str(max_new_tokens),
        "X-Served-Model-Name": RUNTIME_SETTINGS.served_model_name,
        "X-Model-Config": json.dumps(
            {
                "model_name": RUNTIME_SETTINGS.model_name,
                "guidance_scale": RUNTIME_SETTINGS.guidance_scale,
                "max_duration_seconds": RUNTIME_SETTINGS.max_duration_seconds,
            }
        ),
    }
    if request.seed is not None:
        headers["X-MusicGen-Seed"] = str(request.seed)

    return Response(content=wav_bytes, media_type="audio/wav", headers=headers)


def main() -> None:
    global RUNTIME_SETTINGS
    RUNTIME_SETTINGS = build_runtime_settings()
    uvicorn.run(app, host=RUNTIME_SETTINGS.host, port=RUNTIME_SETTINGS.port)


if __name__ == "__main__":
    main()
