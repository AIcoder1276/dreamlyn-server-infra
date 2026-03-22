# Musical Wrapper

Python 3.12+ gateway that accepts OpenAI-style chat completion requests and converts them into MusicGen generation jobs.

## Local venv

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Required environment

- `MUSICAL_PUBLIC_BASE_URL`

## Required config file

Create `config.yml` beside `app.py` from `config.yml.example`.

## Request options

- `extra_body.output_mode=user` returns plain assistant text for chat UIs.
- `extra_body.output_mode=device` returns structured assistant content for machine processing.
