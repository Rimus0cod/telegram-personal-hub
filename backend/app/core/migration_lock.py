"""PostgreSQL advisory lock for Alembic migrations.

Only one migration process may run at a time, even if several Compose projects,
migration jobs or backend replicas start together. The lock is session-scoped:
PostgreSQL releases it automatically when the connection closes, and the
context manager also unlocks explicitly on success or failure.
"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Arbitrary application-wide constant. It only has to be stable across workers.
MIGRATION_LOCK_ID = 0x4855424D494752  # "HUBMIGR"


@contextmanager
def migration_lock(connection: Connection):
    """Serialize Alembic runs on PostgreSQL; no-op on other dialects."""
    if connection.dialect.name != "postgresql":
        yield
        return

    connection.execute(
        text("SELECT pg_advisory_lock(:lock_id)"),
        {"lock_id": MIGRATION_LOCK_ID},
    )
    try:
        yield
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
        except Exception:  # noqa: BLE001 - connection close also releases it
            # Never hide the migration error with an unlock failure.
            pass
