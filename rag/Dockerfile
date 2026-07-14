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

# Add the Government of Canada root CA to the system trust store.
# This allows pip and other HTTPS clients to validate connections passing
# through the corporate TLS-intercepting proxy.
COPY GoC-GdC-Root-A.crt \
    /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt

RUN update-ca-certificates

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

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

