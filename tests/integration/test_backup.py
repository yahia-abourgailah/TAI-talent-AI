"""A backup is taken, restored for real, and counted back (NFR-02).

These run against the same database as the other integration tests, and restore into a throwaway
database of their own, which they drop again. They need pg_dump and pg_restore of the server's
version: set TALENT_BACKUP_TOOLS=docker:postgres for the compose stack, or leave it at "local"
where the client tools are installed.
"""

import os
import shutil

import pytest

from ops.backup import (
    BackupError,
    Server,
    Tools,
    alembic_revision,
    check,
    check_fresh,
    listed,
    take,
)


@pytest.fixture(scope="module")
def server_and_tools():
    dsn = os.environ.get("TALENT_TEST_OWNER_DSN")
    if not dsn:
        pytest.skip("TALENT_TEST_OWNER_DSN is not set; backups need a database to dump")
    tools = Tools(os.environ.get("TALENT_BACKUP_TOOLS", "local"))
    server = Server(dsn)
    try:
        tools.run("pg_dump", "--version")
    except BackupError as exc:
        pytest.skip(f"pg_dump is not runnable here ({exc}); set TALENT_BACKUP_TOOLS")
    return server, tools


def test_a_backup_is_taken_restored_and_counted_back(server_and_tools, tmp_path):
    server, tools = server_and_tools
    manifest = take(server, tools, tmp_path, keep=2)

    dump = tmp_path / manifest["file"]
    assert dump.exists()
    assert manifest["bytes"] > 0
    assert dump.stat().st_mode & 0o077 == 0  # nobody else on the machine can read it
    assert manifest["alembic_revision"] == alembic_revision(server, tools)
    assert manifest["row_counts"]["core.candidate"] > 0

    restored = manifest["restored"]
    assert restored["ok"] is True
    assert restored["tables"] == len(manifest["row_counts"])
    assert restored["rows"] > 0
    assert restored["alembic_revision"] == manifest["alembic_revision"]
    assert restored["tables_missing"] == [] and restored["tables_empty"] == []
    # The check cleans up after itself.
    databases = tools.run(
        "psql",
        *server.where(tools.where.startswith("docker:")),
        "--dbname",
        "postgres",
        "--no-align",
        "--tuples-only",
        "--command",
        "SELECT datname FROM pg_database",
        password=server.password,
    ).decode()
    assert restored["restored_into"] not in databases.split()

    assert listed(tmp_path)[0]["restored"] is True


def test_a_dump_that_is_not_a_dump_fails_the_check(server_and_tools, tmp_path):
    server, tools = server_and_tools
    broken = tmp_path / "talent-20260101T000000Z.dump"
    broken.write_bytes(b"this is not a dump at all")
    with pytest.raises(BackupError):
        check(server, tools, broken)


def test_a_backup_stands_on_its_own_in_an_empty_machine(server_and_tools, tmp_path):
    """The restore that matters: a machine with nothing on it but Postgres.

    The same-server check cannot catch a dump that leans on what is already there — the roles, the
    grants, the extensions. This one starts from nothing, follows the runbook's own procedure, and
    counts the grants as well as the rows: data with no grants is a platform that cannot read
    itself.
    """
    server, tools = server_and_tools
    if not shutil.which("docker"):
        pytest.skip("docker is not available here; the empty-machine restore needs it")
    manifest = take(server, tools, tmp_path, keep=1, verify=False)

    fresh = check_fresh(tmp_path / manifest["file"])
    assert fresh["ok"] is True
    assert fresh["candidates"] > 0
    assert fresh["alembic_revision"] == manifest["alembic_revision"]
    assert fresh["grants_to_the_app_role"] > 0
    assert fresh["app_can_read_candidates"] is True


def test_a_dump_without_grants_is_reported_as_not_standing_on_its_own(server_and_tools, tmp_path):
    """What this check exists to catch, proved by making it happen on purpose."""
    server, tools = server_and_tools
    if not shutil.which("docker"):
        pytest.skip("docker is not available here; the empty-machine restore needs it")
    stripped = tools.run(
        "pg_dump",
        *server.where(tools.where.startswith("docker:")),
        "--dbname",
        server.database,
        "--format",
        "custom",
        "--no-owner",
        "--no-privileges",
        password=server.password,
    )
    path = tmp_path / "talent-20260101T000000Z.dump"
    path.write_bytes(stripped)
    with pytest.raises(BackupError, match="does not stand on its own"):
        check_fresh(path)
