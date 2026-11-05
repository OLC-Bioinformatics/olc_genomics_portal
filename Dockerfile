# filepath: /home/cfiaadmin/FoodPort/olc_genomics_portal/Dockerfile
FROM ubuntu:26.04

# Initialize
RUN mkdir -p /data/web
WORKDIR /data/web

# Setup
RUN apt-get update --fix-missing && \
    apt-get clean && \
    apt-get autoclean && \
    apt-get install -y python3 python3-pip python3-dev python3-venv postgresql-client postgresql-server-dev-all gettext ncbi-blast+ xvfb docker.io firefox xvfb

# Create and use virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements/base.txt /data/web/

RUN pip config set global.trusted-host "pypi.org pypi.python.org files.pythonhosted.org"
RUN pip install --upgrade pip
RUN pip install -r base.txt --ignore-installed

# Prepare
COPY . /data/web/