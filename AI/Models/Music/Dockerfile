FROM pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade --break-system-packages pip setuptools wheel \
    && pip install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py

EXPOSE 8000

ENTRYPOINT ["python", "/app/app.py"]
CMD []
