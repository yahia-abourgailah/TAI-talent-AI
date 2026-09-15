"""A removed or invalid record is archived with a reason and the person who did it (B3, BR-205).

    python -m candidates.archive --candidate-id 42 --reason "Test data (Q-13)" --by "<your name>"

Nothing is deleted: the app role has no DELETE grant. An archived candidate stays readable, and
the database refuses to change or clear an archive once it is set.
"""

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.queue import engine_from_environment


class ArchiveError(Exception):
    """The archive was refused: no reason, no actor, no such candidate, or already archived."""


def archive_candidate(conn: Connection, candidate_id: int, reason: str, actor: str) -> None:
    if not reason.strip():
        raise ArchiveError("An archive needs a reason.")
    if not actor.strip():
        raise ArchiveError("An archive needs the person who did it.")
    current = conn.execute(
        text("SELECT archived_at FROM core.candidate WHERE id = :id FOR UPDATE"),
        {"id": candidate_id},
    ).one_or_none()
    if current is None:
        raise ArchiveError(f"No candidate {candidate_id}.")
    if current.archived_at is not None:
        raise ArchiveError(f"Candidate {candidate_id} is already archived.")
    conn.execute(
        text(
            "UPDATE core.candidate SET archived_at = clock_timestamp(), "
            "archived_reason = :reason, archived_by = :actor WHERE id = :id"
        ),
        {"id": candidate_id, "reason": reason.strip(), "actor": actor.strip()},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m candidates.archive")
    parser.add_argument("--candidate-id", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--by", required=True, help="the person archiving the record")
    args = parser.parse_args(argv)
    try:
        with engine_from_environment().begin() as conn:
            archive_candidate(conn, args.candidate_id, args.reason, args.by)
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Archived candidate {args.candidate_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
