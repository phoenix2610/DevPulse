from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

_settings = get_settings()
_kwargs = {"connect_args": {"check_same_thread": False}} if _settings.database_url.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(_settings.database_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION_LOCK = 724_731


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def init_db() -> None:
    from alembic import command

    from . import models  # noqa: F401  (register tables)

    cfg = alembic_config()
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
