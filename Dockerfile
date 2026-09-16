FROM python:3.14-slim

ARG VERSION=development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_VERSION=${VERSION}

LABEL org.opencontainers.image.title="ListenBrainz Telegram Downloader" \
      org.opencontainers.image.description="Download authorized Telegram audio from ListenBrainz history and Saved Messages" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app ./app

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data /downloads \
    && chown -R app:app /app /data /downloads

USER app

VOLUME ["/data", "/downloads"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "app.cli", "healthcheck"]
ENTRYPOINT ["python", "-m", "app.cli"]
CMD ["run"]
