# filepath: /home/cfiaadmin/FoodPort/olc_genomics_portal/Dockerfile
FROM ubuntu:26.04 AS runtime-base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        postgresql-client \
        ncbi-blast+ \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt
RUN update-ca-certificates

ENV PATH="/venv/bin:$PATH" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

WORKDIR /data/web

FROM runtime-base AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        python3-dev \
        python3-venv && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv

COPY requirements/base.txt /data/web/

RUN python -m pip install --upgrade pip && \
    python -m pip install --require-hashes --no-cache-dir -r /data/web/base.txt

FROM builder AS test

RUN apt-get update && \
    apt-get install -y --no-install-recommends gettext && \
    rm -rf /var/lib/apt/lists/*

COPY requirements/test.txt /data/web/requirements/test.txt

RUN python -m pip install --require-hashes --no-cache-dir -r /data/web/requirements/test.txt

COPY . /data/web/

FROM runtime-base AS production

COPY --from=builder /venv /venv
COPY . /data/web/
RUN chmod -R a+rX /data/web

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system --gid 10001 foodport && \
    useradd --uid 10001 --gid foodport \
        --create-home --home-dir /home/foodport \
        --shell /usr/sbin/nologin foodport && \
    mkdir -p \
        /data/web/staticfiles \
        /data/web/olc_webportalv2/media \
        /sequences && \
    chown -R foodport:foodport \
        /data/web/staticfiles \
        /data/web/olc_webportalv2/media \
        /sequences

USER foodport:foodport