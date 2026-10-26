import logging
import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Admin, Base, Product

log = logging.getLogger("webstore.db")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "mysql+pymysql://webstore:webstore@mariadb:3306/webstore"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    pool_size=10,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Comma-separated LDAP uids that get the admin role at startup.
ADMIN_USERS = [
    u.strip() for u in os.environ.get("ADMIN_USERS", "admin").split(",") if u.strip()
]

SEED_PRODUCTS = [
    {
        "sku": "CEDAR-MUG",
        "name": "Cedar travel mug",
        "description": "Double-wall steel mug, 350 ml. One of three items in the lab catalog.",
        "price_cents": 2400,
        "stock": 50,
    },
    {
        "sku": "CANVAS-TOTE",
        "name": "Canvas tote",
        "description": "Heavy canvas tote with a short strap. Second of three catalog items.",
        "price_cents": 1800,
        "stock": 50,
    },
    {
        "sku": "FIELD-NOTE",
        "name": "Field notebook",
        "description": "A6 ruled notebook, 96 pages. Third of three catalog items.",
        "price_cents": 900,
        "stock": 50,
    },
]


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def galera_status(db: Session) -> dict[str, str]:
    rows = db.execute(text("SHOW STATUS LIKE 'wsrep%'")).all()
    return {name: value for name, value in rows}


def galera_synced(status: dict[str, str]) -> bool:
    return (
        status.get("wsrep_ready") == "ON"
        and status.get("wsrep_cluster_status") == "Primary"
        and status.get("wsrep_local_state_comment") == "Synced"
    )


def _add_missing_columns() -> None:
    """create_all() only creates new tables; add columns new code expects.

    Both backends may start together, so a "duplicate column" race is ignored.
    On Galera, ALTER TABLE replicates to every node (TOI).
    """
    wanted = {
        "sessions": [("node", "VARCHAR(64) NULL")],
    }
    insp = inspect(engine)
    for table, columns in wanted.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in columns:
            if name in have:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    conn.execute(text(f"CREATE INDEX ix_{table}_{name} ON {table} ({name})"))
                log.info("added column %s.%s", table, name)
            except DBAPIError as exc:
                log.warning("add column %s.%s skipped: %s", table, name, exc.orig)


def _seed_admins() -> None:
    for uid in ADMIN_USERS:
        with SessionLocal() as db:
            if db.get(Admin, uid) is not None:
                continue
            db.add(Admin(uid=uid))
            try:
                db.commit()
                log.info("granted admin role to %s", uid)
            except (IntegrityError, DBAPIError):
                db.rollback()  # the other backend seeded it first


def init_schema() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _seed_admins()
    with SessionLocal() as db:
        count = db.scalar(select(Product.id).limit(1))
        if count is None:
            for item in SEED_PRODUCTS:
                db.add(Product(**item))
            db.commit()
