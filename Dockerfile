FROM ubuntu:16.04

# Initialize
RUN mkdir -p /data/web
WORKDIR /data/web

# Setup
RUN apt-get update
RUN apt-get install -y python3 python3-dev postgresql-client postgresql-server-dev-all gettext ncbi-blast+
RUN apt-get install -y python3-pip
RUN pip3 install --upgrade pip
COPY requirements/base.txt /data/web/
RUN pip3 install -r base.txt

# Prepare
COPY . /data/web/

RUN apt-get update
RUN apt-get install -y samtools firefox xvfb
# Need this for GeneSeekr to work.
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

