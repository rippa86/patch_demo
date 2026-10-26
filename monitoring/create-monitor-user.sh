#!/usr/bin/env bash
# Create the read-only MariaDB user the exporter on demo-vm uses.
# Run as root on ONE shop host (web-frontend1); Galera replicates users.
#
#   sudo MONITOR_DB_PASSWORD='...' ./monitoring/create-monitor-user.sh
#
# Grants: login (enough for SHOW GLOBAL STATUS) + SELECT on the webstore schema
# (sessions, orders, products). Nothing else.
set -euo pipefail

set -a
# shellcheck disable=SC1091
source /etc/webstore/webstore.env
set +a

PW="${MONITOR_DB_PASSWORD:?set MONITOR_DB_PASSWORD (same value as in demo-vm /etc/webstore/monitoring.env)}"
FROM="${MONITOR_HOST:-10.10.4.108}"
PW_SQL="${PW//\'/\'\'}"

podman exec -i webstore-mariadb mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" <<SQL
CREATE USER IF NOT EXISTS 'monitor'@'${FROM}' IDENTIFIED BY '${PW_SQL}';
ALTER USER 'monitor'@'${FROM}' IDENTIFIED BY '${PW_SQL}';
GRANT SELECT ON webstore.* TO 'monitor'@'${FROM}';
SQL

echo "monitor@${FROM} ready. Test from demo-vm:"
echo "  mariadb -h 10.10.4.115 -u monitor -p -e \"SHOW GLOBAL STATUS LIKE 'wsrep_cluster_size'\""
