# HA webstore — high-level design

Two RHEL 10 shop hosts, active-active, plus one small ops VM. A shopper can create an account, log in, log out, and buy from a three-item cart. Either shop host can be drained with Ansible and powered off without taking the store down.

Implementation rules for an agent live in `.cursor/skills/rhel-ha-webstore/`.

## Decision

| Concern | Choice |
| --- | --- |
| Shop hosts | `web-frontend1.lab.rippa.io` (`10.10.4.115`), `web-frontend2.lab.rippa.io` (`10.10.4.116`) |
| Ops / quorum VM | `demo-vm.lab.rippa.io` (`10.10.4.108`) — dashboard, garbd, synth-users |
| Client name / VIP DNS | `shoppingapp.lab.rippa.io` (keepalived VIP on the shop hosts, not a fourth app box) |
| Virtual load balancer | HAProxy **in a Podman container on each shop host** |
| Virtual IP | keepalived **on the shop hosts** (not a third LB box, not a container) |
| Frontend | Apache httpd: create account, login, logout, cart |
| Public APIs | **Four separate FastAPI services** — account, login, logout, cart |
| Stats API | Fifth FastAPI service, used only by the ops dashboard |
| Backend | Internal FastAPI: SQL, sessions, cart, orders |
| SQL | MariaDB Galera on web-frontend1+web-frontend2, **garbd on demo-vm** |
| Auth store | 389-ds **on each shop host**, multi-master. Passwords never in SQL |
| Sessions | Rows in Galera (not Redis). JWT carries `sid`. No sticky sessions |
| Drain / restore | **Ansible playbooks**, not operator `.sh` scripts |
| Synthetic load | `synth-users` on **demo-vm** — 100 lab accounts, login/out ~30s |
| Runtime | Podman quadlets + systemd |

## Shop behaviour (v1)

Three user-facing functions, each with its own public API:

| Page function | API service | Contract |
| --- | --- | --- |
| Create account | `api-account` | `POST /api/account` — add `uid` in 389-ds |
| Login | `api-login` | `POST /api/login` — LDAP bind, insert session, return JWT |
| Logout | `api-logout` | `POST /api/logout` — revoke session (token no longer counts as logged in) |
| Shopping cart | `api-cart` | `GET/PUT /api/cart`, `POST /api/cart/checkout` — exactly **three** catalog items |

Catalog is fixed at three products (seed data). Checkout writes an order and decrements stock in one Galera transaction.

## Synthetic users (load generator)

Yes — a small app on `demo-vm.lab.rippa.io` drives the graph without browsers.

| Setting | Value |
| --- | --- |
| Users | 100 (`synth001` … `synth100`) |
| Create | Once at start: `POST /api/account` via the **shop VIP** (ignore “already exists”) |
| Loop | Each user has its **own** timer. If logged out → login; if logged in → logout |
| Interval | Uniform random **15–45 seconds** (about 30s, not a global tick) |
| Target | Shop VIP only (`/api/account`, `/api/login`, `/api/logout`) |
| Stop | `systemctl stop webstore-synth-users` — shop stays up, graph goes quiet |

Do **not** log all 100 in on the same second. Independent jitter keeps LDAP and HAProxy honest and makes the line chart move. Expected occupancy is roughly half (~50) if each user spends about as much time in as out.

Tokens live in the synth process memory. Sessions still land in Galera, so the dashboard count is real. Optional cart calls are out of scope for v1 (login/out only).

Idempotent bootstrap: if LDAP already has `synthNNN`, skip create and continue the loop.

## Why this, not the alternatives

**HAProxy can be a container; the VIP cannot require a fourth app host.** keepalived floats the address behind `shoppingapp.lab.rippa.io` between web-frontend1 and web-frontend2. `demo-vm` is quorum + ops only.

**One API process per function.** Account, login, logout, and cart fail, scale, and drain independently. HAProxy routes by path prefix. The browser never talks to LDAP or SQL.

**Sessions in Galera, not stateless-only JWT.** A live “how many users are logged in” graph is impossible with pure stateless tokens. Login inserts `sessions`; logout sets `revoked_at`. Both shop nodes see the same count. Redis is still avoided (same two-node quorum problem as Galera).

**Ansible instead of drain `.sh`.** Planned bleed is an inventory-driven playbook: VIP move, HAProxy `drain`, wait for zero connections, stop units in order. Same engine can restore and (optionally) deploy.

**Dashboard on a different VM.** The graph is an ops concern. If `demo-vm` is down, shopping still works. garbd colocates there so the third failure domain is one small machine.

## Topology

```
  [ shoppers ]                    [ operator browser ]
       |                                  |
 shoppingapp.lab.rippa.io         demo-vm.lab.rippa.io :80
 (keepalived VIP on web-frontend1/02)  httpd dashboard
       |                          synth-users (100 lab logins)
       |                                  |
 [ HAProxy on web-frontend1 + web-frontend2 ] <-----+
                |                         |
    |        |        |        |          |
  /          /api/account  /api/login     |
 httpd       /api/logout   /api/cart      |
 create,     |        |        |          |
 login,      v        v        v          v
 logout,   api-     api-     api-     api-stats
 cart      account  login    logout   (shop hosts, not public)
              |        |        |          |
              +--- LDAP (host) -+          |
                       |                   |
                       v                   v
                 [ backend :8001 ]  ←—— same sessions table
                       |
                 [ MariaDB Galera web-frontend1+web-frontend2 ]
                       |
                 [ garbd on demo-vm 10.10.4.108 ]
```

### Addressing (lab.rippa.io)

| Name | Address | Role |
| --- | --- | --- |
| `shoppingapp.lab.rippa.io` | `10.10.4.50` (allocated VIP; confirm unused) | Shop HTTP(S) only — keepalived on shop hosts |
| `web-frontend1.lab.rippa.io` | `10.10.4.115` | Full shop stack |
| `web-frontend2.lab.rippa.io` | `10.10.4.116` | Full shop stack |
| `demo-vm.lab.rippa.io` | `10.10.4.108` | Dashboard httpd + garbd + synth-users |

VIP stays on the same L2 as the shop hosts (`10.10.4.0/24`). `demo-vm` does not take the shop VIP. HAProxy is not installed as the only listener on a dedicated `shoppingapp` VM — that name is DNS for the VIP.

## Service contracts

| Unit | Where | Port | Public? | Health |
| --- | --- | --- | --- | --- |
| keepalived | shop host | VRRP | n/a | VIP on MASTER |
| haproxy | Podman | 80, 443 | shop VIP | runtime socket |
| httpd-shop | Podman | 8080 | via LB `/` | `GET /healthz.txt` |
| api-account | Podman | 8010 | `/api/account` | `/healthz` (LDAP) |
| api-login | Podman | 8011 | `/api/login` | `/healthz` (LDAP + backend) |
| api-logout | Podman | 8012 | `/api/logout` | `/healthz` (backend) |
| api-cart | Podman | 8013 | `/api/cart` | `/healthz` (backend) |
| api-stats | Podman | 8014 | **ops VM only** | `/healthz` (backend) |
| api-admin | Podman | 8015 | `/api/admin` (admins table) | `/healthz` (backend) |
| backend | Podman | 8001 | **no** | `wsrep` Synced |
| mariadb | Podman | 3306 + Galera | **no** | Primary + Synced |
| 389-ds | shop host | 389, 636 | **no** | LDAPS + MMR |
| garbd | ops VM Podman | 4567 | **no** | in cluster |
| httpd-dash | ops VM Podman | 8080 | ops :80 | `/healthz.txt` |
| synth-users | ops VM Podman | n/a | no | process up; logs login/out |

firewalld: shop VIP allows 80/443. `api-stats` is not in HAProxy’s public ACLs; allow 8014 only from `demo-vm.lab.rippa.io` (`10.10.4.108`). Galera/LDAP/3306/8001 stay internal.

HAProxy path map (`shoppingapp.lab.rippa.io`):

- `/api/account` → api-account web-frontend1+web-frontend2
- `/api/login` → api-login web-frontend1+web-frontend2
- `/api/logout` → api-logout web-frontend1+web-frontend2
- `/api/cart` → api-cart web-frontend1+web-frontend2
- `/api/admin` → api-admin web-frontend1+web-frontend2 (JWT + `admins` row required)
- default `/` → httpd-shop web-frontend1+web-frontend2

Dashboard JS calls `http://web-frontend1.lab.rippa.io:8014` and `http://web-frontend2.lab.rippa.io:8014` with failover — **not** via shoppingapp.

## Auth and sessions

1. **Create account** — `api-account` uses a privileged LDAP bind to add `uid=…,ou=People,dc=webstore,dc=local`. No session yet.
2. **Login** — `api-login` binds as that user, then `POST` internal backend `sessions`. Backend inserts a row (`sid`, `uid`, `issued_at`, `expires_at`). API returns JWT `{ sub, sid }`.
3. **Authenticated calls** — `api-cart` / `api-logout` send JWT to backend; backend requires a non-revoked, unexpired session. Either shop host can serve the call.
4. **Logout** — `api-logout` revokes the `sid`. Dashboard count drops on both nodes. JWT is dead even if not expired.
5. **Logged-in graph** — `api-stats` returns `COUNT(*)` of live sessions plus a short time series (`session_samples`, written on login/logout). Dashboard polls every few seconds and draws a line chart.

Idle expiry still drops the count when `expires_at` passes (stats query filters it).

## Data

LDAP: `ou=People`, `ou=Services`. Privileged DN for account create. User passwords only in 389-ds.

MariaDB:

- `products` — three seed rows; admins add more and restock via `/api/admin`
- `admins` — uids with the admin role (seeded from `ADMIN_USERS`; passwords stay in LDAP)
- `inventory_log` — audit of admin create/restock
- `sessions.node` — shop host whose backend issued the session (per-node active users)
- `carts`, `cart_items`
- `orders`, `order_items`
- `sessions` — live logins
- `session_samples` — `{ t, n }` for the graph

## High availability

| Event | Behaviour |
| --- | --- |
| Crash web-frontend1 | VIP to web-frontend2 if needed. HAProxy uses healthy web-frontend2 APIs. Galera on web-frontend2 + garbd stay Primary. Sessions in Galera still valid. Dashboard still accurate. |
| Planned drain web-frontend1 | Ansible: bleed web-frontend1, then stop. New logins already on web-frontend2. Existing JWTs work there. |
| Stop only MariaDB on web-frontend1 | Backend on web-frontend1 health fails → cart/logout/stats/login (backend part) fail health → HAProxy removes those members. web-frontend2 keeps SQL. |
| Crash demo-vm | Shop stays up. Graph and synth load stop. Galera still has 2 of 3 while both shop hosts live. |
| Both shop hosts partitioned | Galera does not go Primary without garbd majority. Prefer outage over split-brain. |

### Ansible drain (replaces `.sh`)

Playbook `ansible/playbooks/drain.yml` (`-e target=web-frontend1.lab.rippa.io`):

1. If `target` owns the VIP, stop/pause keepalived so the VIP moves.
2. HAProxy runtime: set **all** `target` servers (httpd + four public APIs) to **drain** — no new connections, existing finish.
3. Wait until current connections to `target` are 0 (retries).
4. Set those servers to **maint**.
5. Stop api-account, api-login, api-logout, api-cart, api-stats, backend, httpd.
6. `wsrep_desync=ON`, stop mariadb.
7. Stop 389-ds.

`restore.yml` is the reverse: 389-ds → mariadb join → backend → APIs → httpd → haproxy → keepalived → enable in pools.

Run from a controller (laptop or ops VM) with SSH to the shop hosts. Do not ship `webstore-drain.sh` as the operator interface.

## Container vs host

| On the host | In Podman (shop) | On demo-vm |
| --- | --- | --- |
| keepalived | haproxy, httpd-shop | httpd-dash, synth-users |
| 389-ds | api-account, api-login, api-logout, api-cart, api-stats | garbd |
| firewalld, podman | backend, mariadb | |

## Security baseline

- TLS at shop HAProxy when certs exist.
- LDAPS for account create and login.
- JWT secret + DB + LDAP privileged bind in `/etc/webstore/` or Podman secrets, never git.
- backend, MariaDB, api-stats unpublished on the shop VIP.
- Dashboard is not a public shop page; restrict by network or HTTP basic/admin LDAP.
- SELinux enforcing.

## Out of scope for v1

- Payments, email, image CDN, Kubernetes, Redis.
- HA for the dashboard VM (single ops box is enough).
- Pacemaker/STONITH.

## Documents

| File | Purpose |
| --- | --- |
| [webstore HLD canvas](/Users/dripani/.cursor/projects/Users-dripani-cursor-Patchingdemo/canvases/webstore-hld.canvas.tsx) | Topology, APIs, drain, dashboard |
| `.cursor/skills/rhel-ha-webstore/SKILL.md` | Agent instructions |
| `.cursor/skills/rhel-ha-webstore/architecture.md` | Constraints |
| `.cursor/skills/rhel-ha-webstore/repo-layout.md` | Tree and API freeze |
| `.cursor/skills/rhel-ha-webstore/implementation-prompt.md` | Prompt to build it |
