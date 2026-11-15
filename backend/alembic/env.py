from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.database import Base
from app.core.config import DATABASE_URL
from app.core.migration_lock import migration_lock
from app.models import models  # noqa: F401

config = context.config
# ConfigParser interprets ``%`` in option values, while DATABASE_URL may contain
# percent-encoded passwords or connection options. Escape it for the ini file;
# ConfigParser returns the original value to engine_from_config().
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
if config.config_file_name: fileConfig(config.config_file_name)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle":"named"})
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    # Use a dedicated connection for the advisory lock so the lock's implicit
    # transaction cannot interfere with Alembic's migration transaction.
    with connectable.connect() as lock_connection:
        with migration_lock(lock_connection):
            with connectable.connect() as connection:
                context.configure(connection=connection, target_metadata=target_metadata)
                with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
