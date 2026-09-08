ARG NODE_IMAGE=node:22.21.1
ARG PYTHON_IMAGE=python:3.10.19

FROM ${NODE_IMAGE} AS builder

WORKDIR /app

RUN npm config set registry https://registry.npmmirror.com

COPY sau_frontend/package.json sau_frontend/package-lock.json ./

RUN npm ci

COPY sau_frontend .

ENV NODE_ENV=production
ENV PATH=/app/node_modules/.bin:$PATH

RUN npm run build


FROM ${PYTHON_IMAGE}

ARG PATCHRIGHT_CHROMIUM_REVISION=1208
ARG PATCHRIGHT_CHROMIUM_URL=https://cdn.npmmirror.com/binaries/chrome-for-testing/145.0.7632.6/linux64/chrome-linux64.zip
ARG PATCHRIGHT_CHROMIUM_SHA256=b5e3195041af345a668d110f5daf5581961fa3608626ea588c97dd0fe81c4e38

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
ENV LOCAL_CHROME_PATH=/opt/playwright/chromium-${PATCHRIGHT_CHROMIUM_REVISION}/chrome-linux64/chrome
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FRONTEND_BUILD_DIR=/app

RUN for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$source_file" ]; then \
            sed -i \
                -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                -e 's|https://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
                "$source_file"; \
        fi; \
    done \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends libnss3 \
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

# Patchright's upstream browser CDN can stall on mainland hosts. The production
# stack uses the server-verified Chrome-for-Testing revision from a configurable
# mirror and proves it can launch before the image is accepted.
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends curl fonts-noto-cjk libcups2 unzip \
    && rm -rf /var/lib/apt/lists/*

RUN fc-match 'Noto Sans CJK SC' | grep -q 'NotoSansCJK'

RUN mkdir -p "/opt/playwright/chromium-${PATCHRIGHT_CHROMIUM_REVISION}" /opt/google/chrome \
    && curl --fail --location --retry 5 --retry-all-errors \
        --connect-timeout 20 --max-time 900 \
        --output /tmp/patchright-chromium.zip \
        "${PATCHRIGHT_CHROMIUM_URL}" \
    && echo "${PATCHRIGHT_CHROMIUM_SHA256}  /tmp/patchright-chromium.zip" | sha256sum -c - \
    && unzip -q /tmp/patchright-chromium.zip -d "/opt/playwright/chromium-${PATCHRIGHT_CHROMIUM_REVISION}" \
    && test -x "${LOCAL_CHROME_PATH}" \
    && touch "/opt/playwright/chromium-${PATCHRIGHT_CHROMIUM_REVISION}/INSTALLATION_COMPLETE" \
    && ln -sf "${LOCAL_CHROME_PATH}" /opt/google/chrome/chrome \
    && rm -f /tmp/patchright-chromium.zip

COPY --from=builder /app/dist/index.html /app
COPY --from=builder /app/dist/assets /app/assets
COPY --from=builder /app/dist/geo-favicon.svg /app/geo-favicon.svg
COPY --from=builder /app/dist/vite.svg /app/assets

# Runtime data on the verified host is owned by 1000:1000. Match that identity
# in the image and keep the application source and browser installation
# read-only to the serving process.
RUN groupadd --gid 1000 geo \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin geo \
    && install -d -o geo -g geo -m 0750 \
        /app/data \
        /app/cookies \
        /app/cookiesFile \
        /app/logs \
        /app/uploadFile \
        /app/videoFile \
        /home/geo/.cache \
    && chmod -R a+rX /opt/playwright /opt/google/chrome

ENV HOME=/home/geo

USER geo

# Exercise the same identity and executable paths used at runtime. This catches
# an unreadable Chrome installation or a missing production WSGI entry point at
# image-build time.
RUN command -v sau-web >/dev/null \
    && DATABASE_PATH=/tmp/geo-build-smoke.db COOKIES_DIRECTORY=/tmp/geo-build-cookies MEDIA_ROOT=/tmp/geo-build-media DEMO_MODE=false SEED_DEMO_DATA=false python -c "from pathlib import Path; import sau_backend; from sau_web import build_waitress_options; import waitress; assert build_waitress_options({})['port'] == 5409; assert Path(sau_backend.app.config['FRONTEND_BUILD_DIR'], 'index.html').is_file()" \
    && python -c "import os; from patchright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True, executable_path=os.environ['LOCAL_CHROME_PATH']); b.close(); p.stop()"

EXPOSE 5409

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5409/api/health', timeout=3).read()" || exit 1

CMD ["python", "-m", "sau_web"]
