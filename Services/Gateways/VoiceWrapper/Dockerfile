FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.29 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock* /app/
RUN uv sync --locked --no-dev || uv sync --no-dev

COPY src /app/src
COPY config.yml* /app/

RUN mkdir -p /app/Logs /app/Cache

EXPOSE 8000

CMD ["uv", "run", "python", "/app/src/app.py", "--host", "0.0.0.0", "--port", "8000"]
