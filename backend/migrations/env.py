from alembic import context
from sqlalchemy import text

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base, engine

target_metadata = Base.metadata


def _run(conn) -> None:
    if conn.dialect.name == "postgresql":
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=conn.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif (conn := context.config.attributes.get("connection")) is not None:
    _run(conn)
else:
    with engine.begin() as conn:
        _run(conn)
