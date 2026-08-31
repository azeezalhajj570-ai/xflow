#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

if [ $# -lt 1 ]; then
    echo "Usage: $0 <module_name>"
    echo "   or: $0 all"
    echo ""
    echo "Installed modules:"
    docker compose exec odoo odoo -d "${ODOO_DATABASE}" --stop-after-init --i18n-export=/dev/null 2>/dev/null || true
    echo ""
    echo "To list all available modules, use 'all' or specify a module name."
    exit 1
fi

MODULE="$1"
echo "Updating module(s): ${MODULE}..."
docker compose exec odoo odoo -d "${ODOO_DATABASE}" -u "${MODULE}" --stop-after-init
docker compose restart odoo
echo "Module(s) '${MODULE}' updated and Odoo restarted."
