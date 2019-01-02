#!/usr/bin/env bash

set -o errexit
set -o pipefail
set -o nounset
set -o xtrace


celery -A olc_webportalv2.taskapp worker -l INFO
