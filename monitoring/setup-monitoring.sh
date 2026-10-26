#!/usr/bin/env bash
# Install / update the monitoring stack on demo-vm (run as root from a repo checkout):
#   webstore-exporter  127.0.0.1:9200  polls HAProxy, Galera and LDAP on both shop nodes
#   webstore-prometheus 127.0.0.1:9091 stores 15 days of metrics
#   webstore-grafana   :3000          "Webstore - Overview" dashboard
#
#   sudo ./monitoring/setup-monitoring.sh
#
# First run creates /etc/webstore/monitoring.env from the example and stops so
# you can fill in the passwords. Re-running is safe (it updates config/images).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE=/etc/webstore/monitoring.env
GRAFANA_ENV=/etc/webstore/grafana.env
DEST=/etc/webstore/monitoring

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }
mkdir -p /etc/webstore "$DEST"

# --- 1. exporter settings ----------------------------------------------------
if [[ ! -f $ENV_FILE ]]; then
  install -m 0600 "$HERE/monitoring.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE. Set MONITOR_DB_PASSWORD and LDAP_API_PASSWORD, then re-run."
  exit 1
fi
if grep -qE '^(MONITOR_DB_PASSWORD|LDAP_API_PASSWORD)=change-me' "$ENV_FILE"; then
  echo "$ENV_FILE still has change-me passwords. Edit it, then re-run."
  exit 1
fi
chmod 600 "$ENV_FILE"

# --- 2. config files ---------------------------------------------------------
rm -rf "$DEST/prometheus" "$DEST/grafana"
cp -r "$HERE/prometheus" "$HERE/grafana" "$DEST/"

if [[ ! -f $GRAFANA_ENV ]] || ! grep -q '^GF_SECURITY_ADMIN_PASSWORD=' "$GRAFANA_ENV"; then
  ADMIN_PW="${GRAFANA_ADMIN_PASSWORD:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')}"
  echo "Grafana admin password: $ADMIN_PW  (saved in $GRAFANA_ENV)"
else
  ADMIN_PW="$(sed -n 's/^GF_SECURITY_ADMIN_PASSWORD=//p' "$GRAFANA_ENV")"
fi
umask 077
cat > "$GRAFANA_ENV" <<EOF
GF_SECURITY_ADMIN_PASSWORD=${ADMIN_PW}
GF_AUTH_ANONYMOUS_ENABLED=true
GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/webstore-overview.json
GF_SERVER_HTTP_PORT=3000
GF_ANALYTICS_REPORTING_ENABLED=false
GF_ANALYTICS_CHECK_FOR_UPDATES=false
EOF
umask 022

# --- 3. images and units -----------------------------------------------------
podman build -t localhost/webstore-exporter:1.0 "$HERE/exporter"
podman pull quay.io/prometheus/prometheus:v2.55.1
podman pull docker.io/grafana/grafana-oss:11.6.0
install -m 0644 "$HERE"/quadlets/*.container /etc/containers/systemd/

firewall-cmd --permanent --add-port=3000/tcp >/dev/null && firewall-cmd --reload >/dev/null
# A firewalld reload drops Podman's port-forward rules; put them back.
podman network reload --all >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl restart webstore-exporter webstore-prometheus webstore-grafana

# --- 4. health ---------------------------------------------------------------
echo "Waiting for services..."
for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:9200/metrics >/dev/null \
    && curl -sf http://127.0.0.1:9091/-/ready >/dev/null \
    && curl -sf http://127.0.0.1:3000/api/health >/dev/null && break
  sleep 2
done

echo
echo "Exporter view of the lab (1 = OK):"
curl -s http://127.0.0.1:9200/metrics \
  | grep -E '^webstore_(haproxy_up|galera_up|galera_synced|ldap_up|shop_data_source|active_users_all) ' \
  || true
curl -s http://127.0.0.1:9200/metrics \
  | grep -E '^webstore_(galera_up|galera_synced|ldap_up|shop_data_source)\{' || true
echo
echo "Grafana: http://$(hostname -f):3000/  (anonymous view; admin login for edits)"
echo "If a value above is 0, see: journalctl -u webstore-exporter -n 30"
