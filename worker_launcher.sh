#!/bin/bash
celery worker -E -l info -A olc_webportalv2.taskapp -n workerD -Q default,primer_finder -c 1 &
celery worker -E -l info -A olc_webportalv2.taskapp -n workerC -Q cowbat -c 5 &
celery worker -E -l info -A olc_webportalv2.taskapp -n workerG -Q geneseekr -c 1
