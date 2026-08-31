#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.dump>"
    echo ""
    echo "Available backups:"
    ls -lh backups/ 2>/dev/null || echo "  (no backups found)"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "${BACKUP_FILE}" ]; then
    echo "Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "Restoring database from: ${BACKUP_FILE}..."
docker compose cp "${BACKUP_FILE}" db:/tmp/odoo_restore.dump
docker compose exec db dropdb -U "${POSTGRES_USER}" --if-exists "${POSTGRES_DB}"
docker compose exec db createdb -U "${POSTGRES_USER}" -O "${POSTGRES_USER}" "${POSTGRES_DB}"
docker compose exec db pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -F c --no-owner --no-privileges /tmp/odoo_restore.dump
docker compose exec db rm /tmp/odoo_restore.dump
echo "Database restored. Restarting Odoo..."
docker compose restart odoo
echo "Restore complete."
