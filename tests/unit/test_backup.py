"""Backups: where they may be written, what a dump is named, and what is kept (NFR-02).

Nothing here touches a database. The dump-and-restore itself is proved in the integration tests.
"""

import json

import pytest

from importer.paths import DataLocationError
from ops.backup import (
    KEEP_DEFAULT,
    BackupError,
    Server,
    Tools,
    _out_directory,
    listed,
    prune,
)

DSN = "postgresql+psycopg://talent_owner:secret@db.internal:5432/talent"


def test_the_dsn_is_read_as_pg_dump_needs_it():
    server = Server(DSN)
    assert (server.host, server.port, server.user, server.database) == (
        "db.internal",
        "5432",
        "talent_owner",
        "talent",
    )
    assert server.password == "secret"
    # Inside the container the server is local, whatever the host in the DSN says.
    assert server.where(inside_container=True)[:2] == ["--host", "127.0.0.1"]
    assert server.where(inside_container=False)[:2] == ["--host", "db.internal"]


@pytest.mark.parametrize("dsn", ["mysql://x/y", "postgresql:///talent", "not a url"])
def test_a_dsn_that_is_not_a_server_is_refused(dsn):
    with pytest.raises(BackupError):
        Server(dsn)


def test_the_tools_run_where_the_server_is():
    assert Tools("local").command("pg_dump", "--help") == ["pg_dump", "--help"]
    assert Tools("docker:postgres").command("pg_dump") == [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "pg_dump",
    ]
    with pytest.raises(BackupError):
        Tools("kubernetes")


def test_a_backup_directory_inside_the_repository_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("TALENT_HOST_BACKUP_DIR", raising=False)
    inside = tmp_path / "repo" / "backups"
    inside.mkdir(parents=True)
    (tmp_path / "repo" / ".git").mkdir()
    with pytest.raises(DataLocationError):
        _out_directory(str(inside))
    assert _out_directory(str(tmp_path / "outside")).name == "outside"


def test_no_directory_at_all_says_what_to_set(monkeypatch):
    monkeypatch.delenv("TALENT_BACKUP_DIR", raising=False)
    with pytest.raises(BackupError, match="TALENT_BACKUP_DIR"):
        _out_directory(None)


def _backup(directory, name: str, restored: bool = True) -> None:
    (directory / f"{name}.dump").write_bytes(b"not a real dump")
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "alembic_revision": "0013",
                "row_counts": {"core.candidate": 5140},
                "restored": {"ok": restored},
            }
        ),
        encoding="utf-8",
    )


def test_old_backups_are_pruned_newest_first(tmp_path):
    for day in range(5):
        _backup(tmp_path, f"talent-2026091{day}T000000Z")
    removed = prune(tmp_path, keep=2)
    assert removed == [
        "talent-20260912T000000Z.dump",
        "talent-20260911T000000Z.dump",
        "talent-20260910T000000Z.dump",
    ]
    assert sorted(p.name for p in tmp_path.glob("*.dump")) == [
        "talent-20260913T000000Z.dump",
        "talent-20260914T000000Z.dump",
    ]
    # The manifest goes with its dump.
    assert not (tmp_path / "talent-20260910T000000Z.json").exists()


def test_keeping_none_is_refused(tmp_path):
    with pytest.raises(BackupError):
        prune(tmp_path, keep=0)


def test_the_listing_says_which_backups_were_restored(tmp_path):
    _backup(tmp_path, "talent-20260915T000000Z", restored=True)
    _backup(tmp_path, "talent-20260916T000000Z", restored=False)
    rows = listed(tmp_path)
    assert [row["file"] for row in rows] == [
        "talent-20260916T000000Z.dump",
        "talent-20260915T000000Z.dump",
    ]
    assert [row["restored"] for row in rows] == [False, True]
    assert rows[0]["alembic_revision"] == "0013"
    assert rows[0]["rows"] == 5140
    assert KEEP_DEFAULT >= 7  # a fortnight of nights, so a bad week is survivable
