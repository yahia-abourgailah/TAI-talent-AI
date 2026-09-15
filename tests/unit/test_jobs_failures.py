"""What a failed run may keep, and where data folders may be. No database; made-up data only."""

import pytest

from importer.paths import inside_host_repository
from jobs.queue import ReportableError, describe_failure


class _Original:
    sqlstate = "23514"


class FakeDatabaseError(Exception):
    def __init__(self) -> None:
        super().__init__("Failing row contains (Fake Person, fake@example.com)")
        self.orig = _Original()


def test_a_database_error_keeps_its_kind_and_code_but_never_the_row():
    described = describe_failure(FakeDatabaseError())
    assert described == "FakeDatabaseError (SQLSTATE 23514)"
    assert "Fake Person" not in described


def test_any_other_error_keeps_only_its_kind():
    assert describe_failure(ValueError("could not convert 'Fake Person'")) == "ValueError"


def test_a_reportable_error_keeps_its_message():
    described = describe_failure(ReportableError("The sheet is missing columns: Stage."))
    assert described == "ReportableError: The sheet is missing columns: Stage."


@pytest.mark.parametrize(
    ("path", "inside"),
    [
        ("/home/dev/repo", True),
        ("/home/dev/repo/data/out", True),
        ("data/out", True),
        ("/home/dev/repository", False),
        ("/home/dev/TAI-data/out", False),
        ("", False),
    ],
)
def test_host_paths_inside_the_repository_are_found(monkeypatch, path, inside):
    monkeypatch.setenv("TALENT_HOST_REPO", "/home/dev/repo")
    assert inside_host_repository(path) is inside


def test_without_a_host_repository_nothing_is_refused():
    assert inside_host_repository("/anywhere") is False
