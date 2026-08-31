# =============================================================================
# Odoo 19 Development - Makefile
# =============================================================================
# Usage: make <target>
#   up         Start all services
#   down       Stop all services
#   restart    Restart all services
#   logs       Follow logs
#   shell      Open shell in Odoo container
#   psql       Open psql in database container
#   backup     Backup the database
#   restore    Restore the database
#   update     Update modules
#   dev        Run Odoo in development mode (hot reload)
#   init       Initialize database with base modules
#   clean      Clean up containers, volumes, and data
# =============================================================================

-include .env
export $(shell sed 's/=.*//' .env)

DOCKER_COMPOSE = docker compose
SERVICE_ODOO = odoo
SERVICE_DB = db
BACKUP_DIR = backups

.PHONY: up down restart logs shell psql backup restore update init dev clean

up:
	$(DOCKER_COMPOSE) up -d --remove-orphans
	@echo "Odoo 19 is starting. Check 'make logs' for progress."
	@echo "Access Odoo at http://localhost:${ODOO_PORT}"

down:
	$(DOCKER_COMPOSE) down

restart:
	$(DOCKER_COMPOSE) restart

logs:
	$(DOCKER_COMPOSE) logs -f

shell:
	$(DOCKER_COMPOSE) exec $(SERVICE_ODOO) bash

psql:
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

backup:
	@echo "Creating database backup..."
	@mkdir -p $(BACKUP_DIR)
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) pg_dump -U $(POSTGRES_USER) -d $(POSTGRES_DB) -F c -f /tmp/odoo_backup.dump
	$(DOCKER_COMPOSE) cp $(SERVICE_DB):/tmp/odoo_backup.dump ./$(BACKUP_DIR)/odoo_$$(date +%Y%m%d_%H%M%S).dump
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) rm /tmp/odoo_backup.dump
	@echo "Backup created in $(BACKUP_DIR)/"

restore:
	@echo "Restoring database from backup..."
	@if [ -z "$(file)" ]; then \
		echo "Usage: make restore file=path/to/backup.dump"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) cp $(file) $(SERVICE_DB):/tmp/odoo_restore.dump
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) dropdb -U $(POSTGRES_USER) --if-exists $(POSTGRES_DB)
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) createdb -U $(POSTGRES_USER) -O $(POSTGRES_USER) $(POSTGRES_DB)
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) pg_restore -U $(POSTGRES_USER) -d $(POSTGRES_DB) -F c --no-owner --no-privileges /tmp/odoo_restore.dump
	$(DOCKER_COMPOSE) exec $(SERVICE_DB) rm /tmp/odoo_restore.dump
	@echo "Restore complete. Restarting Odoo..."
	$(DOCKER_COMPOSE) restart $(SERVICE_ODOO)

update:
	@if [ -z "$(m)" ]; then \
		echo "Usage: make update m=module_name"; \
		echo "   or: make update m=all"; \
		exit 1; \
	fi
	@echo "Stopping Odoo to update module(s): $(m)..."
	$(DOCKER_COMPOSE) stop $(SERVICE_ODOO)
	$(DOCKER_COMPOSE) run --rm --entrypoint "/entrypoint.sh" $(SERVICE_ODOO) odoo -d $(ODOO_DATABASE) -u $(m) --stop-after-init
	$(DOCKER_COMPOSE) start $(SERVICE_ODOO)
	@echo "Module(s) '$(m)' updated."

init:
	@echo "Initializing Odoo database with base modules..."
	$(DOCKER_COMPOSE) stop $(SERVICE_ODOO)
	$(DOCKER_COMPOSE) exec -T $(SERVICE_DB) psql -U $(POSTGRES_USER) -d postgres -c "DROP DATABASE IF EXISTS $(POSTGRES_DB);" 2>&1 || true
	$(DOCKER_COMPOSE) exec -T $(SERVICE_DB) psql -U $(POSTGRES_USER) -d postgres -c "CREATE DATABASE $(POSTGRES_DB) OWNER $(POSTGRES_USER);"
	$(DOCKER_COMPOSE) run --rm --entrypoint "/entrypoint.sh" $(SERVICE_ODOO) odoo -d $(ODOO_DATABASE) -i base --stop-after-init
	$(DOCKER_COMPOSE) start $(SERVICE_ODOO)
	@echo "Database initialized. Access Odoo at http://localhost:${ODOO_PORT}"

dev:
	@echo "Starting Odoo in development mode (hot reload enabled)..."
	$(DOCKER_COMPOSE) stop $(SERVICE_ODOO)
	$(DOCKER_COMPOSE) run --rm --service-ports --entrypoint "/entrypoint.sh" $(SERVICE_ODOO) odoo -d $(ODOO_DATABASE) --dev=all
	$(DOCKER_COMPOSE) start $(SERVICE_ODOO)

clean:
	@echo "WARNING: This will remove all containers, volumes, and data."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds to proceed..."
	@sleep 5
	$(DOCKER_COMPOSE) down -v --remove-orphans
	@rm -rf data/postgres/* data/filestore/* logs/* addons/custom/test_module addons/enterprise/test_enterprise
	@echo "Clean complete."
