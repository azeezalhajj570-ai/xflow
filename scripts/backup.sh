#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

BACKUP_FILE="backups/odoo_${POSTGRES_DB}_$(date +%Y%m%d_%H%M%S).dump"
mkdir -p backups

echo "Creating backup: ${BACKUP_FILE}..."
docker compose exec db pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -F c -f /tmp/odoo_backup.dump
docker compose cp db:/tmp/odoo_backup.dump "./${BACKUP_FILE}"
docker compose exec db rm /tmp/odoo_backup.dump
echo "Backup saved to: ${BACKUP_FILE}"
