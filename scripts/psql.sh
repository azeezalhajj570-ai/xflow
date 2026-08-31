#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .env
docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"
