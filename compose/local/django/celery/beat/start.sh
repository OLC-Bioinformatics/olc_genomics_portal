#!/usr/bin/env bash

set -o errexit
set -o pipefail
set -o nounset
set -o xtrace


rm -f './celerybeat.pid'
celery -A olc_webportalv2.taskapp beat -l INFO
