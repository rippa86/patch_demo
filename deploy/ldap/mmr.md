# 389-ds instance + multi-master replication
#
# On each shop host:
#   dnf install -y 389-ds-base openldap-clients
# Fill instance.inf from instance.inf.j2 (host-specific full_machine_name).
#   dscreate from-file /etc/webstore/instance.inf
#
# Load suffix (skip if dscreate already created dc=webstore,dc=local):
#   ldapadd -x -H ldap://localhost -D "cn=Directory Manager" -W -f suffix.ldif
#   ldapadd -x -H ldap://localhost -D "cn=Directory Manager" -W -f seed-users.ldif
#
# Service bind account for api-account:
#   ldapadd ...
#     dn: uid=api,ou=Services,dc=webstore,dc=local
#     objectClass: inetOrgPerson
#     uid: api
#     cn: api
#     sn: api
#   ldappasswd -x -H ldap://localhost -D "cn=Directory Manager" -W \
#     -s "$LDAP_API_PASSWORD" "uid=api,ou=Services,dc=webstore,dc=local"
#   ldappasswd ... -s "$SHOPPER_PASSWORD" "uid=shopper,ou=People,dc=webstore,dc=local"
#
# ACI: allow uid=api to add entries under ou=People (389-ds):
#   dsconf webstore plugin attr-uniqueness add --type uid --across-all-subtrees
#   Or an ACI on ou=People granting add to the api DN.
#
# Replication (supplier on both hosts):
# web-frontend1:
#   dsconf webstore replication enable --suffix "dc=webstore,dc=local" \
#     --role supplier --replica-id 1
# web-frontend2:
#   dsconf webstore replication enable --suffix "dc=webstore,dc=local" \
#     --role supplier --replica-id 2
#
# Agreements (run on each side pointing at the peer, LDAPS if certs exist):
#   dsconf webstore repl-agmt create \
#     --suffix "dc=webstore,dc=local" \
#     --host web-frontend2.lab.rippa.io --port 389 --conn-protocol LDAP \
#     --bind-dn "cn=Directory Manager" --bind-passwd "$LDAP_MANAGER_PASSWORD" \
#     --bind-method SIMPLE \
#     meToPeer
#   dsconf webstore repl-agmt init --suffix "dc=webstore,dc=local" meToPeer
#
# Listen on all addresses so Podman can use host.containers.internal:389
# and the peer can replicate:
#   dsconf webstore config replace nsslapd-listenhost=0.0.0.0
#   systemctl restart dirsrv@webstore
#
# Lab passwords live in /etc/webstore/webstore.env — never in git.
