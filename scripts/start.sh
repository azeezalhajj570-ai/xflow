#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose up -d --remove-orphans
echo "Odoo 19 started. Access at http://localhost:${ODOO_PORT:-8069}"
