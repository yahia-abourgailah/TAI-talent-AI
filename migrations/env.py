"""Migrations run as the schema owner (TALENT_DB_MIGRATION_DSN), never as the app role."""

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _owner_dsn() -> str:
    dsn = os.environ.get("TALENT_DB_MIGRATION_DSN")
    if not dsn:
        raise SystemExit("TALENT_DB_MIGRATION_DSN is not set. Migrations run as the owner role.")
    return dsn


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported. Run against a database.")

engine = create_engine(_owner_dsn(), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
