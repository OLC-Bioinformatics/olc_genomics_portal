# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_CACHE_DIR=/models \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libpq5 \
    tini \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=.,target=/build-context,ro \
    if [ -f /build-context/GoC-GdC-Root-A.crt ]; then \
        cp /build-context/GoC-GdC-Root-A.crt \
            /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
        update-ca-certificates; \
    fi

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --require-hashes \
    --no-cache-dir \
    -r /app/requirements.txt

RUN useradd \
    --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    raguser \
    && mkdir -p /models \
    && chown -R raguser:raguser /app /models

COPY --chown=raguser:raguser . /app

USER raguser

EXPOSE 8001

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/start.sh"]

