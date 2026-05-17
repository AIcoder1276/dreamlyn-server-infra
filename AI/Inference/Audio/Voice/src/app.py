import argparse
import io
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

import torch
import torchaudio
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response


DEFAULT_MODEL_NAME = "k2-fsa/OmniVoice"
DEFAULT_SERVED_MODEL_NAME = "OmniVoice"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
LOGGER = logging.getLogger("omnivoice-inference")


@dataclass(frozen=True)
class RuntimeSettings:
    model_name: str
    served_model_name: str
    host: str
    port: int
    device: str


def build_runtime_settings(argv: Optional[list[str]] = None) -> RuntimeSettings:
    parser = argparse.ArgumentParser(description="Internal OmniVoice inference server.")
    parser.add_argument("model", nargs="?", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--served-model-name", default=DEFAULT_SERVED_MODEL_NAME)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)

    if not 1 <= int(args.port) <= 65535:
        raise ValueError("--port must be within [1, 65535].")

    return RuntimeSettings(
        model_name=args.model,
        served_model_name=args.served_model_name,
        host=args.host,
        port=int(args.port),
        device=args.device,
    )


RUNTIME_SETTINGS = build_runtime_settings([])
MODEL = None


def _get_device() -> str:
    if RUNTIME_SETTINGS.device == "cpu":
        return "cpu"
    if RUNTIME_SETTINGS.device == "cuda":
        return "cuda:0"
    return "cuda:0" if torch.cuda.is_available() else "cpu"


def _get_dtype():
    return torch.float16 if _get_device().startswith("cuda") else torch.float32


def _load_model():
    from omnivoice import OmniVoice

    model = OmniVoice.from_pretrained(
        RUNTIME_SETTINGS.model_name,
        device_map=_get_device(),
        dtype=_get_dtype(),
    )
    return model


def _audio_to_tensor(audio: Any) -> torch.Tensor:
    if isinstance(audio, torch.Tensor):
        waveform = audio.detach().cpu()
    else:
        waveform = torch.as_tensor(audio).detach().cpu()

    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 2:
        raise ValueError(f"Generated audio must be 1D or 2D, got shape {tuple(waveform.shape)}.")

    return waveform.to(dtype=torch.float32)


def _render_wav_bytes(audio: Any, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    torchaudio.save(buffer, _audio_to_tensor(audio), sample_rate, format="wav")
    return buffer.getvalue()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global MODEL
    MODEL = _load_model()
    yield


app = FastAPI(title="OmniVoice Internal Inference", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    sample_rate = getattr(MODEL, "sampling_rate", None)
    return JSONResponse(
        {
            "status": "ok",
            "model_loaded": MODEL is not None,
            "model_name": RUNTIME_SETTINGS.model_name,
            "served_model_name": RUNTIME_SETTINGS.served_model_name,
            "cuda": torch.cuda.is_available(),
            "device": _get_device(),
            "sample_rate": sample_rate,
        }
    )


@app.post("/generate")
async def generate(
    text: str = Form(...),
    ref_text: str = Form(...),
    language: Optional[str] = Form(None),
    speed: float = Form(1.0),
    ref_audio: UploadFile = File(...),
) -> Response:
    if MODEL is None:
        raise HTTPException(status_code=503, detail="OmniVoice model is not ready yet.")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty.")
    if not ref_text.strip():
        raise HTTPException(status_code=400, detail="ref_text must not be empty.")
    if speed <= 0:
        raise HTTPException(status_code=400, detail="speed must be greater than 0.")

    suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_file.write(await ref_audio.read())
        temp_path = temp_file.name

    try:
        kwargs = {
            "text": text,
            "ref_audio": temp_path,
            "ref_text": ref_text,
            "speed": float(speed),
        }
        if language:
            kwargs["language"] = language

        try:
            with torch.inference_mode():
                audio = MODEL.generate(**kwargs)

            output = audio[0] if isinstance(audio, list) else audio
            sample_rate = int(getattr(MODEL, "sampling_rate", 24000))
            return Response(
                content=_render_wav_bytes(output, sample_rate),
                media_type="audio/wav",
                headers={
                    "X-OmniVoice-Sample-Rate": str(sample_rate),
                    "X-Served-Model-Name": RUNTIME_SETTINGS.served_model_name,
                },
            )
        except Exception as exc:
            LOGGER.exception("OmniVoice generation failed.")
            raise HTTPException(status_code=502, detail="OmniVoice generation failed.") from exc
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def main() -> None:
    global RUNTIME_SETTINGS
    RUNTIME_SETTINGS = build_runtime_settings()
    uvicorn.run(app, host=RUNTIME_SETTINGS.host, port=RUNTIME_SETTINGS.port)


if __name__ == "__main__":
    main()
