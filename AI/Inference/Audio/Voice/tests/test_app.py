import io
import sys
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app


class FakeModel:
    sampling_rate = 24000

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        import torch

        return [torch.zeros((1, 240), dtype=torch.float32)]


class FailingModel:
    sampling_rate = 24000

    def generate(self, **kwargs):
        raise RuntimeError("generation failed")


def test_generate_accepts_multipart_reference_audio(monkeypatch):
    fake_model = FakeModel()
    monkeypatch.setattr(app, "MODEL", fake_model)
    monkeypatch.setattr(app, "RUNTIME_SETTINGS", app.RuntimeSettings(
        model_name="k2-fsa/OmniVoice",
        served_model_name="OmniVoice",
        host="0.0.0.0",
        port=8000,
        device="cpu",
    ))
    monkeypatch.setattr(app, "_render_wav_bytes", lambda audio, sample_rate: b"wav-bytes")

    client = TestClient(app.app)
    response = client.post(
        "/generate",
        data={
            "text": "Hello from OmniVoice",
            "ref_text": "Reference transcript",
            "language": "English",
            "speed": "1.25",
        },
        files={"ref_audio": ("ref.wav", io.BytesIO(b"fake-wav"), "audio/wav")},
    )

    assert response.status_code == 200
    assert response.content == b"wav-bytes"
    assert response.headers["content-type"].startswith("audio/wav")
    assert fake_model.calls[0]["text"] == "Hello from OmniVoice"
    assert fake_model.calls[0]["ref_text"] == "Reference transcript"
    assert fake_model.calls[0]["language"] == "English"
    assert fake_model.calls[0]["speed"] == 1.25
    assert Path(fake_model.calls[0]["ref_audio"]).suffix == ".wav"


def test_render_wav_bytes_accepts_numpy_audio(monkeypatch):
    calls = []

    def fake_save(buffer, waveform, sample_rate, format):
        calls.append((waveform, sample_rate, format))
        buffer.write(b"wav-bytes")

    monkeypatch.setattr(app.torchaudio, "save", fake_save)

    wav_bytes = app._render_wav_bytes(np.zeros(240, dtype=np.float32), 24000)

    assert wav_bytes == b"wav-bytes"
    assert calls[0][0].shape == (1, 240)
    assert calls[0][0].dtype == app.torch.float32
    assert calls[0][1] == 24000
    assert calls[0][2] == "wav"


def test_generate_requires_loaded_model(monkeypatch):
    monkeypatch.setattr(app, "MODEL", None)
    client = TestClient(app.app)

    response = client.post(
        "/generate",
        data={"text": "Hello", "ref_text": "Reference transcript"},
        files={"ref_audio": ("ref.wav", io.BytesIO(b"fake-wav"), "audio/wav")},
    )

    assert response.status_code == 503


def test_generate_returns_502_when_model_generation_fails(monkeypatch):
    monkeypatch.setattr(app, "MODEL", FailingModel())
    client = TestClient(app.app)

    response = client.post(
        "/generate",
        data={"text": "Hello", "ref_text": "Reference transcript"},
        files={"ref_audio": ("ref.wav", io.BytesIO(b"fake-wav"), "audio/wav")},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "OmniVoice generation failed."}
