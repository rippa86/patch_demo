#!/usr/bin/env python3
"""webstore-exporter: one Prometheus endpoint for the whole lab.

Runs on demo-vm next to HAProxy and Prometheus. Every scrape it polls, in
parallel and with short timeouts:

  * HAProxy stats CSV       -> per-API, per-node request/response/health metrics
  * MariaDB on each node    -> Galera state, cluster size, which node takes writes
  * MariaDB (any live node) -> active users per node, orders, stock
  * 389-ds on each node     -> bind health, latency, People entry count

Configuration is environment variables (see monitoring/monitoring.env.example).
"""
import csv
import io
import logging
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pymysql
from ldap3 import Connection, Server
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily

log = logging.getLogger("webstore-exporter")

# "web-frontend1=10.10.4.115,web-frontend2=10.10.4.116"
NODES = dict(
    item.split("=", 1)
    for item in os.environ.get(
        "NODES", "web-frontend1=10.10.4.115,web-frontend2=10.10.4.116"
    ).split(",")
    if "=" in item
)
IP_TO_NODE = {ip: name for name, ip in NODES.items()}

HAPROXY_STATS_URL = os.environ.get("HAPROXY_STATS_URL", "http://127.0.0.1:8404/;csv")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("MONITOR_DB_USER", "monitor")
DB_PASSWORD = os.environ.get("MONITOR_DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "webstore")
LDAP_PORT = int(os.environ.get("LDAP_PORT", "389"))
LDAP_BIND_DN = os.environ.get("LDAP_SERVICE_DN", "uid=api,ou=Services,dc=webstore,dc=local")
LDAP_BIND_PASSWORD = os.environ.get("LDAP_API_PASSWORD", "")
LDAP_PEOPLE_OU = os.environ.get("LDAP_PEOPLE_OU", "ou=People,dc=webstore,dc=local")
TIMEOUT = float(os.environ.get("CHECK_TIMEOUT", "3"))
PORT = int(os.environ.get("EXPORTER_PORT", "9200"))
# Prometheus runs on the same host; keep the endpoint off the LAN by default.
ADDR = os.environ.get("EXPORTER_ADDR", "127.0.0.1")

GALERA_STATES = {"1": "Joining", "2": "Donor/Desynced", "3": "Joined", "4": "Synced"}


def short(name: str) -> str:
    return name.split(".", 1)[0]


def node_for_server(svname: str, addr: str) -> str:
    ip = addr.rsplit(":", 1)[0] if addr else ""
    return IP_TO_NODE.get(ip) or short(svname)


def api_for_proxy(pxname: str) -> str:
    name = pxname[3:] if pxname.startswith("be_") else pxname
    return {"web_pool": "web", "httpd": "web"}.get(name, name)


# --------------------------------------------------------------------------- HAProxy


def fetch_haproxy() -> list[dict]:
    with urllib.request.urlopen(HAPROXY_STATS_URL, timeout=TIMEOUT) as resp:
        text = resp.read().decode()
    text = text.lstrip("# ")
    return [row for row in csv.DictReader(io.StringIO(text)) if row.get("type") == "2"]


def haproxy_metrics(rows: list[dict] | None):
    up = GaugeMetricFamily("webstore_haproxy_up", "HAProxy stats readable by the exporter")
    up.add_metric([], 0 if rows is None else 1)
    yield up
    if rows is None:
        return
    srv_up = GaugeMetricFamily(
        "webstore_api_server_up", "HAProxy health of this API on this node (1=UP)", labels=["api", "node"]
    )
    resp = CounterMetricFamily(
        "webstore_api_responses", "HTTP responses per API, node and class",
        labels=["api", "node", "code"],
    )
    sess = CounterMetricFamily(
        "webstore_api_sessions", "Connections HAProxy sent to this API/node", labels=["api", "node"]
    )
    cur = GaugeMetricFamily(
        "webstore_api_current_sessions", "Open connections to this API/node", labels=["api", "node"]
    )
    rtime = GaugeMetricFamily(
        "webstore_api_response_time_seconds",
        "Average response time over the last 1024 requests", labels=["api", "node"],
    )

    def num(row, key):
        try:
            return float(row.get(key) or 0)
        except ValueError:
            return 0.0

    for row in rows:
        api = api_for_proxy(row["pxname"])
        node = node_for_server(row["svname"], row.get("addr", ""))
        labels = [api, node]
        srv_up.add_metric(labels, 1 if row.get("status", "").startswith("UP") else 0)
        for code in ("1xx", "2xx", "3xx", "4xx", "5xx", "other"):
            resp.add_metric(labels + [code], num(row, f"hrsp_{code}"))
        sess.add_metric(labels, num(row, "stot"))
        cur.add_metric(labels, num(row, "scur"))
        rtime.add_metric(labels, num(row, "rtime") / 1000.0)
    yield from (srv_up, resp, sess, cur, rtime)


# --------------------------------------------------------------------------- MariaDB


def db_connect(ip: str):
    return pymysql.connect(
        host=ip, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME,
        connect_timeout=TIMEOUT, read_timeout=TIMEOUT, write_timeout=TIMEOUT,
        autocommit=True,
    )


def fetch_galera(ip: str) -> dict | None:
    conn = db_connect(ip)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW GLOBAL STATUS LIKE 'wsrep%'")
            return {k: v for k, v in cur.fetchall()}
    finally:
        conn.close()


def fetch_shop(ip: str) -> dict:
    conn = db_connect(ip)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT uid, COALESCE(node, 'unknown') FROM sessions "
                    "WHERE revoked_at IS NULL AND expires_at > UTC_TIMESTAMP() LIMIT 1000"
                )
            except (pymysql.err.OperationalError, pymysql.err.ProgrammingError):  # backend not upgraded yet: no `node` column
                cur.execute(
                    "SELECT uid, 'unknown' FROM sessions "
                    "WHERE revoked_at IS NULL AND expires_at > UTC_TIMESTAMP() LIMIT 1000"
                )
            sessions = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM orders")
            orders = cur.fetchone()[0]
            cur.execute("SELECT sku, name, stock FROM products ORDER BY id")
            stock = cur.fetchall()
        return {"sessions": sessions, "orders": orders, "stock": stock}
    finally:
        conn.close()


def galera_metrics(results: dict[str, dict | None]):
    names = {
        "up": ("webstore_galera_up", "Exporter can query MariaDB on this node"),
        "ready": ("webstore_galera_ready", "wsrep_ready = ON"),
        "primary": ("webstore_galera_primary", "Node is in the Primary component (has quorum)"),
        "synced": ("webstore_galera_synced", "wsrep_local_state_comment = Synced"),
        "state": ("webstore_galera_state", "wsrep_local_state: 1 Joining 2 Donor 3 Joined 4 Synced"),
        "size": ("webstore_galera_cluster_size", "Members in the cluster as seen by this node (incl. garbd)"),
        "fc": ("webstore_galera_flow_control_paused_ratio", "Fraction of time paused by flow control"),
        "queue": ("webstore_galera_recv_queue", "Write-sets waiting to be applied"),
        "last": ("webstore_galera_last_committed", "Last committed seqno"),
    }
    g = {k: GaugeMetricFamily(n, h, labels=["node"]) for k, (n, h) in names.items()}
    info = GaugeMetricFamily(
        "webstore_galera_info", "Galera state as labels", labels=["node", "state", "cluster_status"]
    )
    repl = CounterMetricFamily(
        "webstore_galera_replicated_writesets",
        "Write-sets that ORIGINATED on this node (i.e. writes this node accepted)", labels=["node"],
    )
    recv = CounterMetricFamily(
        "webstore_galera_received_writesets", "Write-sets received from other nodes", labels=["node"]
    )

    def f(s, key):
        try:
            return float(s.get(key) or 0)
        except ValueError:
            return 0.0

    for node, s in results.items():
        if s is None:
            g["up"].add_metric([node], 0)
            continue
        g["up"].add_metric([node], 1)
        g["ready"].add_metric([node], 1 if s.get("wsrep_ready") == "ON" else 0)
        g["primary"].add_metric([node], 1 if s.get("wsrep_cluster_status") == "Primary" else 0)
        g["synced"].add_metric([node], 1 if s.get("wsrep_local_state_comment") == "Synced" else 0)
        g["state"].add_metric([node], f(s, "wsrep_local_state"))
        g["size"].add_metric([node], f(s, "wsrep_cluster_size"))
        g["fc"].add_metric([node], f(s, "wsrep_flow_control_paused"))
        g["queue"].add_metric([node], f(s, "wsrep_local_recv_queue"))
        g["last"].add_metric([node], f(s, "wsrep_last_committed"))
        info.add_metric(
            [node, s.get("wsrep_local_state_comment", "?"), s.get("wsrep_cluster_status", "?")], 1
        )
        repl.add_metric([node], f(s, "wsrep_replicated"))
        recv.add_metric([node], f(s, "wsrep_received"))
    yield from g.values()
    yield from (info, repl, recv)


def shop_metrics(source: str | None, data: dict | None):
    src = GaugeMetricFamily(
        "webstore_shop_data_source", "Node the shop counters were read from", labels=["node"]
    )
    if data is None:
        yield src
        return
    src.add_metric([source], 1)
    yield src

    per_node: dict[str, set] = {n: set() for n in NODES}
    users = GaugeMetricFamily(
        "webstore_logged_in_user", "A live session (1 per user)", labels=["uid", "node"]
    )
    seen = set()
    for uid, node in data["sessions"]:
        per_node.setdefault(node, set()).add(uid)
        if uid not in seen:
            seen.add(uid)
            users.add_metric([uid, node], 1)
    active = GaugeMetricFamily(
        "webstore_active_users", "Distinct logged-in users by the node that issued the session",
        labels=["node"],
    )
    for node, uids in sorted(per_node.items()):
        active.add_metric([node], len(uids))
    total = GaugeMetricFamily("webstore_active_users_all", "Distinct logged-in users (all nodes)")
    total.add_metric([], len(seen))
    orders = CounterMetricFamily("webstore_orders", "Orders placed")
    orders.add_metric([], data["orders"])
    stock = GaugeMetricFamily("webstore_product_stock", "Units in stock", labels=["sku", "name"])
    for sku, name, qty in data["stock"]:
        stock.add_metric([sku, name], qty)
    yield from (active, total, users, orders, stock)


# --------------------------------------------------------------------------- LDAP


def fetch_ldap(ip: str) -> dict:
    start = time.monotonic()
    server = Server(f"ldap://{ip}:{LDAP_PORT}", connect_timeout=TIMEOUT)
    conn = Connection(
        server, user=LDAP_BIND_DN, password=LDAP_BIND_PASSWORD,
        auto_bind=True, receive_timeout=TIMEOUT, raise_exceptions=True,
    )
    bind_seconds = time.monotonic() - start
    try:
        entries = conn.extend.standard.paged_search(
            LDAP_PEOPLE_OU, "(objectClass=inetOrgPerson)", attributes=["1.1"],
            paged_size=500, generator=True,
        )
        people = sum(1 for e in entries if e.get("type") == "searchResEntry")
    finally:
        conn.unbind()
    return {"bind_seconds": bind_seconds, "people": people}


def ldap_metrics(results: dict[str, dict | None]):
    up = GaugeMetricFamily("webstore_ldap_up", "Service-account bind succeeded", labels=["node"])
    bind = GaugeMetricFamily("webstore_ldap_bind_seconds", "Connect + bind time", labels=["node"])
    people = GaugeMetricFamily(
        "webstore_ldap_people", "Entries under ou=People on this node (replication check)",
        labels=["node"],
    )
    for node, r in results.items():
        up.add_metric([node], 0 if r is None else 1)
        if r is not None:
            bind.add_metric([node], r["bind_seconds"])
            people.add_metric([node], r["people"])
    yield from (up, bind, people)


# --------------------------------------------------------------------------- collector


class WebstoreCollector:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=8)

    def _safe(self, label: str, fn, *args):
        start = time.monotonic()
        try:
            return fn(*args), time.monotonic() - start
        except Exception as exc:  # one dead node must not break the scrape
            log.warning("%s failed: %s", label, exc)
            return None, time.monotonic() - start

    def collect(self):
        jobs = {("haproxy", None): self.pool.submit(self._safe, "haproxy", fetch_haproxy)}
        for node, ip in NODES.items():
            jobs[("galera", node)] = self.pool.submit(self._safe, f"galera {node}", fetch_galera, ip)
            jobs[("ldap", node)] = self.pool.submit(self._safe, f"ldap {node}", fetch_ldap, ip)

        durations = GaugeMetricFamily(
            "webstore_exporter_check_seconds", "Time spent on each check", labels=["check", "node"]
        )
        results: dict = {}
        for (kind, node), fut in jobs.items():
            value, seconds = fut.result()
            results[(kind, node)] = value
            durations.add_metric([kind, node or ""], seconds)

        # Shop counters: read from the first node that is up and synced.
        shop_source, shop_data = None, None
        for node, ip in NODES.items():
            s = results.get(("galera", node))
            if s and s.get("wsrep_local_state_comment") == "Synced":
                shop_data, seconds = self._safe(f"shop {node}", fetch_shop, ip)
                durations.add_metric(["shop", node], seconds)
                if shop_data is not None:
                    shop_source = node
                    break

        yield from haproxy_metrics(results[("haproxy", None)])
        yield from galera_metrics({n: results[("galera", n)] for n in NODES})
        yield from ldap_metrics({n: results[("ldap", n)] for n in NODES})
        yield from shop_metrics(shop_source, shop_data)
        yield durations


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not DB_PASSWORD:
        log.warning("MONITOR_DB_PASSWORD is empty; Galera/shop checks will fail")
    if not LDAP_BIND_PASSWORD:
        log.warning("LDAP_API_PASSWORD is empty; LDAP checks will fail")
    REGISTRY.register(WebstoreCollector())
    start_http_server(PORT, addr=ADDR)
    log.info("listening on %s:%d, nodes=%s, haproxy=%s", ADDR, PORT, NODES, HAPROXY_STATS_URL)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
