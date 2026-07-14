import asyncio

from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app import models  # noqa: F401  (register mappings on Base.metadata)
from app.config import settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # -x db_url=... overrides (used by tooling/tests); default is the app's URL.
    return context.get_x_argument(as_dictionary=True).get("db_url", settings.database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        # init_db passes its own (sync-wrapped) connection so migrations share
        # the startup transaction and advisory lock.
        _do_run_migrations(connectable)
        return
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
