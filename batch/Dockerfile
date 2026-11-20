# syntax=docker/dockerfile:1
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    MAMBA_ROOT_PREFIX=/opt/conda \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl bzip2 bash tini && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=.,target=/build-context,ro \
    if [ -f /build-context/GoC-GdC-Root-A.crt ]; then \
        cp /build-context/GoC-GdC-Root-A.crt \
            /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
        update-ca-certificates; \
    fi

ARG MICROMAMBA_VERSION=2.10.0
RUN curl --fail --show-error --location --retry 3 --retry-all-errors \
    --output /tmp/micromamba.tar.bz2 \
    "https://micro.mamba.pm/api/micromamba/linux-64/${MICROMAMBA_VERSION}" && \
    tar -xjf /tmp/micromamba.tar.bz2 -C /usr/local/bin --strip-components=1 bin/micromamba && \
    rm /tmp/micromamba.tar.bz2

# Configure micromamba channels without enabling default channels.
RUN micromamba config remove-key channels --system || true && \
    micromamba config remove-key default_channels --system || true && \
    micromamba config set channel_alias https://conda.anaconda.org --system && \
    micromamba config set channel_priority strict --system && \
    micromamba config append channels conda-forge --system && \
    micromamba config append channels bioconda --system && \
    micromamba config append channels olcbioinformatics --system

# Create an isolated service environment without the legacy AzureBatch wrapper.
RUN micromamba create -y -n batch \
    -c conda-forge -c bioconda -c olcbioinformatics \
    --strict-channel-priority \
    python=3.12 \
    gunicorn \
    flask \
    pip && \
    micromamba clean -a -y

# Use env without activation
ENV CONDA_DEFAULT_ENV=batch
ENV PATH=/opt/conda/envs/batch/bin:$PATH

COPY batch/requirements.txt /opt/batch-requirements.txt
RUN python -m pip install --require-hashes --no-cache-dir -r /opt/batch-requirements.txt

WORKDIR /app

COPY batch/api.py batch/provider.py /app/

# Entry script
COPY batch/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

EXPOSE 5000
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/usr/local/bin/start.sh"]