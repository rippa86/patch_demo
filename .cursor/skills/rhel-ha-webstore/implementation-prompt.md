# Implementation prompt

Paste the block below into a **new** Cursor agent chat in this repo (or attach skill `rhel-ha-webstore`).

```
Follow the project skill `rhel-ha-webstore` and the HLD in docs/HLD.md.

Build the HA webstore against this lab inventory. Use these names everywhere (Ansible inventory, keepalived, Galera wsrep IPs, LDAP MMR, HAProxy backends, docs). Do not use webstore-a / webstore-b / 10.0.0.x placeholders.

Lab hosts:
- Shop node 1: web-frontend1.lab.rippa.io  (RHEL 10, 10.10.4.115)
- Shop node 2: web-frontend2.lab.rippa.io  (RHEL 10, 10.10.4.116)
- Client / load-balancer name: shoppingapp.lab.rippa.io
  This is the keepalived floating VIP DNS name, NOT a fourth app server.
  HAProxy still runs in Podman on web-frontend1 and web-frontend2.
  Allocate an unused 10.10.4.0/24 address as the VIP, document it in deploy/env.example, and assume shoppingapp.lab.rippa.io is (or will be) an A record to that VIP.
- Quorum / ops host: demo-vm.lab.rippa.io  10.10.4.108
  garbd, ops dashboard httpd, synth-users. Not in the shop VIP path. Does not run keepalived for shoppingapp.

Architecture (do not change):
- web-frontend1 and web-frontend2 each run the full shop stack, active-active. Either host can be Ansible-drained and shut down; the shop stays up.
- Virtual load balancer: HAProxy container on each shop host. VIP via keepalived on web-frontend1/web-frontend2 (host, not a container). Clients use https://shoppingapp.lab.rippa.io (HTTP ok for lab if no certs).
- Frontend: Apache httpd, vanilla HTML+JS: create account, login, logout, shopping cart with exactly three items.
- Separate public API containers (do not merge): api-account, api-login, api-logout, api-cart.
- Internal backend owns MariaDB. api-stats is NOT on shoppingapp; only demo-vm may call it (web-frontend1:8014 and web-frontend2:8014).
- Auth: 389-ds on each shop host (not a container), multi-master between web-frontend1 and web-frontend2. Create account writes LDAP. Login binds LDAP. Passwords never in SQL.
- Sessions in Galera; JWT contains sid. No Redis, no sticky sessions.
- Database: MariaDB Galera on web-frontend1 + web-frontend2 plus garbd on demo-vm.lab.rippa.io.
- demo-vm: dashboard page that graphs currently logged-in users (poll api-stats). Also synth-users: create 100 lab accounts (synth001–synth100) once, then each user independently logs in and out on a random 15–45s interval (~30s) against http://shoppingapp.lab.rippa.io. Stopping synth must not take the shop down. Do not log all 100 in on the same tick.
- Drain/restore: Ansible playbooks that bleed HAProxy connections off the target (drain → wait for 0 conns → stop). Inventory hosts: web-frontend1.lab.rippa.io, web-frontend2.lab.rippa.io, demo-vm.lab.rippa.io. Example: ansible-playbook .../drain.yml -e target=web-frontend1.lab.rippa.io. No operator .sh drain scripts.
- Runtime: Podman quadlets + systemd. UBI 10 images. Secrets in env.example / /etc/webstore, never real passwords in git.

Ansible groups:
- webstore_shop: web-frontend1.lab.rippa.io, web-frontend2.lab.rippa.io
- webstore_ops: demo-vm.lab.rippa.io
- vars: shop_vip_dns=shoppingapp.lab.rippa.io, shop_vip_ip=10.10.4.50, galera_garbd_ip=10.10.4.108

Implement in the skill’s build order. When finished, tell me:
1. The resolved IPs you used for web-frontend1, web-frontend2, and the VIP
2. ansible commands to drain web-frontend1.lab.rippa.io
3. URLs for the shop (shoppingapp.lab.rippa.io) and the ops graph (demo-vm.lab.rippa.io)
4. How to bootstrap Galera + 389-ds on these hosts
```
