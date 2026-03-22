FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
    
ARG DEBIAN_FRONTEND=noninteractive

RUN python -m venv "$VIRTUAL_ENV" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py
COPY config.yml* /app/

RUN mkdir -p /app/Logs

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
