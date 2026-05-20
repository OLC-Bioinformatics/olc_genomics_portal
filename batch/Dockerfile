FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive \
    MAMBA_ROOT_PREFIX=/opt/conda \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Base tools (no update-ca-certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl bzip2 bash tini && \
    rm -rf /var/lib/apt/lists/*

# Install micromamba (no Anaconda defaults; no ToS)
RUN curl --insecure -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | \
    tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba

# Configure micromamba: disable SSL verify and set channels (no defaults)
RUN micromamba config remove-key channels --system || true && \
    micromamba config remove-key default_channels --system || true && \
    micromamba config set channel_alias https://conda.anaconda.org --system && \
    micromamba config set channel_priority strict --system && \
    micromamba config set ssl_verify false --system && \
    micromamba config append channels conda-forge --system && \
    micromamba config append channels bioconda --system && \
    micromamba config append channels olcbioinformatics --system && \
    micromamba config list

# Create isolated env with Python 3.12 and deps (tqdm pinned)
RUN micromamba create -y -n batch \
    -c conda-forge -c bioconda -c olcbioinformatics \
    --strict-channel-priority \
    python=3.12 \
    gunicorn \
    flask \
    azure_batch==0.0.13 && \
    micromamba clean -a -y

# Use env without activation
ENV CONDA_DEFAULT_ENV=batch
ENV PATH=/opt/conda/envs/batch/bin:$PATH

WORKDIR /app

# Entry script
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

EXPOSE 5000
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/usr/local/bin/start.sh"]