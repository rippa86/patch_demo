---
name: rhel-ha-webstore
description: >-
  Builds and maintains a RHEL 10 active-active webstore with Podman-separated
  httpd, four public FastAPI services (account, login, logout, cart), stats API,
  Python backend, MariaDB Galera, host 389-ds, HAProxy, Ansible drain/restore,
  an ops VM dashboard of logged-in users, and a 100-user synth login/out
  generator. Use when implementing, extending, deploying, or draining this HA
  webstore, or when the user mentions dual-host Podman, Galera, 389-ds, garbd,
  Ansible drain, synthetic users, or rhel-ha-webstore.
---

# RHEL HA webstore

Implement [docs/HLD.md](../../../docs/HLD.md). Constraints: [architecture.md](architecture.md). Tree: [repo-layout.md](repo-layout.md). Prompt: [implementation-prompt.md](implementation-prompt.md).

## Non-negotiables

- Two RHEL 10 shop hosts, active-active. Drain one with Ansible; the shop stays up.
- Separate public APIs — do not merge: `api-account`, `api-login`, `api-logout`, `api-cart`. Plus internal `backend` and `api-stats`.
- Frontend: create account, login, logout, cart with **exactly three** products. HTML/JS only, httpd, calls those APIs only.
- LDAP (389-ds) and keepalived on the **shop host**. garbd, dashboard httpd, and **synth-users** on **demo-vm.lab.rippa.io**.
- HAProxy in Podman on shop hosts. VIP via keepalived on A/B, not on ops.
- Galera = two data nodes + garbd on ops. Sessions live in Galera (JWT has `sid`). No Redis. No sticky sessions.
- Drain/restore are **Ansible playbooks**, not operator shell scripts.
- UBI 10 images. Quadlets + systemd.

## Build order

```
- [ ] 0. Repo layout per repo-layout.md
- [ ] 1. Backend + schema (3 products, cart, orders, sessions, samples) + /healthz
- [ ] 2. Four public APIs + api-stats (each own Containerfile / quadlet)
- [ ] 3. Shop frontend (account, login, logout, cart)
- [ ] 4. Ops dashboard page (logged-in count + line graph)
- [ ] 5. synth-users: 100 accounts, independent ~30s login/out via VIP
- [ ] 6. Quadlets, Galera, 389-ds MMR, HAProxy path routing, keepalived, firewalld
- [ ] 7. Ansible drain.yml + restore.yml (HAProxy drain → wait 0 conns → stop)
- [ ] 8. Smoke: drain A, shop and dashboard still work on B; synth keeps hitting B
```

## Service rules

**httpd-shop** — static files. Pages for register, session, cart. `GET /healthz.txt`. No ProxyPass.

**api-account :8010** — `POST /api/account`. LDAP add user only. No SQL.

**api-login :8011** — `POST /api/login`. LDAP bind, then backend create session. Returns JWT.

**api-logout :8012** — `POST /api/logout`. Bearer JWT → backend revoke session.

**api-cart :8013** — cart + checkout for the three products. Bearer JWT. SQL only via backend.

**api-stats :8014** — live session count + series. Not on shop VIP. Dashboard is the only client.

**backend :8001** — only SQL client. Sessions, products, cart, orders. Require Galera Synced.

**haproxy** — path ACLs to the four public APIs + default httpd. Round-robin, HTTP checks, runtime socket for Ansible drain.

**389-ds** — `dc=webstore,dc=local`. Account API uses a service bind to create users. Login uses user bind.

**synth-users (ops VM)** — Python client, not a shop API. Bootstrap `synth001`–`synth100` via `POST /api/account` on the VIP. Then each user independently sleeps `random.uniform(15, 45)` and toggles login/logout. Treat “uid exists” as success. Stoppable without affecting the shop. Do not thundering-herd (no single `sleep(30)` for all 100).

## Ansible drain / restore

`ansible/playbooks/drain.yml -e target=web-frontend1.lab.rippa.io`

1. Move VIP off `target` if needed (keepalived).
2. Set `target` servers to HAProxy **drain** (no new conns).
3. Wait until current connections to `target` are 0.
4. `maint`, then stop APIs, backend, httpd, mariadb (desync first), 389-ds.

`restore.yml` reverses that. Inventory: `webstore_shop` (web-frontend1.lab.rippa.io, web-frontend2.lab.rippa.io), `webstore_ops` (demo-vm.lab.rippa.io). Client VIP DNS: `shoppingapp.lab.rippa.io`. No `deploy/scripts/webstore-drain.sh`.

## Quality bar

- Health checks fail when LDAP or Galera is down so HAProxy removes that member.
- Dashboard count matches Galera `sessions` (not “users on this host”).
- Secrets in `/etc/webstore/` or Podman secrets, never git.
- Do not add Nginx, Kubernetes, Redis, or a single combined API container.
