"""Admin API: catalog management for users in the `admins` table.

Auth is the same JWT the shop login issues. Every call re-checks that the
session is live and that the user is still in `admins`, so removing a row
revokes admin rights immediately.
"""
import os

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8001")
PEER_BACKEND_URL = os.environ.get("PEER_BACKEND_URL", "")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")

app = FastAPI(title="webstore-api-admin")


class ProductIn(BaseModel):
    sku: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    price_cents: int = Field(ge=1, le=10_000_000)
    stock: int = Field(default=0, ge=0, le=1_000_000)


class RestockIn(BaseModel):
    qty: int = Field(ge=1, le=100_000)


def _backend_urls() -> list[str]:
    urls = [BACKEND_URL]
    if PEER_BACKEND_URL and PEER_BACKEND_URL not in urls:
        urls.append(PEER_BACKEND_URL)
    return urls


def backend_json(
    method: str, path: str, json: dict | None = None, params: dict | None = None
) -> httpx.Response:
    headers = {"X-Internal-Token": INTERNAL_TOKEN}
    last: Exception | None = None
    for base in _backend_urls():
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.request(
                    method, f"{base}{path}", json=json, params=params, headers=headers
                )
            if response.status_code < 500:
                return response
            last = RuntimeError(response.text)
        except Exception as exc:
            last = exc
            continue
    raise HTTPException(status_code=503, detail=f"backend unreachable: {last}")


def _passthrough(response: httpx.Response) -> dict | list:
    if response.status_code == 200:
        return response.json()
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise HTTPException(status_code=response.status_code, detail=detail)


def require_admin(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    uid, sid = payload.get("sub"), payload.get("sid")
    if not uid or not sid:
        raise HTTPException(status_code=401, detail="token missing claims")
    session = backend_json("GET", f"/internal/sessions/{sid}")
    if session.status_code != 200 or session.json().get("uid") != uid:
        raise HTTPException(status_code=401, detail="session invalid")
    role = backend_json("GET", f"/internal/admins/{uid}")
    if role.status_code != 200:
        raise HTTPException(status_code=403, detail="admin only")
    return uid


@app.get("/healthz")
def healthz() -> dict:
    response = backend_json("GET", "/healthz")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="backend unhealthy")
    return {"ok": True}


@app.get("/api/admin/me")
def me(uid: str = Depends(require_admin)) -> dict:
    return {"uid": uid, "admin": True}


@app.get("/api/admin/products")
def products(_uid: str = Depends(require_admin)) -> list:
    return _passthrough(backend_json("GET", "/internal/products"))


@app.post("/api/admin/products")
def create_product(body: ProductIn, uid: str = Depends(require_admin)) -> dict:
    return _passthrough(
        backend_json("POST", "/internal/products", json={"uid": uid, **body.model_dump()})
    )


@app.post("/api/admin/products/{product_id}/restock")
def restock(product_id: int, body: RestockIn, uid: str = Depends(require_admin)) -> dict:
    return _passthrough(
        backend_json(
            "POST",
            f"/internal/products/{product_id}/restock",
            json={"uid": uid, "qty": body.qty},
        )
    )


@app.get("/api/admin/inventory-log")
def inventory_log(limit: int = 50, _uid: str = Depends(require_admin)) -> list:
    return _passthrough(backend_json("GET", "/internal/inventory-log", params={"limit": limit}))
