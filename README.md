# Rippa Lab HA webstore

Active-active shop on two RHEL 10 hosts behind a keepalived VIP, with LDAP on the host, MariaDB Galera, and an ops VM for the logged-in graph plus synthetic users.

| Role | Host | Notes |
| --- | --- | --- |
| Shop node | `web-frontend1.lab.rippa.io` (**10.10.4.115**) | Full stack (httpd, APIs, backend, Galera, 389-ds, HAProxy, keepalived) |
| Shop node | `web-frontend2.lab.rippa.io` (**10.10.4.116**) | Same as web-frontend1 |
| VIP / shop URL | `shoppingapp.lab.rippa.io` → **10.10.4.50** | Floating IP on the shop hosts, not a fourth app server |
| Ops / quorum | `demo-vm.lab.rippa.io` (**10.10.4.108**) | Dashboard, garbd, synth-users |

Architecture details: [docs/HLD.md](docs/HLD.md). Long-form bootstrap notes: [deploy/BOOTSTRAP.md](deploy/BOOTSTRAP.md).

## What you get

- Shop UI: create account, login, logout, cart with three products
- Separate APIs: `/api/account`, `/api/login`, `/api/logout`, `/api/cart`
- Ops page: live logged-in count and graph
- Optional: 100 synthetic users logging in and out about every 30s

## Prerequisites

On a **controller** (laptop or jump host) that can SSH as root (or sudo) to all three hosts:

- Git, Ansible 2.14+, Python 3
- SSH keys for `web-frontend1`, `web-frontend2`, and `demo-vm`

On **each RHEL 10 shop host** (deploy also installs most of these):

- Podman, keepalived, socat, 389-ds-base, openldap-clients, rsync

Confirm **10.10.4.50 is free** before starting keepalived:

```bash
ping -c1 10.10.4.50
```

Create DNS `shoppingapp.lab.rippa.io` → `10.10.4.50`, or let deploy write `/etc/hosts` on the lab boxes.

## 1. Clone and inventory

```bash
git clone https://github.com/rippa86/patch_demo.git
cd patch_demo
git checkout ha-webstore   # or the branch you are deploying

cd ansible
cp inventory.ini.example inventory.ini
```

Edit `inventory.ini` only if hostnames or IPs differ.

## 2. Secrets

On **all three hosts**, using the same JWT, DB, LDAP, and synth values on both shop nodes. Keep `DATABASE_URL` in sync with `MARIADB_PASSWORD`.

```bash
sudo mkdir -p /etc/webstore
sudo cp deploy/env.example /etc/webstore/webstore.env
sudo chmod 600 /etc/webstore/webstore.env
sudo vi /etc/webstore/webstore.env
```

Replace every `change-me-lab-*` value. `KEEPALIVED_PASS` must be **8 characters or fewer**.

If you place `webstore.env` before the first deploy, Ansible will not overwrite it (`force: false`); it only fills discovered IPs.

## 3. Deploy images and unit files

From `ansible/` on the controller:

```bash
ansible-playbook playbooks/deploy.yml
```

This copies sources to `/opt/webstore`, builds container images, installs Podman quadlets, templates keepalived/Galera, and prints the live shop host IPs.

Apply firewall rules from [deploy/firewalld/README.md](deploy/firewalld/README.md) on the shop hosts (HTTP, VRRP, Galera/LDAP on `10.10.4.0/24`, stats port **8014** only from `10.10.4.108`). On demo-vm allow TCP 80 and Galera 4567 from the lab LAN.

## 4. 389 Directory Server (each shop host)

Run on **web-frontend1 and web-frontend2**:

```bash
sudo dscreate from-file /etc/webstore/instance.inf
```

If the suffix `dc=webstore,dc=local` is already there, skip `suffix.ldif`. Otherwise:

```bash
sudo ldapadd -x -H ldap://localhost -D "cn=Directory Manager" -W -f /opt/webstore/deploy/ldap/suffix.ldif
sudo ldapadd -x -H ldap://localhost -D "cn=Directory Manager" -W -f /opt/webstore/deploy/ldap/api-user.ldif
sudo ldapadd -x -H ldap://localhost -D "cn=Directory Manager" -W -f /opt/webstore/deploy/ldap/seed-users.ldif
sudo ldapmodify -x -H ldap://localhost -D "cn=Directory Manager" -W -f /opt/webstore/deploy/ldap/api-aci.ldif

set -a && source /etc/webstore/webstore.env && set +a
sudo ldappasswd -x -H ldap://localhost -D "cn=Directory Manager" -w "$LDAP_MANAGER_PASSWORD" \
  -s "$LDAP_API_PASSWORD" "uid=api,ou=Services,dc=webstore,dc=local"

sudo dsconf webstore config replace nsslapd-listenhost=0.0.0.0
sudo systemctl enable --now dirsrv@webstore
```

Enable multi-master replication (`replica-id 1` on web-frontend1, `2` on web-frontend2) using [deploy/ldap/mmr.md](deploy/ldap/mmr.md).

## 5. Galera

Bootstrap **web-frontend1 only**, then join web-frontend2 and garbd:

```bash
cd ansible
ansible-playbook playbooks/galera-bootstrap.yml
ansible-playbook playbooks/galera-join.yml
```

On a shop host:

```bash
sudo podman exec webstore-mariadb mysql -e "SHOW STATUS LIKE 'wsrep_cluster_size';"
```

Expect **3** (web-frontend1, web-frontend2, garbd).

## 6. Start the shop

On **each** of web-frontend1 and web-frontend2:

```bash
sudo systemctl enable --now \
  webstore-backend.service \
  webstore-api-account.service \
  webstore-api-login.service \
  webstore-api-logout.service \
  webstore-api-cart.service \
  webstore-api-stats.service \
  webstore-httpd.service \
  webstore-haproxy.service \
  keepalived.service
```

On **demo-vm**:

```bash
sudo systemctl enable --now webstore-httpd-dash.service
```

After the VIP answers HTTP, start synthetic logins:

```bash
sudo systemctl enable --now webstore-synth-users.service
```

## 7. Check it

| Page | URL |
| --- | --- |
| Shop | http://shoppingapp.lab.rippa.io/ |
| Create account | http://shoppingapp.lab.rippa.io/account.html |
| Logged-in graph | http://demo-vm.lab.rippa.io/ |

```bash
cd ansible
ansible-playbook playbooks/smoke.yml
```

You should be able to register, log in, add the three catalog items to a cart, and check out. The ops page should move as synth users (or you) log in and out.

## Drain one shop host

Bleed connections off web-frontend1, then stop it; web-frontend2 keeps serving:

```bash
cd ansible
ansible-playbook playbooks/drain.yml -e target=web-frontend1.lab.rippa.io
```

Bring it back:

```bash
ansible-playbook playbooks/restore.yml -e target=web-frontend1.lab.rippa.io
```

There are no operator `webstore-drain.sh` scripts; use these playbooks.

## Layout

| Path | Purpose |
| --- | --- |
| `frontend/` | Shop HTML/JS (httpd) |
| `api-account/` `api-login/` `api-logout/` `api-cart/` | Public APIs |
| `api-stats/` | Ops graph data (not on the shop VIP) |
| `backend/` | SQL, sessions, cart |
| `dashboard/` | Logged-in graph (demo-vm) |
| `synth-users/` | 100 lab logins against the VIP |
| `ansible/playbooks/` | deploy, Galera, drain, restore, smoke |
| `deploy/` | Quadlets, HAProxy, keepalived, LDAP, env example |
