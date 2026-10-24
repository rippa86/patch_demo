# Rippa Lab HA webstore

Active-active shop on two RHEL 10 hosts behind HAProxy on an ops VM, with 389-ds (replicated) and
MariaDB Galera on the shop hosts, plus Prometheus/Grafana monitoring.

| Role | Host | Runs |
| --- | --- | --- |
| Shop node | `web-frontend1.lab.rippa.io` (**10.10.4.115**) | httpd, APIs, backend, MariaDB Galera, 389-ds |
| Shop node | `web-frontend2.lab.rippa.io` (**10.10.4.116**) | Same as web-frontend1 |
| Shop URL | `shoppingapp.lab.rippa.io` → **10.10.4.30** | Second IP on demo-vm; HAProxy listens here |
| Ops / quorum | `demo-vm.lab.rippa.io` (**10.10.4.108**) | HAProxy, garbd, exporter, Prometheus, Grafana |

All addresses, names and ports are variables in [`ansible/group_vars/all/main.yml`](ansible/group_vars/all/main.yml).

## What you get

- Shop: create account, login, logout, cart, checkout
- Admin page (`/admin.html`): create products and restock (users in the `admins` table)
- APIs behind HAProxy: `/api/account`, `/api/login`, `/api/logout`, `/api/cart`, `/api/stats`, `/api/admin`
- Grafana on demo-vm: active users, API traffic, Galera and LDAP per node ([monitoring/](monitoring/README.md))
- Test tools: 100 test accounts and a load generator ([tools/](tools/README.md))

## Install (or re-converge) with Ansible

On a controller (your laptop or demo-vm) with `ansible-core` 2.14+ and SSH + sudo to the three hosts
(RHEL 10, registered so `dnf` works):

```bash
git clone -b admin-api-and-monitoring https://github.com/rippa86/patch_demo.git
cd patch_demo/ansible
cp inventory.ini.example inventory.ini           # set ansible_user if you don't SSH as root
vi group_vars/all/main.yml                        # IPs, names, shop URL/IP, ports

ansible-playbook playbooks/site.yml -e lab_password='YourLabPassword1'
```

`lab_password` is the **only** secret: MariaDB (root, app, SST, monitor), 389-ds Directory Manager,
the LDAP `api`, `admin` and replication accounts, the JWT secret, the internal token and the Grafana
admin all use it. Prefer a vault over the command line:

```bash
cp group_vars/all/vault.yml.example group_vars/all/vault.yml   # set lab_password
ansible-vault encrypt group_vars/all/vault.yml
ansible-playbook playbooks/site.yml --ask-vault-pass
```

What `site.yml` does, in order:

| Step | Hosts | |
| --- | --- | --- |
| common | all | `/etc/hosts`, packages, chrony, firewalld rules (added live, no reload), code to `/opt/webstore` |
| ldap | shop | 389-ds instance, base entries, `api` + `admin` users, ACI, multi-supplier replication (`meToPeer`) |
| shop_apps (config) | shop | `webstore.env`, Podman quadlets, image builds |
| galera | shop | Bootstrap a new cluster, or join a running one; refuses to guess if all nodes are down with data |
| shop_apps (start) | shop | backend → APIs → httpd, each health-checked |
| ops roles | demo-vm | garbd, shop IP + HAProxy (+ stats), exporter + Prometheus + Grafana |
| post | | read-only `monitor` DB user, `user1..user100`, smoke test |

It is safe to re-run on a working lab: every step checks first, and only services whose image,
unit or settings changed are restarted. MariaDB is only restarted (one node at a time) if
`galera.cnf` changes.

**Firewall** (from `group_vars`): shop hosts open 8080, 8001, 8010–8013, 8015, 3306, 4444, 4567/tcp+udp,
4568, 389, 636 to the lab subnet and 8014 to demo-vm only; demo-vm opens 80, 8404 and 3000 to everyone
and 4567 to the lab subnet.

**Galera after a full outage**: if no node is running but both have data, the playbook stops and
prints each node's `seqno`. Re-run with the node that has the highest value:

```bash
ansible-playbook playbooks/site.yml -e galera_force_bootstrap=web-frontend2.lab.rippa.io
```

## Check it

| Page | URL |
| --- | --- |
| Shop | http://shoppingapp.lab.rippa.io/ |
| Admin (catalog) | http://shoppingapp.lab.rippa.io/admin.html (`admin` / lab_password) |
| HAProxy stats | http://demo-vm.lab.rippa.io:8404/ |
| Grafana | http://demo-vm.lab.rippa.io:3000/ (anonymous view; `admin` / lab_password to edit) |

```bash
ansible-playbook playbooks/smoke.yml -e lab_password='...'
```

## Admin user

The admin login lives in LDAP like every other user; the **role** lives in MariaDB
(`admins` table, seeded at backend start from `ADMIN_USERS`, default `admin`).
`api-account` refuses to register reserved names (`RESERVED_USERNAMES`), so nobody can
sign up as `admin` and inherit the role.

`site.yml` creates the `admin` LDAP account with `lab_password`; sign in at `/admin.html`.
(Without Ansible: `deploy/ldap/create-admin.sh` on web-frontend1 does the same.)

| Endpoint (JWT from `/api/login`, admin only) | Does |
| --- | --- |
| `GET /api/admin/me` | 200 if the caller is an admin, 403 otherwise |
| `GET /api/admin/products` | Catalog with stock |
| `POST /api/admin/products` `{sku, name, description, price_cents, stock}` | Create a product (409 on duplicate SKU) |
| `POST /api/admin/products/{id}/restock` `{qty}` | Add stock atomically |
| `GET /api/admin/inventory-log` | Who created/restocked what, newest first |

Grant or revoke admins in SQL; it takes effect on the next request:

```sql
INSERT INTO webstore.admins (uid) VALUES ('alice');
DELETE FROM webstore.admins WHERE uid = 'alice';
```

## Drain one shop host

Bleed connections off a node in HAProxy, then stop its services; the other node and garbd keep
quorum:

```bash
ansible-playbook playbooks/drain.yml   -e target=web-frontend1.lab.rippa.io
ansible-playbook playbooks/restore.yml -e target=web-frontend1.lab.rippa.io -e lab_password='...'
```

## Layout

| Path | Purpose |
| --- | --- |
| `frontend/` | Shop HTML/JS (httpd) |
| `api-account/` `api-login/` `api-logout/` `api-cart/` | Public APIs |
| `api-stats/` | Ops graph data (not on the shop VIP) |
| `api-admin/` | Catalog management (`/api/admin`, admins only) |
| `monitoring/` | Exporter, Prometheus and Grafana for demo-vm |
| `tools/` | `create_accounts.py` (user1..user100) and `load_test.py` (simulated shoppers) |
| `backend/` | SQL, sessions, cart |
| `dashboard/` | Old logged-in graph (superseded by `monitoring/`) |
| `synth-users/` | 100 lab logins against the VIP |
| `ansible/` | `site.yml` (full build), smoke, drain, restore; roles per component; settings in `group_vars/all/main.yml` |
| `deploy/` | Quadlets, LDAP helper scripts, env example (keepalived/shop-HAProxy files are legacy, unused) |
