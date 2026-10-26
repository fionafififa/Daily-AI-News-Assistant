"""Run versioned PostgreSQL schema changes using DATABASE_URL."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.database import get_database_url
from app.db_schema import metadata

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

database_url = get_database_url()


def include_name(name, type_, parent_names):
    # Autogenerate manages declared tables only, preserving independent practice tables.
    if type_ == "table":
        return name in metadata.tables
    return True


def run_migrations_offline():
    context.configure(
        url=database_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=metadata,
                include_name=include_name,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
