# syntax=docker/dockerfile:1
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=.,target=/build-context,ro \
    if [ -f /build-context/GoC-GdC-Root-A.crt ]; then \
        cp /build-context/GoC-GdC-Root-A.crt \
            /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
        update-ca-certificates; \
    fi

COPY storage/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install --require-hashes -r /app/requirements.txt

COPY storage/provider.py storage/api.py ./

EXPOSE 5001
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5001", "api:app"]
