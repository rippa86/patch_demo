# Monitoring (demo-vm): exporter → Prometheus → Grafana

Replaces the old `webstore-httpd-dash` page. That page ran in the browser and
called `:8014` on each shop node directly, which fails for two reasons: the
firewall only lets demo-vm reach 8014, and `api-stats` CORS only allows the
origin `http://10.10.4.108` (port 80), not `:8088`. Everything here runs
server-side on demo-vm instead.

```
                    demo-vm (10.10.4.108)
  ┌───────────────────────────────────────────────────────┐
  │ HAProxy :80 ── stats CSV :8404 ─┐                     │
  │                                  ▼                     │
  │                       webstore-exporter 127.0.0.1:9200 │──► MariaDB :3306  web-frontend1/2
  │                                  ▲                     │──► 389-ds  :389   web-frontend1/2
  │  Grafana :3000 ──► Prometheus 127.0.0.1:9090 (scrape)  │
  └───────────────────────────────────────────────────────┘
```

## Dashboard: "Webstore - Overview"

| Section | What it answers | Source |
| --- | --- | --- |
| Active users per node | Who is logged in, and which shop node issued their session | `sessions.node` (new column written by the backend) |
| APIs per node | Requests/s, errors, latency and UP/DOWN for each API on each node; traffic split | HAProxy stats on demo-vm |
| Database (Galera) | Per-node state (Synced/Donor/Joining/DOWN), quorum, cluster size, **which node is accepting writes**, replication pressure | `SHOW GLOBAL STATUS LIKE 'wsrep%'` on each node |
| LDAP | Bind up/down and latency per node, users in `ou=People` per node, replication drift | ldap3 bind as `uid=api` on each node |
| Catalog and orders | Stock per product (works with the admin restock page), orders/min | `products`, `orders` |

Galera has no fixed primary/secondary: every Synced node in the Primary
component accepts writes. The "Writes accepted" panels show where writes
actually originate. Each shop node's backend writes to its own local MariaDB,
so that split follows HAProxy's load balancing.

## Install

1. **On web-frontend1**, create the read-only DB user (Galera replicates it):

   ```bash
   sudo MONITOR_DB_PASSWORD='pick-one' /opt/webstore/monitoring/create-monitor-user.sh
   ```

2. **On demo-vm**, from a repo checkout:

   ```bash
   sudo ./monitoring/setup-monitoring.sh      # first run creates /etc/webstore/monitoring.env and stops
   sudo vi /etc/webstore/monitoring.env       # MONITOR_DB_PASSWORD, LDAP_API_PASSWORD (uid=api)
   sudo ./monitoring/setup-monitoring.sh      # builds the exporter, starts all three
   ```

   It prints the Grafana admin password on first run and a quick health
   readout. Open `http://demo-vm.lab.rippa.io:3000/`; anonymous users get read-only access.

3. `HAPROXY_STATS_URL` defaults to `http://127.0.0.1:8404/;csv`, which matches
   a `listen stats` block with `stats uri /`. If your stats page has another
   URI, append `;csv` to it.

4. Optional: retire the old dashboard.

   ```bash
   sudo systemctl stop webstore-httpd-dash
   sudo rm /etc/containers/systemd/webstore-httpd-dash.container && sudo systemctl daemon-reload
   ```

## Troubleshooting

```bash
curl -s 127.0.0.1:9200/metrics | grep -E '_up|synced|data_source'   # 1 = OK
journalctl -u webstore-exporter -n 30                                 # reason for any 0
```

| Symptom | Fix |
| --- | --- |
| `galera_up{node=…} 0`, log says `Access denied for user 'monitor'@'X'` | The DB saw demo-vm as `X`. Re-run step 1 with `MONITOR_HOST=X`. |
| `ldap_up 0`, `invalidCredentials` | `LDAP_API_PASSWORD` in monitoring.env doesn't match the `uid=api` password. |
| `haproxy_up 0` | Check `curl '127.0.0.1:8404/;csv'`; fix `HAPROXY_STATS_URL`. |
| Active users all show `unknown` | Backend not upgraded yet; sessions created after the upgrade carry the node. |
| API panels show odd node names | The exporter maps HAProxy server **addresses** to `NODES`; make sure the IPs match. |
