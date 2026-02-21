# Use Python 3.13 to match Railway default; install ffmpeg, Node.js, and Chrome deps for Remotion
FROM python:3.13-slim

# System deps: ffmpeg, Node.js, and libraries Chrome Headless Shell needs to run
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg nodejs npm \
       libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
       libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
       libxshmfence1 libx11-xcb1 libxcb1 libxext6 libx11-6 libdbus-1-3 \
       fonts-noto-color-emoji fonts-freefont-ttf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Node deps and pre-download Chrome Headless Shell at build time
COPY package.json ./
RUN npm install \
    && npx remotion browser ensure

COPY . .

ENV PORT=5000
EXPOSE $PORT

CMD gunicorn -b 0.0.0.0:${PORT} main:app --timeout 1800 --graceful-timeout 1800
