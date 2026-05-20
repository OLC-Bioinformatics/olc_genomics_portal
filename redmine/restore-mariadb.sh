#!/usr/bin/env bash
set -euo pipefail

# Restore a Redmine MariaDB backup into the running mariadb service.
# Example: ./redmine/restore-mariadb.sh backups/mariadb-redmine-2026_05_11T12_00_00.sql.gz

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <backup-file>"
  exit 1
fi

BACKUP_FILE="$1"
if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE"
  exit 1
fi

DB_NAME="redmine"

echo "Restoring Redmine MariaDB from $BACKUP_FILE"

docker compose exec -T mariadb sh -c "exec mysql -u root -p\"\$MYSQL_ROOT_PASSWORD\" -e \"DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME;\""

gunzip -c "$BACKUP_FILE" | docker compose exec -T mariadb sh -c "exec mysql -u root -p\"\$MYSQL_ROOT_PASSWORD\" $DB_NAME"

echo "Restore complete."
