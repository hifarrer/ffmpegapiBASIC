# Use Python 3.13 to match Railway default; install ffmpeg and Node.js for Remotion TikTok captions
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Node deps for Remotion (npx/tsx used by add-tiktok-captions and add-tiktok-subtitles)
COPY package.json ./
RUN npm install

COPY . .

ENV PORT=5000
EXPOSE $PORT

CMD gunicorn -b 0.0.0.0:${PORT} main:app --timeout 1800 --graceful-timeout 1800
