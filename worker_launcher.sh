#!/bin/bash
celery -A olc_webportalv2.taskapp worker -E -l info -n workerD -Q default -c 1 &
celery -A olc_webportalv2.taskapp worker -E -l info -n workerC -Q cowbat -c 5 &
celery -A olc_webportalv2.taskapp worker -E -l info -n workerG -Q geneseekr -c 1
