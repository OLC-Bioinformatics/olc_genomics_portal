# filepath: /home/cfiaadmin/FoodPort/olc_genomics_portal/Dockerfile
FROM ubuntu:26.04 AS portal-base

# Initialize
RUN mkdir -p /data/web
WORKDIR /data/web

# Setup
RUN apt-get update --fix-missing && \
    apt-get clean && \
    apt-get autoclean && \
    apt-get install -y \
        ca-certificates \
        python3 \
        python3-pip \
        python3-dev \
        python3-venv \
        postgresql-client \
        postgresql-server-dev-all \
        gettext \
        ncbi-blast+ \
        xvfb \
        docker.io \
        firefox \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        fonts-dejavu-core

# Trust the Dev VM's corporate TLS-inspection CA for Sentry and other HTTPS clients.
COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt
RUN update-ca-certificates

# Create and use virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

COPY requirements/base.txt /data/web/

RUN pip config set global.trusted-host "pypi.org pypi.python.org files.pythonhosted.org"
RUN pip install --upgrade pip
RUN pip install -r base.txt --ignore-installed

# Prepare
COPY . /data/web/

FROM portal-base AS test

RUN pip install --no-cache-dir -r /data/web/requirements/test.txt

FROM portal-base AS production