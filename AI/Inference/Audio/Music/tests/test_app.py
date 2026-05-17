import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))


def import_app():
    torch = types.ModuleType("torch")
    torch.device = lambda value: value
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.Tensor = object
    torch.inference_mode = lambda: types.SimpleNamespace(__enter__=lambda _: None, __exit__=lambda *_: None)
    sys.modules.setdefault("torch", torch)

    numpy = types.ModuleType("numpy")
    numpy.int16 = "int16"
    numpy.clip = lambda value, _min, _max: value
    sys.modules.setdefault("numpy", numpy)

    scipy = types.ModuleType("scipy")
    scipy_io = types.ModuleType("scipy.io")
    scipy_io.wavfile = types.SimpleNamespace(write=lambda *_: None)
    sys.modules.setdefault("scipy", scipy)
    sys.modules.setdefault("scipy.io", scipy_io)

    transformers = types.ModuleType("transformers")
    transformers.AutoProcessor = object
    transformers.MusicgenForConditionalGeneration = object
    sys.modules.setdefault("transformers", transformers)

    import app

    return app

class MusicInferenceAppTests(unittest.TestCase):
    def test_app_source_lives_under_src(self):
        self.assertTrue((SRC_DIR / "app.py").exists())

    def test_build_runtime_settings_accepts_served_model_name(self):
        app = import_app()
        settings = app.build_runtime_settings(
            [
                "facebook/musicgen-small",
                "--served-model-name",
                "MusicGen-Small",
                "--host",
                "127.0.0.1",
                "--port",
                "8123",
            ]
        )

        self.assertEqual("facebook/musicgen-small", settings.model_name)
        self.assertEqual("MusicGen-Small", settings.served_model_name)
        self.assertEqual("127.0.0.1", settings.host)
        self.assertEqual(8123, settings.port)

    def test_estimate_max_new_tokens_scales_to_duration(self):
        app = import_app()
        app.RUNTIME_SETTINGS = app.RuntimeSettings(
            model_name="facebook/musicgen-small",
            served_model_name="MusicGen-Small",
            host="0.0.0.0",
            port=8000,
            guidance_scale=3.0,
            max_duration_seconds=30.0,
            max_new_tokens_at_max_duration=1500,
            device="cpu",
        )

        self.assertEqual(750, app._estimate_max_new_tokens(15.0))
        self.assertEqual(1500, app._estimate_max_new_tokens(60.0))


if __name__ == "__main__":
    unittest.main()
