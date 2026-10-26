import os
import re

from fastapi import FastAPI, HTTPException
from ldap3 import ALL, Connection, HASHED_SALTED_SHA256, Server
from ldap3.core.exceptions import LDAPEntryAlreadyExistsResult
from ldap3.utils.hashed import hashed
from pydantic import BaseModel, Field

LDAP_URL = os.environ.get("LDAP_URL", "ldap://host.containers.internal:389")
LDAP_PEER_URL = os.environ.get("LDAP_PEER_URL", "")
LDAP_SERVICE_DN = os.environ.get(
    "LDAP_SERVICE_DN", "uid=api,ou=Services,dc=webstore,dc=local"
)
LDAP_API_PASSWORD = os.environ.get("LDAP_API_PASSWORD", "")
PEOPLE_OU = os.environ.get("LDAP_PEOPLE_OU", "ou=People,dc=webstore,dc=local")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
# Names that can only be created by an operator (deploy/ldap/create-admin.sh),
# so nobody can self-register "admin" and inherit the admin role.
RESERVED_USERNAMES = {
    u.strip().lower()
    for u in os.environ.get("RESERVED_USERNAMES", "admin,administrator,root,api").split(",")
    if u.strip()
}

app = FastAPI(title="webstore-api-account")


class AccountIn(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=128)
    email: str = Field(min_length=3, max_length=256)


def _servers() -> list[str]:
    urls = [LDAP_URL]
    if LDAP_PEER_URL and LDAP_PEER_URL not in urls:
        urls.append(LDAP_PEER_URL)
    return urls


def _bind_admin(url: str) -> Connection:
    server = Server(url, get_info=ALL, connect_timeout=3)
    conn = Connection(
        server,
        user=LDAP_SERVICE_DN,
        password=LDAP_API_PASSWORD,
        auto_bind=True,
        receive_timeout=5,
        raise_exceptions=True,
    )
    return conn


def ldap_reachable() -> bool:
    for url in _servers():
        try:
            conn = _bind_admin(url)
            conn.unbind()
            return True
        except Exception:
            continue
    return False


@app.get("/healthz")
def healthz() -> dict:
    if not ldap_reachable():
        raise HTTPException(status_code=503, detail="ldap unreachable")
    return {"ok": True}


@app.post("/api/account")
def create_account(body: AccountIn) -> dict:
    username = body.username.strip()
    if not USER_RE.match(username):
        raise HTTPException(status_code=400, detail="invalid username")
    if username.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=403, detail="username is reserved")
    dn = f"uid={username},{PEOPLE_OU}"
    attrs = {
        "objectClass": ["inetOrgPerson", "organizationalPerson", "person", "top"],
        "uid": username,
        "cn": username,
        "sn": username,
        "mail": body.email.strip(),
        "userPassword": hashed(HASHED_SALTED_SHA256, body.password),
    }
    last_error = "ldap unreachable"
    for url in _servers():
        try:
            conn = _bind_admin(url)
            try:
                added = conn.add(dn, attributes=attrs)
                result = conn.result or {}
            finally:
                conn.unbind()
            if added or result.get("result") == 0:
                return {"username": username}
            if result.get("result") == 68:
                raise HTTPException(status_code=409, detail="username exists")
            last_error = str(result)
        except HTTPException:
            raise
        except LDAPEntryAlreadyExistsResult:
            raise HTTPException(status_code=409, detail="username exists") from None
        except Exception as exc:
            last_error = str(exc)
            continue
    raise HTTPException(status_code=503, detail=last_error)
