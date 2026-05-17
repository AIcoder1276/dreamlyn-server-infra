FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* /app/
RUN uv sync --locked --no-dev || uv sync --no-dev

COPY src /app/src

EXPOSE 8000

ENTRYPOINT ["uv", "run", "python", "/app/src/app.py"]
CMD []
