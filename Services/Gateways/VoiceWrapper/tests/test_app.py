import sys
from pathlib import Path

from fastapi.testclient import TestClient
import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app
import dto


def test_default_config_paths_use_simple_config_name():
    assert dto.CONFIG_PATH.name == "config.yml"
    assert dto.CONFIG_EXAMPLE_PATH.name == "config.yml.example"


def make_settings(tmp_path):
    return dto.VoiceWrapperSettings(
        omnivoice_api_base="http://voice-inference:8000",
        default_voice="amber",
        voices={
            "amber": dto.VoiceReference(
                ref_audio_url="http://storage/amber.wav",
                ref_text="Amber reference text",
                language="English",
            ),
            "onyx": dto.VoiceReference(
                ref_audio_url="http://storage/onyx.wav",
                ref_text="Onyx reference text",
                language="English",
            ),
        },
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        s3_endpoint_url="http://swfs-s3:8333",
    )


def test_models_lists_elina(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "SETTINGS", make_settings(tmp_path))
    client = TestClient(app.app)

    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "elina"


def test_load_settings_reads_nested_voice_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "omnivoice_api_base": "http://voice-inference:8000",
                "s3": {
                    "endpoint": "http://swfs-s3:8333",
                    "region": "us-east-1",
                    "bucket": "dreamlyn-voice",
                    "access_key_env": "STORAGE_VOICE_USER_LOGIN",
                    "secret_key_env": "STORAGE_VOICE_USER_PASSWORD",
                },
                "folders": {
                    "cache": "/app/Cache",
                    "logs": "/app/logs",
                },
                "voice": {
                    "default": "amber",
                    "variants": {
                        "amber": {
                            "s3_key": "ru-RU/Amber.mp3",
                            "ref_text": "Amber reference text",
                            "language": "English",
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dto, "CONFIG_PATH", config_path)
    monkeypatch.setattr(dto, "CONFIG_EXAMPLE_PATH", Path(f"{config_path}.example"))

    settings = dto.load_settings()

    assert settings.omnivoice_api_base == "http://voice-inference:8000"
    assert settings.s3_endpoint_url == "http://swfs-s3:8333"
    assert settings.s3_region_name == "us-east-1"
    assert settings.s3_bucket == "dreamlyn-voice"
    assert settings.cache_dir == Path("/app/Cache")
    assert settings.log_dir == Path("/app/logs")
    assert settings.default_voice == "amber"
    assert settings.voices["amber"].s3_bucket == "dreamlyn-voice"
    assert settings.voices["amber"].s3_key == "ru-RU/Amber.mp3"


def test_speech_uses_requested_voice_and_returns_audio(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "fetch_reference_audio", lambda voice_id, voice: tmp_path / f"{voice_id}.wav")

    calls = []

    async def fake_generate(request, resolved_voice, ref_audio_path):
        calls.append((request, resolved_voice, ref_audio_path))
        return b"mp3-bytes", "audio/mpeg"

    monkeypatch.setattr(app, "request_inference_audio", fake_generate)
    client = TestClient(app.app)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "elina",
            "input": "Hello endpoint device",
            "voice": "onyx",
            "response_format": "mp3",
            "speed": 1.1,
        },
    )

    assert response.status_code == 200
    assert response.content == b"mp3-bytes"
    assert response.headers["x-requested-voice"] == "onyx"
    assert response.headers["x-resolved-voice"] == "onyx"
    assert response.headers["x-voice-fallback"] == "false"
    assert calls[0][0].input == "Hello endpoint device"
    assert calls[0][1].voice_id == "onyx"


def test_speech_accepts_internal_model_name_from_litellm(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "fetch_reference_audio", lambda voice_id, voice: tmp_path / f"{voice_id}.wav")

    async def fake_generate(request, resolved_voice, ref_audio_path):
        return b"mp3-bytes", "audio/mpeg"

    monkeypatch.setattr(app, "request_inference_audio", fake_generate)
    client = TestClient(app.app)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "OmniVoice",
            "input": "Hello through LiteLLM",
            "voice": "amber",
            "response_format": "mp3",
        },
    )

    assert response.status_code == 200
    assert response.content == b"mp3-bytes"


def test_speech_falls_back_to_default_voice_when_requested_fetch_fails(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app, "SETTINGS", settings)

    def fake_fetch(voice_id, voice):
        if voice_id == "onyx":
            raise app.ReferenceAudioUnavailable("missing onyx")
        return tmp_path / "amber.wav"

    monkeypatch.setattr(app, "fetch_reference_audio", fake_fetch)

    async def fake_generate(request, resolved_voice, ref_audio_path):
        return b"flac-bytes", "audio/flac"

    monkeypatch.setattr(app, "request_inference_audio", fake_generate)
    client = TestClient(app.app)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "elina",
            "input": "Fallback please",
            "voice": "onyx",
            "response_format": "flac",
        },
    )

    assert response.status_code == 200
    assert response.content == b"flac-bytes"
    assert response.headers["x-requested-voice"] == "onyx"
    assert response.headers["x-resolved-voice"] == "amber"
    assert response.headers["x-voice-fallback"] == "true"


def test_speech_errors_when_default_voice_unavailable(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(
        app,
        "fetch_reference_audio",
        lambda voice_id, voice: (_ for _ in ()).throw(app.ReferenceAudioUnavailable("missing")),
    )
    client = TestClient(app.app)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "elina", "input": "Hello", "voice": "unknown"},
    )

    assert response.status_code == 502


def test_fetch_reference_audio_caches_after_first_download(monkeypatch, tmp_path):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app, "SETTINGS", settings)
    downloads = []

    def fake_download(url):
        downloads.append(url)
        return b"reference-bytes"

    monkeypatch.setattr(app, "download_reference_audio", fake_download)

    first = app.fetch_reference_audio("amber", settings.voices["amber"])
    second = app.fetch_reference_audio("amber", settings.voices["amber"])

    assert first == second
    assert first.read_bytes() == b"reference-bytes"
    assert downloads == ["http://storage/amber.wav"]


def test_fetch_reference_audio_reads_s3_object_and_preserves_extension(monkeypatch, tmp_path):
    settings = dto.VoiceWrapperSettings(
        omnivoice_api_base="http://voice-inference:8000",
        default_voice="amber",
        voices={
            "amber": dto.VoiceReference(
                s3_bucket="dreamlyn-voice",
                s3_key="ru-RU/Amber.mp3",
                ref_text="Amber reference text",
                language="English",
            ),
        },
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        s3_endpoint_url="http://swfs-s3:8333",
    )
    monkeypatch.setattr(app, "SETTINGS", settings)
    calls = []

    def fake_download(bucket, key):
        calls.append((bucket, key))
        return b"mp3-reference-bytes"

    monkeypatch.setattr(app, "download_reference_audio_from_s3", fake_download)

    path = app.fetch_reference_audio("amber", settings.voices["amber"])

    assert path.name.endswith(".mp3")
    assert path.read_bytes() == b"mp3-reference-bytes"
    assert app._media_type_for_reference(path) == "audio/mpeg"
    assert calls == [("dreamlyn-voice", "ru-RU/Amber.mp3")]
