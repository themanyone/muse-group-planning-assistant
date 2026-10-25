"""Alembic environment — wired to the app's own models and settings.

The migration path and the dev ``create_all`` path read the SAME
metadata (gpa.db.Base), so the schema can never drift between them.
The database URL comes from GPA_DATABASE_URL (via gpa.config), never
from a second config file that could disagree with the app — with
one exception: a programmatic run may hand an explicit URL through
``config.attributes["database_url"]`` (the B-152 data mover promotes
a deployment by migrating a DIFFERENT database than the process's
own; the attribute is Alembic's channel for exactly that).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from gpa.config import get_settings
from gpa.db import Base

# Importing models registers every table on Base.metadata.
from gpa import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False is load-bearing: the
    # default (True) silences every logger the host process
    # already configured — an application that runs Alembic
    # in-process (the B-152 mover does) would lose its access
    # log from that moment on. Alembic's ini configures
    # Alembic's loggers; it does not own the process's.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option(
    "sqlalchemy.url",
    config.attributes.get("database_url") or get_settings().database_url,
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live database (for reviewable scripts)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured database."""
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
