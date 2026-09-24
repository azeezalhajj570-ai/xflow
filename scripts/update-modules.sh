#!/usr/bin/env bash
# Update one or more Odoo modules on the development database.
#
# Usage:
#   ./scripts/update-modules.sh x_account x_account_twitter
#   ./scripts/update-modules.sh x_account,x_account_getxapi,x_account_twitter
#   ODOO_DB=other_db ./scripts/update-modules.sh x_account
#
# The script will:
#   1. Read environment from .env (database name, credentials).
#   2. Stop the dev server container: the update needs the database to itself,
#      and two Odoo processes would also fight over the filestore.
#   3. Run the update in a one-off container ("docker compose run --rm") with
#      HTTP moved off the dev port and workers disabled.
#   4. Start the dev server again, whatever the update did.
#
# View changes only take effect through this update: the arch of inherited
# views is merged at module update time, so a broken xpath or an unknown field
# shows up here and nowhere else.

set -euo pipefail

cd "$(dirname "$0")/.."
source .env

# ODOO_DATABASE comes from .env; ODOO_DB is an optional one-run override.
DB="${ODOO_DB:-${ODOO_DATABASE}}"
HTTP_PORT="${ODOO_UPDATE_PORT:-18069}"

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <module> [module ...]"
    echo "       $0 module_a,module_b,module_c"
    echo
    echo "Modules installed on ${DB} can be listed with:"
    echo "  docker exec ${POSTGRES_HOST} psql -U ${POSTGRES_USER} -d ${DB} \\"
    echo "      -c \"select name from ir_module_module where state = 'installed' order by name\""
    exit 1
fi

# Accept both "a b c" and "a,b,c": join the arguments with commas, then drop
# the spaces of an already comma-separated list.
MODULES="$(IFS=,; echo "$*")"
MODULES="${MODULES// /}"
MODULES="${MODULES//,,/,}"
MODULES="${MODULES%,}"

if [ -z "${MODULES}" ] || [ "${MODULES}" = "," ]; then
    echo "No module name given." >&2
    exit 1
fi

LOG_FILE="logs/update_${MODULES//,/_}.log"
mkdir -p logs

echo "=== Stopping the dev server (service: odoo) ==="
# Only the Odoo service: PostgreSQL runs in the external madarbot stack and
# must stay up.  --rm below leaves no stopped container behind.
docker compose stop odoo

# Bring the dev server back on every exit path — failure, SIGINT, or success.
trap 'echo "=== Starting the dev server again ==="; docker compose start odoo' EXIT

echo "=== Updating '${MODULES}' on database '${DB}' ==="
# Odoo 19 uses sub-commands, so server options must follow 'odoo server'.
# Use the container entrypoint so DB credentials are injected correctly.
# --workers=0 keeps the run single-process (and its memory use low), and
# --http-port keeps it away from the dev server's 8069.
docker compose run --rm -T odoo /entrypoint.sh odoo server \
    -d "${DB}" \
    -u "${MODULES}" \
    --stop-after-init \
    --workers=0 \
    --http-port="${HTTP_PORT}" 2>&1 | tee "${LOG_FILE}"

echo "=== Update log saved to ${LOG_FILE} ==="
