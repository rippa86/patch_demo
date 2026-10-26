#!/usr/bin/env bash
# Create (or reset the password of) the shop admin account in 389-ds.
#
# Run as root on ONE shop host (web-frontend1); multi-master replication copies
# it to the peer. The password comes from ADMIN_PASSWORD in webstore.env.
# The admin *role* lives in MariaDB (table `admins`, seeded from ADMIN_USERS by
# the backend), so this script only handles the LDAP login.
#
#   sudo /opt/webstore/deploy/ldap/create-admin.sh            # uid "admin"
#   sudo /opt/webstore/deploy/ldap/create-admin.sh opsadmin   # another admin uid
set -euo pipefail

ENV_FILE=/etc/webstore/webstore.env
UID_NAME="${1:-admin}"
LDAP_URI="${LDAP_URI:-ldap://127.0.0.1:389}"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${LDAP_MANAGER_PASSWORD:?missing in $ENV_FILE}"
: "${ADMIN_PASSWORD:?add ADMIN_PASSWORD=... to $ENV_FILE}"
PEOPLE_OU="${LDAP_PEOPLE_OU:-ou=People,dc=webstore,dc=local}"
DN="uid=${UID_NAME},${PEOPLE_OU}"
DM=(-x -H "$LDAP_URI" -D "cn=Directory Manager" -w "$LDAP_MANAGER_PASSWORD")

if ldapsearch "${DM[@]}" -b "$DN" -s base dn >/dev/null 2>&1; then
  echo "$DN exists; resetting password"
else
  echo "creating $DN"
  ldapadd "${DM[@]}" <<EOF
dn: ${DN}
objectClass: inetOrgPerson
objectClass: organizationalPerson
objectClass: person
objectClass: top
uid: ${UID_NAME}
cn: ${UID_NAME}
sn: Shop administrator
mail: ${UID_NAME}@lab.rippa.io
EOF
fi

# Plain modify (not ldappasswd): 389-ds refuses the password-modify extended
# operation without TLS, but hashes a replaced userPassword itself.
ldapmodify "${DM[@]}" >/dev/null <<EOF
dn: ${DN}
changetype: modify
replace: userPassword
userPassword: ${ADMIN_PASSWORD}
EOF

# Prove the bind works.
ldapwhoami -x -H "$LDAP_URI" -D "$DN" -w "$ADMIN_PASSWORD"
echo "OK. Make sure '${UID_NAME}' is listed in ADMIN_USERS (default: admin) and restart webstore-backend."
