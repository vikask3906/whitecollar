"""
alembic/env.py
──────────────
Alembic migration environment — uses sync SQLAlchemy engine.
Reads SYNC_DATABASE_URL from .env so credentials stay out of alembic.ini.
"""
import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# Load .env so SYNC_DATABASE_URL is available
load_dotenv()

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config

# Override sqlalchemy.url from environment (keeps creds out of alembic.ini)
sync_url = os.getenv("SYNC_DATABASE_URL")
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)

# Interpret alembic.ini logging config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata (enables autogenerate) ─────────────────────────────────────
# Import ALL models so Alembic can detect schema changes
from app.database import Base  # noqa: E402
import app.models  # noqa: E402  (registers all ORM models onto Base)

target_metadata = Base.metadata


# ── Migration functions ────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no DB required)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to live DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
