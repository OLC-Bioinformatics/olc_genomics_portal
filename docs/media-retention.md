# Media retention

FoodPort retains per-run local media for 30 days. The cleaner considers only
files older than the configured retention period in run directories associated
with known analysis records. Shared/reference assets, COWSNPhR archives,
symlinks, and unmapped directories are left alone.

Applying the cleanup deletes eligible local files directly. It does not check,
upload, or change blob objects, and it never removes blob containers. Files
that are missing from blob storage or differ from their blob copy are still
deleted locally. Analysis history records are retained.

The scheduled local cleanup task defaults to dry-run mode. To deploy the
updated web and Celery code:

```sh
docker compose up -d --build --force-recreate web celeryworker celerybeat
```

To review a file-level dry-run report:

```sh
mkdir -p /home/cfiaadmin/foodport-retention-reports
docker compose exec web python manage.py clean_media \
  --report-csv /tmp/foodport-media-retention.csv
docker compose cp web:/tmp/foodport-media-retention.csv \
  /home/cfiaadmin/foodport-retention-reports/media-retention-dry-run.csv
```

The report lists each eligible file as `would_delete`. Inaccessible files are
retained and reported. The report path passed to `clean_media` is inside the
container, so copy it to a host directory to review it outside the container.

To apply a one-time local cleanup after reviewing the report:

```sh
docker compose exec web python manage.py clean_media --apply \
  --report-csv /tmp/foodport-media-retention-applied.csv
docker compose cp web:/tmp/foodport-media-retention-applied.csv \
  /home/cfiaadmin/foodport-retention-reports/media-retention-applied.csv
```

To enable scheduled deletion, set `MEDIA_RETENTION_DRY_RUN=false` in the
deployment environment and recreate the web, worker, and beat services. The
default is `true`; `MEDIA_RETENTION_DAYS` defaults to 30. Blob-container
cleanup is disabled.
