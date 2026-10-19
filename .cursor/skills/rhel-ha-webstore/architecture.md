# Architecture constraints

Read [docs/HLD.md](../../../docs/HLD.md). Do not reverse these decisions.

## Runtime map

| Component | Location | Notes |
| --- | --- | --- |
| keepalived | shop host | VIP on A or B only |
| 389-ds | shop host | MMR. Not a container |
| haproxy | Podman shop | Path-routes four public APIs |
| httpd-shop | Podman shop | Register, login/logout UI, cart |
| api-account | Podman shop :8010 | Create account → LDAP |
| api-login | Podman shop :8011 | Login → LDAP + session |
| api-logout | Podman shop :8012 | Logout → revoke session |
| api-cart | Podman shop :8013 | Three-item cart + checkout |
| api-stats | Podman shop :8014 | Ops only |
| backend | Podman shop :8001 | Only process that opens SQL |
| mariadb | Podman shop | Galera, advertise host IP |
| garbd | **demo-vm.lab.rippa.io** | Third vote (`10.10.4.108`) |
| httpd-dash | **demo-vm.lab.rippa.io** | Logged-in graph page |
| synth-users | **demo-vm.lab.rippa.io** | 100 lab users login/out via shoppingapp ~30s |

## HAProxy (shop VIP)

- `/api/account` → `web-frontend1.lab.rippa.io:8010` and `web-frontend2.lab.rippa.io:8010`
- `/api/login` → `*:8011`
- `/api/logout` → `*:8012`
- `/api/cart` → `*:8013`
- `/` → `*:8080`
- Do **not** route `/api/stats` on the VIP
- `balance roundrobin`, HTTP checks, no cookies / stick tables
- Runtime socket so Ansible can `drain` then `maint`

## Cross-host

Podman network `webstore` on each shop host: APIs → `http://backend:8001`, backend → `mariadb:3306`.

LDAP: `ldaps://127.0.0.1` then peer.

If local backend health fails, APIs that need SQL may use `http://<peer>:8001`. Never publish 8001 on the VIP.

## Galera

Cluster `webstore`, two nodes + garbd on ops, `pc.ignore_sb=false`, SST mariabackup.

## LDAP

Suffix `dc=webstore,dc=local`. Create-account uses `uid=api,ou=Services,…`. Login binds as `uid={uid},ou=People,…`. Seed optional lab users in `deploy/ldap/`, not in app code.

## Sessions

- Table `sessions(sid, uid, issued_at, expires_at, revoked_at)`
- JWT HS256 `{ sub, sid }`, same secret on A and B, 15–60 min
- Logout and expiry remove the user from the dashboard count
- `session_samples(t, n)` updated on login/logout for the graph
- Frontend: `sessionStorage` for the token. Secret never in JS

## App behaviour (v1)

Must work:

- Create account (unique uid)
- Login / logout
- Cart for **three** named products; checkout
- Ops page: current logged-in count + line graph over time
- Ansible drain of web-frontend1 with connections bled to web-frontend2
- `synth-users` creates 100 `synthNNN` accounts once, then each user logs in/out on an independent 15–45s timer against the shop VIP

Skip payments, email, CDN.

## What not to introduce

- Merging the four public APIs into one FastAPI process
- Nginx instead of httpd
- Operator `webstore-drain.sh` / `webstore-restore.sh` (Ansible only)
- Redis, SQLite, or single-node MariaDB
- LDAP in Podman
- Sticky sessions
- Dashboard on the shop VIP as a public page
- Logging all 100 synth users in on the same clock tick
