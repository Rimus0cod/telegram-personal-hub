"""Real PostgreSQL 16 backup/restore tests for scripts/*.sh.

Gated by TEST_POSTGRES_ADMIN_URL, TEST_PG_BIN and a POSIX bash. Only temporary
databases whose names are generated here are created or dropped.
"""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.models import Note, User

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"
EXPECTED_HEAD = "0004_focus_session_client_id"


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    # Git Bash (MSYS2) uses /c/... rather than Cygwin's /cygdrive/c/....
    return "/" + resolved.drive[0].lower() + resolved.as_posix()[2:]


def _database_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def pg_tools():
    admin = os.environ.get("TEST_POSTGRES_ADMIN_URL")
    pg_bin = os.environ.get("TEST_PG_BIN")
    bash = shutil.which("bash")
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.exists():
            bash = str(git_bash)
    if not admin or not pg_bin or not bash:
        pytest.skip("TEST_POSTGRES_ADMIN_URL, TEST_PG_BIN and bash are required")

    bin_path = Path(pg_bin)
    if not bin_path.is_dir():
        pytest.skip("TEST_PG_BIN is not a directory")
    for tool in ("pg_dump", "pg_restore", "psql", "createdb"):
        if not (bin_path / tool).exists() and not (bin_path / f"{tool}.exe").exists():
            pytest.skip(f"{tool} is missing from TEST_PG_BIN")

    return {
        "admin_url": make_url(admin),
        "pg_bin": bin_path,
        "bash": bash,
    }


@pytest.fixture(scope="module")
def admin_engine(pg_tools):
    engine = create_engine(str(pg_tools["admin_url"]))
    try:
        yield engine
    finally:
        engine.dispose()


def _admin_execute(engine, statement: str):
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(statement))


def _database_exists(engine, name: str) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name},
            ).scalar()
            is not None
        )


def _drop_database(engine, name: str):
    if _database_exists(engine, name):
        _admin_execute(engine, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _script_env(pg_tools, database: str, extra=None):
    url = pg_tools["admin_url"]
    env = os.environ.copy()
    pg_bin = _bash_path(pg_tools["pg_bin"])
    if os.name == "nt":
        env["PATH"] = f"{pg_bin}:/usr/bin:/bin:{env.get('PATH', '')}"
    else:
        env["PATH"] = f"{pg_bin}:{env.get('PATH', '')}"
    env.update(
        {
            "PGHOST": url.host or "127.0.0.1",
            "PGPORT": str(url.port or 5432),
            "PGUSER": url.username or "postgres",
            "PGDATABASE": database,
            "PGSSLMODE": "prefer",
        }
    )
    if url.password:
        env["PGPASSWORD"] = url.password
    if extra:
        env.update(extra)
    return env


def _run_script(pg_tools, script: str, args, env):
    return subprocess.run(
        [pg_tools["bash"], _bash_path(SCRIPTS / script), *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def _migrate(pg_tools, database_url, revision="head"):
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", revision],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def source_database(pg_tools, admin_engine, tmp_path_factory):
    name = _database_name("hub_54c_src")
    _admin_execute(admin_engine, f'CREATE DATABASE "{name}"')
    source_url = str(pg_tools["admin_url"].set(database=name))
    try:
        _migrate(pg_tools, source_url)
        engine = create_engine(source_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        user = User(telegram_id=910_540_001, first_name="BackupSentinel")
        session.add(user)
        session.flush()
        session.add(
            Note(
                user_id=user.id,
                title="sentinel-note",
                content="backup/restore sentinel",
            )
        )
        session.commit()
        session.close()
        engine.dispose()
        yield {
            "name": name,
            "url": source_url,
            "backup_dir": tmp_path_factory.mktemp("hub-54c-backups"),
        }
    finally:
        _drop_database(admin_engine, name)


@pytest.fixture(scope="module")
def backup_file(pg_tools, source_database):
    env = _script_env(
        pg_tools,
        source_database["name"],
        {"BACKUP_DIR": _bash_path(source_database["backup_dir"])},
    )
    result = _run_script(pg_tools, "backup-postgres.sh", [], env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    dumps = list(source_database["backup_dir"].glob("*.dump"))
    assert len(dumps) == 1, dumps
    return dumps[0]


def _assert_restored(pg_tools, target_name: str):
    target_url = str(pg_tools["admin_url"].set(database=target_name))
    engine = create_engine(target_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM users WHERE first_name = 'BackupSentinel'")
            ).scalar() == 1
            assert connection.execute(
                text("SELECT count(*) FROM notes WHERE title = 'sentinel-note'")
            ).scalar() == 1
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert head == EXPECTED_HEAD
            indexes = connection.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname IN "
                    "('ix_users_telegram_id','ix_notes_user_updated','ix_tasks_user_completed',"
                    "'ix_focus_user_finished','uq_focus_sessions_user_client','ix_meals_user_day',"
                    "'ix_recipes_user_name','ix_community_foods_barcode')"
                )
            ).scalar()
            assert indexes == 8
    finally:
        engine.dispose()


def test_backup_is_non_empty_and_readable(pg_tools, backup_file):
    assert backup_file.is_file()
    assert backup_file.stat().st_size > 0
    env = _script_env(pg_tools, "postgres")
    result = subprocess.run(
        [pg_tools["bash"], "-c", f"pg_restore --list {_bash_path(backup_file)} >/dev/null"],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_restore_into_new_database_preserves_data(
    pg_tools, admin_engine, source_database, backup_file
):
    target = _database_name("hub_54c_dst")
    env = _script_env(
        pg_tools,
        source_database["name"],
        {
            "RESTORE_CONFIRM": target,
            "EXPECTED_ALEMBIC_HEAD": EXPECTED_HEAD,
            "PGMAINTENANCE_DB": "postgres",
        },
    )
    try:
        result = _run_script(
            pg_tools,
            "restore-postgres.sh",
            [_bash_path(backup_file), target],
            env,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert _database_exists(admin_engine, target)
        _assert_restored(pg_tools, target)

        # The source database is untouched.
        source_engine = create_engine(source_database["url"])
        with source_engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM users WHERE first_name = 'BackupSentinel'")
            ).scalar() == 1
        source_engine.dispose()
    finally:
        _drop_database(admin_engine, target)


def test_restore_refuses_to_touch_an_existing_database(
    pg_tools, admin_engine, source_database, backup_file
):
    target = _database_name("hub_54c_existing")
    env = _script_env(
        pg_tools,
        source_database["name"],
        {
            "RESTORE_CONFIRM": target,
            "EXPECTED_ALEMBIC_HEAD": EXPECTED_HEAD,
        },
    )
    try:
        first = _run_script(
            pg_tools, "restore-postgres.sh", [_bash_path(backup_file), target], env
        )
        assert first.returncode == 0, first.stderr

        second = _run_script(
            pg_tools, "restore-postgres.sh", [_bash_path(backup_file), target], env
        )
        assert second.returncode != 0
        assert "already exists" in (second.stdout + second.stderr)

        # Existing target data is still readable and unchanged.
        _assert_restored(pg_tools, target)
    finally:
        _drop_database(admin_engine, target)


def test_restore_requires_confirmation_and_blocks_production_targets(
    pg_tools, admin_engine, source_database, backup_file
):
    target = _database_name("hub_54c_noconfirm")
    env = _script_env(pg_tools, source_database["name"])
    env.pop("RESTORE_CONFIRM", None)
    result = _run_script(
        pg_tools, "restore-postgres.sh", [_bash_path(backup_file), target], env
    )
    assert result.returncode != 0
    assert not _database_exists(admin_engine, target)

    production = "production"
    env = _script_env(
        pg_tools,
        source_database["name"],
        {"RESTORE_CONFIRM": production},
    )
    result = _run_script(
        pg_tools, "restore-postgres.sh", [_bash_path(backup_file), production], env
    )
    assert result.returncode != 0
    assert "production-like" in (result.stdout + result.stderr)
    assert not _database_exists(admin_engine, production)


def test_corrupt_and_empty_backups_are_rejected(
    pg_tools, admin_engine, source_database, tmp_path
):
    corrupt = tmp_path / "corrupt.dump"
    corrupt.write_bytes(b"this is not a custom-format dump")
    empty = tmp_path / "empty.dump"
    empty.write_bytes(b"")

    for index, backup in enumerate((corrupt, empty)):
        target = _database_name(f"hub_54c_bad{index}")
        env = _script_env(
            pg_tools,
            source_database["name"],
            {"RESTORE_CONFIRM": target},
        )
        result = _run_script(
            pg_tools, "restore-postgres.sh", [_bash_path(backup), target], env
        )
        assert result.returncode != 0
        assert not _database_exists(admin_engine, target)


def test_scripts_do_not_contain_automatic_drop_commands():
    restore_text = (SCRIPTS / "restore-postgres.sh").read_text(encoding="utf-8").lower()
    backup_text = (SCRIPTS / "backup-postgres.sh").read_text(encoding="utf-8").lower()
    restore = "\n".join(
        line for line in restore_text.splitlines() if not line.lstrip().startswith("#")
    )
    backup = "\n".join(
        line for line in backup_text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "dropdb" not in restore
    assert "drop database" not in restore
    assert "alembic upgrade" not in restore
    assert "alembic downgrade" not in restore
    assert "truncate" not in restore
    assert "delete from" not in restore
    assert "echo $database_url" not in backup
    assert "echo $database_url" not in restore
    assert "pgpassword=" not in backup
    assert "pgpassword=" not in restore


# ---------------------------------------------------------------------------
# Schema-aware verification across supported revisions.
# ---------------------------------------------------------------------------

REVISION_0002 = "0002_add_last_city"
REVISION_0003 = "0003_nutrition_schema"
REVISION_0004 = "0004_focus_session_client_id"

CORE_0002_INDEXES = (
    "ix_users_telegram_id",
    "ix_notes_user_updated",
    "ix_tasks_user_completed",
    "ix_focus_user_finished",
)
CORE_0003_INDEXES = CORE_0002_INDEXES + (
    "ix_meals_user_day",
    "ix_recipes_user_name",
    "ix_community_foods_barcode",
)
CORE_0004_INDEXES = CORE_0003_INDEXES + ("uq_focus_sessions_user_client",)


def _execute_sql(url, statement):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _create_source(pg_tools, admin_engine, prefix, revision):
    name = _database_name(prefix)
    _admin_execute(admin_engine, f'CREATE DATABASE "{name}"')
    url = str(pg_tools["admin_url"].set(database=name))
    _migrate(pg_tools, url, revision)
    return name, url


def _dump_database(pg_tools, database, tmp_path, prefix):
    backup_dir = tmp_path / f"{prefix}_{uuid.uuid4().hex[:8]}"
    backup_dir.mkdir()
    env = _script_env(pg_tools, database, {"BACKUP_DIR": _bash_path(backup_dir)})
    result = _run_script(pg_tools, "backup-postgres.sh", [], env)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    dumps = list(backup_dir.glob("*.dump"))
    assert len(dumps) == 1, dumps
    return dumps[0]


def _restore_with(pg_tools, backup, target, expected=None):
    extra = {
        "RESTORE_CONFIRM": target,
        "PGMAINTENANCE_DB": "postgres",
    }
    if expected is not None:
        extra["EXPECTED_ALEMBIC_HEAD"] = expected
    env = _script_env(pg_tools, "postgres", extra)
    return _run_script(
        pg_tools,
        "restore-postgres.sh",
        [_bash_path(backup), target],
        env,
    )


def _index_count(url, names):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname = ANY(:names)"
                ),
                {"names": list(names)},
            ).scalar()
    finally:
        engine.dispose()


def _restored_head(url):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("revision", "expected_indexes"),
    [
        (REVISION_0002, CORE_0002_INDEXES),
        (REVISION_0003, CORE_0003_INDEXES),
        (REVISION_0004, CORE_0004_INDEXES),
    ],
)
def test_restore_accepts_each_supported_revision(
    pg_tools, admin_engine, tmp_path, revision, expected_indexes
):
    source, source_url = _create_source(
        pg_tools, admin_engine, "hub_55a_src", revision
    )
    target = _database_name("hub_55a_dst")
    try:
        if revision != REVISION_0004:
            # A pre-0004 backup must not be required to contain the 0004 index.
            _execute_sql(source_url, "DROP INDEX IF EXISTS uq_focus_sessions_user_client")
        backup = _dump_database(pg_tools, source, tmp_path, revision)
        result = _restore_with(pg_tools, backup, target, revision)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        target_url = str(pg_tools["admin_url"].set(database=target))
        assert _restored_head(target_url) == revision
        assert _index_count(target_url, expected_indexes) == len(expected_indexes)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_rejects_unknown_revision(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_unknown", REVISION_0004)
    target = _database_name("hub_55a_unknown_dst")
    try:
        _execute_sql(source_url, "UPDATE alembic_version SET version_num='9999_unknown'")
        backup = _dump_database(pg_tools, source, tmp_path, "unknown")
        result = _restore_with(pg_tools, backup, target)
        assert result.returncode != 0
        assert "unsupported Alembic revision" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_rejects_missing_revision(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_missing", REVISION_0004)
    target = _database_name("hub_55a_missing_dst")
    try:
        _execute_sql(source_url, "DELETE FROM alembic_version")
        backup = _dump_database(pg_tools, source, tmp_path, "missing")
        result = _restore_with(pg_tools, backup, target)
        assert result.returncode != 0
        assert "revision is missing" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_rejects_ambiguous_revision(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_ambiguous", REVISION_0004)
    target = _database_name("hub_55a_ambiguous_dst")
    try:
        _execute_sql(
            source_url,
            "INSERT INTO alembic_version (version_num) VALUES ('0002_add_last_city')",
        )
        backup = _dump_database(pg_tools, source, tmp_path, "ambiguous")
        result = _restore_with(pg_tools, backup, target)
        assert result.returncode != 0
        assert "exactly one Alembic revision" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_rejects_missing_required_index(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_noindex", REVISION_0004)
    target = _database_name("hub_55a_noindex_dst")
    try:
        _execute_sql(source_url, "DROP INDEX uq_focus_sessions_user_client")
        backup = _dump_database(pg_tools, source, tmp_path, "noindex")
        result = _restore_with(pg_tools, backup, target, REVISION_0004)
        assert result.returncode != 0
        assert "missing required index" in (result.stdout + result.stderr)
        assert "uq_focus_sessions_user_client" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_rejects_missing_required_table(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_notable", REVISION_0004)
    target = _database_name("hub_55a_notable_dst")
    try:
        _execute_sql(source_url, "DROP TABLE community_foods")
        backup = _dump_database(pg_tools, source, tmp_path, "notable")
        result = _restore_with(pg_tools, backup, target, REVISION_0004)
        assert result.returncode != 0
        assert "missing required table" in (result.stdout + result.stderr)
        assert "community_foods" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)


def test_restore_0002_rejects_missing_base_index(
    pg_tools, admin_engine, tmp_path
):
    source, source_url = _create_source(pg_tools, admin_engine, "hub_55a_0002_noindex", REVISION_0002)
    target = _database_name("hub_55a_0002_noindex_dst")
    try:
        _execute_sql(source_url, "DROP INDEX ix_notes_user_updated")
        backup = _dump_database(pg_tools, source, tmp_path, "0002-noindex")
        result = _restore_with(pg_tools, backup, target, REVISION_0002)
        assert result.returncode != 0
        assert "missing required index" in (result.stdout + result.stderr)
        assert "ix_notes_user_updated" in (result.stdout + result.stderr)
    finally:
        _drop_database(admin_engine, target)
        _drop_database(admin_engine, source)
