"""Alembic environment: uses the application's own settings and models."""
from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.config import settings
from app.database import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
