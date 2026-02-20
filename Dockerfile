# Use Python 3.13 to match Railway default; install ffmpeg (includes ffprobe) for audio/video endpoints
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5000
EXPOSE $PORT

CMD gunicorn -b 0.0.0.0:${PORT} main:app --timeout 1800 --graceful-timeout 1800
