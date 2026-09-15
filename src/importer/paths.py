"""Where candidate data may be read from and written to: never inside a git repository.

Under Docker the tools see bind mounts such as /data/out, which have no .git above them. So
compose.tools.yaml also passes the host paths and the host repository, and those are checked too.
"""

import os
from pathlib import Path, PurePosixPath
from typing import Any

from jobs.queue import ReportableError
from replay.baseline import inside_git_repository


class DataLocationError(ReportableError):
    """Candidate data was pointed at a place it must not be, or at nothing."""


def inside_host_repository(host_path: str | None) -> bool:
    """True when a host path lies inside the host repository named by TALENT_HOST_REPO."""
    repository = os.environ.get("TALENT_HOST_REPO", "")
    if not repository or not host_path:
        return False
    root = PurePosixPath(os.path.normpath(repository))
    # A relative bind-mount path is resolved by docker compose against the repository.
    target = PurePosixPath(
        os.path.normpath(host_path if os.path.isabs(host_path) else root / host_path)
    )
    return target == root or root in target.parents


def check_outside_repository(path: Path, host_path: str | None, what: str) -> None:
    if inside_git_repository(path) or inside_host_repository(host_path):
        raise DataLocationError(
            f"{what} is inside a git repository. Candidate data must live outside it "
            "(docs/DATA_HANDLING.md)."
        )


def master_path(value: Any) -> Path:
    raw = str(value or os.environ.get("TALENT_MASTER_PATH") or "")
    if not raw:
        raise DataLocationError("Set TALENT_MASTER_PATH or pass --master.")
    master = Path(raw).expanduser()
    host = None if value else os.environ.get("TALENT_HOST_MASTER_PATH")
    check_outside_repository(master, host, master.name)
    return master
