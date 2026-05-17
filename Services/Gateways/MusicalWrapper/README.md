# Musical Wrapper

Python 3.12+ gateway that accepts OpenAI-style chat completion requests and converts them into MusicGen generation jobs.

## Local run

```powershell
uv sync
uv run uvicorn --app-dir src app:app --host 0.0.0.0 --port 8000
```

## Required environment

- `STORAGE_MUSIC_USER_LOGIN`
- `STORAGE_MUSIC_USER_PASSWORD`

`S3_STORAGE_PUBLIC_BASE_URL` can override `s3.public_base_url` from config.

## Required config file

Create `config.yml` beside `config.yml.example`.

## Request options

- `extra_body.output_mode=user` returns plain assistant text for chat UIs.
- `extra_body.output_mode=device` returns structured assistant content for machine processing.
