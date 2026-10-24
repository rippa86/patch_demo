import os
import socket
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import galera_status, galera_synced, get_db
from app.models import (
    Admin,
    Cart,
    CartItem,
    InventoryLog,
    Order,
    OrderItem,
    Product,
    SessionRow,
    SessionSample,
)

INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "30"))
# Which shop host this backend runs on; the quadlet sets NODE_NAME=%l (short hostname).
NODE_NAME = os.environ.get("NODE_NAME") or socket.gethostname()

router = APIRouter()


def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    if not INTERNAL_TOKEN or x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="internal token required")


class SessionCreate(BaseModel):
    uid: str = Field(min_length=1, max_length=64)


class CartPut(BaseModel):
    uid: str
    items: list[dict]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _live_count(db: Session) -> int:
    now = _now()
    return db.scalar(
        select(func.count(func.distinct(SessionRow.uid))).where(
            SessionRow.revoked_at.is_(None),
            SessionRow.expires_at > now,
        )
    ) or 0


def _sample(db: Session) -> None:
    n = _live_count(db)
    db.add(SessionSample(t=_now(), n=n))
    max_id = db.scalar(select(func.max(SessionSample.id))) or 0
    if max_id > 2000:
        db.execute(delete(SessionSample).where(SessionSample.id <= max_id - 2000))


def _product_out(p: Product) -> dict:
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "description": p.description,
        "price_cents": p.price_cents,
        "stock": p.stock,
    }


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict:
    try:
        status = galera_status(db)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}") from exc
    if not galera_synced(status):
        raise HTTPException(
            status_code=503,
            detail={
                "wsrep_ready": status.get("wsrep_ready"),
                "wsrep_cluster_status": status.get("wsrep_cluster_status"),
                "wsrep_local_state_comment": status.get("wsrep_local_state_comment"),
            },
        )
    return {
        "ok": True,
        "wsrep_cluster_size": status.get("wsrep_cluster_size"),
        "wsrep_local_state_comment": status.get("wsrep_local_state_comment"),
    }


@router.post("/internal/sessions", dependencies=[Depends(require_internal)])
def create_session(body: SessionCreate, db: Session = Depends(get_db)) -> dict:
    now = _now()
    existing = db.scalars(
        select(SessionRow).where(
            SessionRow.uid == body.uid,
            SessionRow.revoked_at.is_(None),
            SessionRow.expires_at > now,
        )
    ).all()
    for row in existing:
        row.revoked_at = now
    sid = str(uuid.uuid4())
    db.add(
        SessionRow(
            sid=sid,
            uid=body.uid,
            issued_at=now,
            expires_at=now + timedelta(minutes=JWT_EXPIRE_MINUTES),
            node=NODE_NAME,
        )
    )
    _sample(db)
    db.commit()
    return {"sid": sid, "uid": body.uid}


@router.get("/internal/sessions/{sid}", dependencies=[Depends(require_internal)])
def get_session(sid: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(SessionRow, sid)
    now = _now()
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= now
    ):
        raise HTTPException(status_code=401, detail="session invalid")
    return {"sid": row.sid, "uid": row.uid}


@router.delete("/internal/sessions/{sid}", dependencies=[Depends(require_internal)])
def revoke_session(sid: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(SessionRow, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row.revoked_at is None:
        row.revoked_at = _now()
        _sample(db)
        db.commit()
    return {"ok": True}


@router.get("/internal/stats/logged-in", dependencies=[Depends(require_internal)])
def logged_in(db: Session = Depends(get_db)) -> dict:
    count = _live_count(db)
    series = [
        {"t": row.t.isoformat() + "Z", "n": row.n}
        for row in db.scalars(
            select(SessionSample).order_by(SessionSample.id.desc()).limit(120)
        ).all()
    ]
    series.reverse()
    return {"count": count, "series": series}


@router.get("/internal/products", dependencies=[Depends(require_internal)])
def list_products(db: Session = Depends(get_db)) -> list[dict]:
    products = db.scalars(select(Product).order_by(Product.id)).all()
    return [_product_out(p) for p in products]


def _get_cart(db: Session, uid: str) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.uid == uid))
    if cart is None:
        cart = Cart(uid=uid)
        db.add(cart)
        db.flush()
    return cart


def _cart_payload(db: Session, uid: str) -> dict:
    cart = db.scalar(
        select(Cart)
        .where(Cart.uid == uid)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    if cart is None:
        return {"uid": uid, "items": []}
    return {
        "uid": uid,
        "items": [
            {
                "product_id": item.product_id,
                "qty": item.qty,
                "name": item.product.name,
                "price_cents": item.product.price_cents,
            }
            for item in cart.items
        ],
    }


@router.get("/internal/cart", dependencies=[Depends(require_internal)])
def get_cart(uid: str, db: Session = Depends(get_db)) -> dict:
    return _cart_payload(db, uid)


@router.put("/internal/cart", dependencies=[Depends(require_internal)])
def put_cart(body: CartPut, db: Session = Depends(get_db)) -> dict:
    cart = _get_cart(db, body.uid)
    cart.items.clear()
    db.flush()
    for raw in body.items:
        product_id = int(raw["product_id"])
        qty = int(raw["qty"])
        if qty < 1:
            continue
        product = db.get(Product, product_id)
        if product is None:
            raise HTTPException(status_code=400, detail=f"unknown product {product_id}")
        cart.items.append(CartItem(product_id=product_id, qty=qty))
    db.commit()
    return _cart_payload(db, body.uid)


@router.post("/internal/orders", dependencies=[Depends(require_internal)])
def checkout(body: SessionCreate, db: Session = Depends(get_db)) -> dict:
    cart = db.scalar(
        select(Cart)
        .where(Cart.uid == body.uid)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    if cart is None or not cart.items:
        raise HTTPException(status_code=400, detail="cart is empty")
    total = 0
    order = Order(uid=body.uid, total_cents=0)
    db.add(order)
    db.flush()
    for item in cart.items:
        product = db.get(Product, item.product_id)
        if product is None or product.stock < item.qty:
            db.rollback()
            raise HTTPException(status_code=409, detail="insufficient stock")
        product.stock -= item.qty
        total += product.price_cents * item.qty
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                qty=item.qty,
                price_cents=product.price_cents,
            )
        )
    order.total_cents = total
    cart.items.clear()
    db.commit()
    return {"order_id": order.id, "total_cents": total}


@router.get("/internal/orders", dependencies=[Depends(require_internal)])
def list_orders(uid: str, db: Session = Depends(get_db)) -> list[dict]:
    orders = db.scalars(
        select(Order)
        .where(Order.uid == uid)
        .options(selectinload(Order.items))
        .order_by(Order.id.desc())
    ).all()
    return [
        {
            "order_id": order.id,
            "created_at": order.created_at.isoformat() + "Z",
            "total_cents": order.total_cents,
            "items": [
                {
                    "product_id": item.product_id,
                    "qty": item.qty,
                    "price_cents": item.price_cents,
                }
                for item in order.items
            ],
        }
        for order in orders
    ]


# --- Admin: role lookup and catalog management --------------------------------


class ProductCreate(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    sku: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    price_cents: int = Field(ge=1, le=10_000_000)
    stock: int = Field(default=0, ge=0, le=1_000_000)


class Restock(BaseModel):
    uid: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1, le=100_000)


def _log(db: Session, product: Product, uid: str, action: str, delta: int) -> None:
    db.add(
        InventoryLog(
            product_id=product.id,
            uid=uid,
            action=action,
            delta=delta,
            stock_after=product.stock,
        )
    )


@router.get("/internal/admins/{uid}", dependencies=[Depends(require_internal)])
def get_admin(uid: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Admin, uid) is None:
        raise HTTPException(status_code=404, detail="not an admin")
    return {"uid": uid, "admin": True}


@router.post("/internal/products", dependencies=[Depends(require_internal)])
def create_product(body: ProductCreate, db: Session = Depends(get_db)) -> dict:
    sku = body.sku.strip().upper()
    if db.scalar(select(Product.id).where(Product.sku == sku)) is not None:
        raise HTTPException(status_code=409, detail=f"sku {sku} already exists")
    product = Product(
        sku=sku,
        name=body.name.strip(),
        description=body.description.strip(),
        price_cents=body.price_cents,
        stock=body.stock,
    )
    db.add(product)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"sku {sku} already exists") from None
    _log(db, product, body.uid, "create", body.stock)
    db.commit()
    return _product_out(product)


@router.post("/internal/products/{product_id}/restock", dependencies=[Depends(require_internal)])
def restock_product(product_id: int, body: Restock, db: Session = Depends(get_db)) -> dict:
    # Single atomic UPDATE so concurrent checkouts on either node are not lost.
    result = db.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(stock=Product.stock + body.qty)
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="product not found")
    product = db.get(Product, product_id, populate_existing=True)
    _log(db, product, body.uid, "restock", body.qty)
    db.commit()
    return _product_out(product)


@router.get("/internal/inventory-log", dependencies=[Depends(require_internal)])
def inventory_log(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(InventoryLog).order_by(InventoryLog.id.desc()).limit(max(1, min(limit, 500)))
    ).all()
    return [
        {
            "id": r.id,
            "product_id": r.product_id,
            "uid": r.uid,
            "action": r.action,
            "delta": r.delta,
            "stock_after": r.stock_after,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]
