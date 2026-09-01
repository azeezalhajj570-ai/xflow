#!/usr/bin/env bash
# Run Odoo tests for one or more modules in a fresh, disposable database.
#
# Usage:
#   ./scripts/run-tests.sh ai_whatsapp
#   ./scripts/run-tests.sh ai_whatsapp --test-tags /ai_whatsapp
#
# The script will:
#   1. Read environment from .env.
#   2. Drop and recreate the test database (default: test_<module>).
#   3. Run Odoo in single-process mode (--workers=0) with HTTP disabled.
#   4. Stream the test log to stdout and write it to logs/test_<module>.log.

set -euo pipefail

cd "$(dirname "$0")/.."
source .env

MODULE="${1:-}"
if [ -z "${MODULE}" ]; then
    echo "Usage: $0 <module_name> [extra odoo options]"
    exit 1
fi
shift || true

TEST_DB="test_${MODULE}"
LOG_FILE="logs/test_${MODULE}.log"

echo "=== Dropping and recreating test database '${TEST_DB}' ==="
docker compose exec db psql -U "${POSTGRES_USER}" -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};"
docker compose exec db psql -U "${POSTGRES_USER}" -d postgres -c "CREATE DATABASE ${TEST_DB} OWNER ${POSTGRES_USER};"

mkdir -p logs

echo "=== Running tests for module '${MODULE}' ==="
# Odoo 19 uses sub-commands: server options must follow 'odoo server'.
# Use the container entrypoint so DB credentials are injected correctly.
# --workers=0 is required for tests and keeps memory usage low.
# --http-port=18069 avoids binding to the dev server's port 8069.
#
# Two phases: -i installs the module (runs at_install tests), then -u runs the
# post_install tests. Suites tagged `post_install` / `-at_install` only run in
# the update phase, and the tag spec must include `standard,post_install`.
docker compose exec -T odoo /entrypoint.sh odoo server \
    -d "${TEST_DB}" \
    -i "${MODULE}" \
    --stop-after-init \
    --workers=0 \
    --http-port=18069 \
    --logfile="/var/log/odoo/test_${MODULE}.log" \
    "$@"

docker compose exec -T odoo /entrypoint.sh odoo server \
    -d "${TEST_DB}" \
    -u "${MODULE}" \
    --test-tags "standard,post_install,/${MODULE}" \
    --stop-after-init \
    --workers=0 \
    --http-port=18069 \
    --logfile="/var/log/odoo/test_${MODULE}.log" \
    --log-handler=odoo.addons.${MODULE}:DEBUG \
    "$@"

# Copy the log from the container to the host so it is easy to inspect.
docker compose cp "odoo:/var/log/odoo/test_${MODULE}.log" "${LOG_FILE}" 2>/dev/null || true

echo "=== Test log saved to ${LOG_FILE} ==="
