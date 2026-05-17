import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class MusicalDtoTests(unittest.TestCase):
    def test_load_settings_reads_nested_config(self):
        import dto

        config = {
            "musicgen_api_base": "http://musicgen:8000/",
            "s3": {
                "endpoint": "http://swfs-s3:8333/",
                "region": "us-east-1",
                "bucket": "dreamlyn-music",
                "prefix": "musicgen",
                "public_base_url": "http://s3.example.test",
                "access_key_env": "CUSTOM_MUSIC_USER",
                "secret_key_env": "CUSTOM_MUSIC_PASSWORD",
            },
            "folders": {
                "logs": "/tmp/music-logs",
            },
            "generation": {
                "duration_seconds": {
                    "min": 5,
                    "max": 7,
                    "default": 6,
                },
                "default_format": "wav",
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            original_config_path = dto.CONFIG_PATH
            original_example_path = dto.CONFIG_EXAMPLE_PATH
            try:
                dto.CONFIG_PATH = config_path
                dto.CONFIG_EXAMPLE_PATH = Path(f"{config_path}.example")

                settings = dto.load_settings()
            finally:
                dto.CONFIG_PATH = original_config_path
                dto.CONFIG_EXAMPLE_PATH = original_example_path

        self.assertEqual("http://musicgen:8000", settings.musicgen_api_base)
        self.assertEqual("http://swfs-s3:8333", settings.s3_endpoint_url)
        self.assertEqual("dreamlyn-music", settings.s3_bucket)
        self.assertEqual("musicgen", settings.s3_prefix)
        self.assertEqual("http://s3.example.test", settings.public_base_url)
        self.assertEqual("CUSTOM_MUSIC_USER", settings.s3_access_key_env)
        self.assertEqual("CUSTOM_MUSIC_PASSWORD", settings.s3_secret_key_env)
        self.assertEqual(Path("/tmp/music-logs"), settings.log_dir)
        self.assertEqual(5.0, settings.min_duration)
        self.assertEqual(7.0, settings.max_duration)
        self.assertEqual(6.0, settings.default_duration)
        self.assertEqual("wav", settings.default_format)

    def test_load_settings_preserves_flat_config_compatibility(self):
        import dto

        config = {
            "musicgen_api_base": "http://musicgen:8000",
            "s3_endpoint_url": "http://swfs-s3:8333",
            "s3_region_name": "us-east-1",
            "s3_bucket": "dreamlyn-music",
            "s3_prefix": "musicgen",
            "min_duration_seconds": 5,
            "max_duration_seconds": 7,
            "default_duration_seconds": 6,
            "default_format": "wav",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            original_config_path = dto.CONFIG_PATH
            original_example_path = dto.CONFIG_EXAMPLE_PATH
            original_public_base_url = os.environ.get("S3_STORAGE_PUBLIC_BASE_URL")
            try:
                os.environ["S3_STORAGE_PUBLIC_BASE_URL"] = "http://s3.example.test"
                dto.CONFIG_PATH = config_path
                dto.CONFIG_EXAMPLE_PATH = Path(f"{config_path}.example")

                settings = dto.load_settings()
            finally:
                dto.CONFIG_PATH = original_config_path
                dto.CONFIG_EXAMPLE_PATH = original_example_path
                if original_public_base_url is None:
                    os.environ.pop("S3_STORAGE_PUBLIC_BASE_URL", None)
                else:
                    os.environ["S3_STORAGE_PUBLIC_BASE_URL"] = original_public_base_url

        self.assertEqual("http://musicgen:8000", settings.musicgen_api_base)
        self.assertEqual("http://swfs-s3:8333", settings.s3_endpoint_url)
        self.assertEqual("dreamlyn-music", settings.s3_bucket)
        self.assertEqual("musicgen", settings.s3_prefix)
        self.assertEqual("http://s3.example.test", settings.public_base_url)


class MusicalSrcLayoutTests(unittest.TestCase):
    def test_app_module_loads_from_src_layout(self):
        os.environ.setdefault("S3_STORAGE_PUBLIC_BASE_URL", "http://s3.example.test")
        os.environ.setdefault("STORAGE_MUSIC_USER_LOGIN", "test-user")
        os.environ.setdefault("STORAGE_MUSIC_USER_PASSWORD", "test-password")

        import app

        self.assertEqual("Musical Wrapper Gateway", app.app.title)


if __name__ == "__main__":
    unittest.main()
