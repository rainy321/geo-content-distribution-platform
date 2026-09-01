FROM node:22.21.1 AS builder

WORKDIR /app

RUN npm config set registry https://registry.npmmirror.com

COPY sau_frontend/package.json sau_frontend/package-lock.json ./

RUN npm ci

COPY sau_frontend .

ENV NODE_ENV=production
ENV PATH=/app/node_modules/.bin:$PATH

RUN npm run build


FROM python:3.10.19

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

RUN apt-get update && apt-get install -y --no-install-recommends libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libxkbcommon0 \
    libasound2 && rm -rf /var/lib/apt/lists/*

RUN pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

COPY . .

RUN pip install ".[web]"

RUN playwright install chromium
RUN patchright install chromium

RUN mkdir -p /app/videoFile /app/cookiesFile /app/data

COPY --from=builder /app/dist/index.html /app
COPY --from=builder /app/dist/assets /app/assets
COPY --from=builder /app/dist/vite.svg /app/assets

EXPOSE 5409

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5409/api/health', timeout=3).read()" || exit 1

CMD ["python", "sau_backend.py"]
