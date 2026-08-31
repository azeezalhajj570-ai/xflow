# Odoo 19 Enterprise - Docker Development Environment

Production-ready Docker-based development environment for Odoo 19 Enterprise.

## Architecture

```
project/
├── docker-compose.yml    # Service orchestration
├── .env                  # Configuration (DO NOT COMMIT)
├── .gitignore
├── Makefile              # Common commands
├── README.md
├── config/
│   └── odoo.conf         # Odoo configuration file
├── addons/
│   ├── custom/           # Your custom modules
│   ├── third_party/      # Third-party community modules
│   └── enterprise/       # Enterprise modules (mounted if available)
├── data/
│   ├── postgres/         # PostgreSQL data (persistent)
│   └── filestore/        # Odoo filestore (persistent)
├── logs/                 # Odoo logs
├── backups/              # Database backups
└── scripts/              # Helper scripts
    ├── start.sh
    ├── stop.sh
    ├── restart.sh
    ├── logs.sh
    ├── shell.sh
    ├── psql.sh
    ├── backup.sh
    ├── restore.sh
    └── update.sh
```

## Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- At least 4 GB RAM allocated to Docker
- 10 GB free disk space

## Quick Start

```bash
# Clone this project
cd odoo19-dev

# Customize environment (optional)
cp .env.example .env
# Edit .env to set your passwords and preferences

# Start the stack
make up
# or: docker compose up -d

# Check logs
make logs
# or: docker compose logs -f

# Access Odoo
# http://localhost:8069
```

## Creating a Database

1. Access http://localhost:8069
2. Fill in the database creation form:
   - **Database Name**: `odoo` (or your preference)
   - **Password**: Use the admin password from `.env` (default: `admin`)
   - **Email**: Your email
   - **Language**: Select your language
   - **Country**: Select your country
3. Click **Create Database**
4. Select modules to install

> **For Enterprise modules**: You need valid Odoo Enterprise credentials or an enterprise addons mounted at `addons/enterprise/`.

## Installing Enterprise Modules

Enterprise modules are loaded from the `addons/enterprise/` directory. If you have a valid Odoo Enterprise subscription:

1. Download the enterprise addons from your Odoo subscription
2. Place them in `addons/enterprise/`
3. Restart Odoo: `make restart`

The addons path is already configured to include the enterprise directory.

## Developing Custom Modules

### Creating a New Module

```bash
# Enter the Odoo container
make shell

# Use the Odoo scaffolding tool
odoo scaffold my_module /mnt/custom-addons

# Exit the container
exit

# Restart Odoo to pick up the new module
make restart
```

Alternatively, create the module directly on your host:

```bash
# On host machine
mkdir -p addons/custom/my_module
mkdir -p addons/custom/my_module/{models,views,security,data,static/description}
touch addons/custom/my_module/__init__.py
touch addons/custom/my_module/__manifest__.py
```

### Hot Reload

Odoo 19 supports Python code reloading in development mode. To enable:

```bash
# Start Odoo with dev mode
docker compose exec odoo odoo -d odoo --dev=all
```

Alternatively, restart Odoo after code changes:

```bash
make restart
```

### Debugging

For Python debugging with pdb:

```bash
# Add this to your code where you want to break:
import pdb; pdb.set_trace()

# Run Odoo in interactive mode:
docker compose exec odoo odoo shell -d odoo
```

For remote debugging with debugpy:

```bash
# Install debugpy (inside container)
pip install debugpy

# Start Odoo with debugger
docker compose exec odoo python -m debugpy --listen 0.0.0.0:5678 --wait-for-client /usr/bin/odoo -d odoo
```

## Installing Third-Party Modules

1. Find the module from Odoo Apps Store or GitHub
2. Place it in `addons/third_party/`:

```bash
cd addons/third_party
git clone https://github.com/OCA/server-tools.git
# Or download and extract the module directory
```

3. Restart Odoo:
```bash
make restart
```

4. Install the module via Apps menu (activate Developer Mode first)

## Updating Modules

```bash
# Update a specific module
make update m=module_name

# Update all modules
make update m=all

# Or directly:
docker compose exec odoo odoo -d odoo -u module_name --stop-after-init
```

## Database Backup and Restore

### Backup

```bash
# Using Makefile
make backup

# Using script
./scripts/backup.sh

# Manual
docker compose exec db pg_dump -U odoo -d odoo -F c -f /tmp/backup.dump
docker compose cp db:/tmp/backup.dump ./backups/
```

### Restore

```bash
# Using Makefile
make restore file=backups/odoo_20240101_120000.dump

# Using script
./scripts/restore.sh backups/odoo_20240101_120000.dump

# Manual
docker compose cp backups/backup.dump db:/tmp/restore.dump
docker compose exec db dropdb -U odoo --if-exists odoo
docker compose exec db createdb -U odoo -O odoo odoo
docker compose exec db pg_restore -U odoo -d odoo -F c --no-owner --no-privileges /tmp/restore.dump
docker compose restart odoo
```

## Common Commands

```bash
make up        # Start services
make down      # Stop services
make restart   # Restart services
make logs      # Follow logs
make shell     # Bash into Odoo container
make psql      # Open PostgreSQL shell
make backup    # Backup database
make restore   # Restore database
make update m=module_name  # Update module
make clean     # Remove all data and containers
```

## Shell Access

```bash
# Odoo container
make shell
# or
docker compose exec odoo bash

# PostgreSQL
make psql
# or
docker compose exec db psql -U odoo -d odoo

# Odoo Python shell
docker compose exec odoo odoo shell -d odoo
```

## Configuration

All configurable values are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DB` | `odoo` | Database name |
| `POSTGRES_USER` | `odoo` | Database user |
| `POSTGRES_PASSWORD` | `odoo18@2024!` | Database password |
| `ODOO_PORT` | `8069` | Odoo HTTP port |
| `ODOO_LONGPOLLING_PORT` | `8072` | Longpolling port |
| `ODOO_ADMIN_PASSWD` | `admin` | Admin password |
| `ODOO_WORKERS` | `4` | Number of worker processes |
| `ODOO_DBFILTER` | `.*` | Database filter regex |

## Logs

Logs are written to `logs/odoo.log` on the host and accessible via:

```bash
make logs
# or
tail -f logs/odoo.log
```

## Troubleshooting

### Odoo fails to start

Check the logs:
```bash
make logs
```

Common issues:
- **Port conflict**: Change `ODOO_PORT` in `.env`
- **Database connection**: Ensure PostgreSQL is healthy: `docker compose ps`
- **Permission issues**: Check `chown` on data directories

### Database connection refused

```bash
# Check if PostgreSQL is running
docker compose ps

# View PostgreSQL logs
docker compose logs db

# Restart PostgreSQL
docker compose restart db
```

### Module not found after adding

```bash
# Ensure the module directory has __init__.py and __manifest__.py
ls addons/custom/my_module/

# Restart Odoo
make restart

# Update module list
make update m=base
```

### Reset database

```bash
# Stop Odoo
make down

# Remove database files
rm -rf data/postgres/*

# Start fresh
make up
```

### "Database backup: ERROR: Connection refused"

PostgreSQL may not be ready yet. Wait a few seconds and try again.

### Enterprise modules not visible

Ensure the `addons/enterprise/` directory contains valid Odoo enterprise addons. An empty directory will not show enterprise modules. Check the logs:

```bash
docker compose logs odoo | grep -i enterprise
```

### Permission denied on volumes

```bash
# Fix permissions
sudo chown -R $(id -u):$(id -g) data/ logs/ backups/
```

### Odoo running out of memory

Adjust the memory limits in `.env`:
```
ODOO_LIMIT_MEMORY_SOFT=2147483648   # 2 GB
ODOO_LIMIT_MEMORY_HARD=2684354560   # 2.5 GB
```

## License

This project is for development purposes. Odoo is a trademark of Odoo S.A.
