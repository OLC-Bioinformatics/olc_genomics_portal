#!/usr/bin/env bash
set -euo pipefail

# Create a compressed dump of the Redmine MariaDB database via the mariadb container.
# Example: ./redmine/backup-mariadb.sh
# Use BACKUP_DIR or RETENTION_DAYS to override defaults.

ROOT=$(cd "$(dirname "$0")" && pwd)
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-21}"
DB_NAME="redmine"

mkdir -p "$BACKUP_DIR"

FILENAME="mariadb-redmine-$(date +'%Y_%m_%dT%H_%M_%S').sql.gz"
BACKUP_PATH="$BACKUP_DIR/$FILENAME"

echo "Creating Redmine MariaDB backup: $BACKUP_PATH"

docker compose exec -T mariadb sh -c "exec mysqldump -u root -p\"\$MYSQL_ROOT_PASSWORD\" $DB_NAME" | gzip > "$BACKUP_PATH"

echo "Backup complete: $BACKUP_PATH"

echo "Removing backups older than $RETENTION_DAYS days from $BACKUP_DIR"
find "$BACKUP_DIR" -type f -name 'mariadb-redmine-*.sql.gz' -mtime +"$RETENTION_DAYS" -print -delete

echo "Done."
