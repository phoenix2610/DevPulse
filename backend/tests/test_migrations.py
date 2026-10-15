from alembic import command

from app.db import alembic_config, init_db


def test_migrations_match_models():
    init_db()
    command.check(alembic_config())


def test_init_db_is_idempotent():
    init_db()
    init_db()
