#!/usr/bin/env bash

set -o errexit
set -o pipefail
set -o nounset
set -o xtrace

# This only works if the docker group does not already exist

DOCKER_SOCKET=/var/run/docker.sock
DOCKER_GROUP=docker
REGULAR_USER=ubuntu

if [ -S ${DOCKER_SOCKET} ]; then
    DOCKER_GID=$(stat -c '%g' ${DOCKER_SOCKET})
    groupadd -for -g ${DOCKER_GID} ${DOCKER_GROUP}
    usermod -aG ${DOCKER_GROUP} ${REGULAR_USER}
fi

# Change to regular user and run the rest of the entry point
su ${REGULAR_USER} -c "/usr/bin/env bash runner.sh ${@}"


python manage.py migrate
python manage.py runserver_plus 0.0.0.0:8000
# Necessary to handle jobs
python manage.py process_tasks
