"""Add the per-user idempotency key for Focus sessions.

``focus_sessions.client_id`` is an optional, client-generated UUID.  The unique
index on ``(user_id, client_id)`` is the final guarantee against duplicate
sessions: two users may use the same UUID, and legacy rows keep ``client_id``
as ``NULL`` (NULLs are distinct in both SQLite and PostgreSQL, so many legacy
rows are allowed).

``0001_initial`` builds the schema from the current SQLAlchemy metadata, so a
freshly created database may already contain the column and the index when this
revision runs.  Every step therefore inspects the live database first, like
``0003_nutrition_schema``.  No table or row is ever dropped or rewritten.

The revision is forward-only: downgrading cannot remove the idempotency key
without risking the loss of data that was written after the upgrade, so it is
refused instead of being approximated.
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_focus_session_client_id"
down_revision = "0003_nutrition_schema"
branch_labels = None
depends_on = None


_CLIENT_ID_INDEX = "uq_focus_sessions_user_client"
_CLIENT_ID_COLUMN = "client_id"
_TABLE = "focus_sessions"
_EXPECTED_TYPE = "VARCHAR(36)"
_EXPECTED_COLUMNS = ("user_id", _CLIENT_ID_COLUMN)


def _inspector():
    return sa.inspect(op.get_bind())


def _table_names():
    return set(_inspector().get_table_names())


def _columns(table):
    return {column["name"]: column for column in _inspector().get_columns(table)}


def _index_details(table):
    return {
        index["name"]: (tuple(index.get("column_names") or ()),
                        bool(index.get("unique")))
        for index in _inspector().get_indexes(table)
    }


def _index_owners(name):
    """Return tables owning an index called ``name`` (dialect-aware catalog)."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        rows = bind.execute(sa.text(
            "SELECT tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND name = :name"
        ), {"name": name}).fetchall()
    elif dialect in ("postgresql", "postgres"):
        rows = bind.execute(sa.text(
            "SELECT tablename FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :name"
        ), {"name": name}).fetchall()
    else:
        rows = [(table,) for table in _table_names()
                if name in _index_details(table)]
    return {row[0] for row in rows}


def _check_column(columns):
    column = columns.get(_CLIENT_ID_COLUMN)
    if column is None:
        return False
    declared = str(column["type"]).upper()
    if declared != _EXPECTED_TYPE:
        raise RuntimeError(
            f"Cannot add the Focus idempotency key: {_TABLE}.{_CLIENT_ID_COLUMN} "
            f"already exists with type {declared}, expected {_EXPECTED_TYPE}. "
            "Resolve the column manually; the migration will not rebuild a "
            "column that may already hold user data."
        )
    if not column["nullable"]:
        raise RuntimeError(
            f"Cannot add the Focus idempotency key: {_TABLE}.{_CLIENT_ID_COLUMN} "
            "is NOT NULL, but legacy sessions must keep client_id as NULL. "
            "Resolve the column manually; the migration will not rewrite it."
        )
    return True


def _check_index(has_column):
    """Refuse unsafe index states before any DDL runs."""
    table_exists = _TABLE in _table_names()
    if not table_exists:
        raise RuntimeError(
            f"Cannot add the Focus idempotency key: the {_TABLE} table is "
            "missing. Restore the database from backup or run a clean "
            "`alembic upgrade head` on a new database."
        )

    owners = _index_owners(_CLIENT_ID_INDEX)
    foreign = owners - {_TABLE}
    if foreign:
        raise RuntimeError(
            f"Cannot create the index {_CLIENT_ID_INDEX}: that name is already "
            f"used by another table ({', '.join(sorted(foreign))}). Index names "
            "must be unique in SQLite and within a PostgreSQL schema. Remove or "
            "rename the conflicting index manually; the migration will not "
            "touch unknown indexes."
        )

    details = _index_details(_TABLE)
    if _CLIENT_ID_INDEX in details:
        actual_columns, actual_unique = details[_CLIENT_ID_INDEX]
        if actual_columns != _EXPECTED_COLUMNS or not actual_unique:
            raise RuntimeError(
                f"Cannot reuse the existing index {_CLIENT_ID_INDEX} on "
                f"{_TABLE}: it has columns "
                f"({', '.join(actual_columns) or 'unknown'}) and "
                f"unique={actual_unique}, but the schema expects "
                f"({', '.join(_EXPECTED_COLUMNS)}) and unique=True. Resolve the "
                "index manually; the migration will not delete or rebuild "
                "unknown indexes."
            )
        return

    if not has_column:
        # The column is about to be added with only NULL values, so duplicates
        # cannot exist yet.
        return

    duplicate = op.get_bind().execute(sa.text(
        f"SELECT user_id, {_CLIENT_ID_COLUMN} FROM {_TABLE} "
        f"WHERE {_CLIENT_ID_COLUMN} IS NOT NULL "
        f"GROUP BY user_id, {_CLIENT_ID_COLUMN} "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate is not None:
        raise RuntimeError(
            f"Cannot create the unique index {_CLIENT_ID_INDEX}: duplicate "
            f"(user_id, {_CLIENT_ID_COLUMN}) values already exist. Resolve "
            "them manually or restore a backup; the migration will not delete "
            "or merge sessions."
        )


def upgrade():
    if _TABLE not in _table_names():
        raise RuntimeError(
            f"Cannot add the Focus idempotency key: the {_TABLE} table is "
            "missing. Restore the database from backup or run a clean "
            "`alembic upgrade head` on a new database."
        )

    columns = _columns(_TABLE)
    has_column = _check_column(columns)
    _check_index(has_column)

    if not has_column:
        op.add_column(
            _TABLE,
            sa.Column(_CLIENT_ID_COLUMN, sa.String(length=36), nullable=True),
        )

    if _CLIENT_ID_INDEX not in _index_details(_TABLE):
        op.create_index(
            _CLIENT_ID_INDEX,
            _TABLE,
            list(_EXPECTED_COLUMNS),
            unique=True,
        )


def downgrade():
    raise RuntimeError(
        "0004_focus_session_client_id is a forward-only, data-preserving "
        "migration. Removing the client_id column would delete idempotency keys "
        "that may be required to reconcile sessions written after the upgrade. "
        "Restore a verified database backup instead of downgrading."
    )
