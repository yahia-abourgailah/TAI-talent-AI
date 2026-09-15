"""A made-up candidate, for trying the pipeline on a dev machine. Refuses outside TALENT_ENV=dev.

    python -m pipeline.dev_candidate --by "<your name>"

Migrated candidates get no applications until OPN-11 is ruled, and there is no intake yet, so this
is how a demo gets a candidate to apply. The candidate holds no personal data at all.
"""

import argparse
import hashlib
import json
import os
import sys
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.queue import engine_from_environment

DEMO_SOURCE = "dev-demo"


def create_demo_candidate(conn: Connection, created_by: str, source: str = DEMO_SOURCE) -> int:
    key = uuid.uuid4().hex
    payload = json.dumps({"made_up": True, "key": key}).encode("utf-8")
    capture_id = conn.execute(
        text(
            """
            INSERT INTO raw.capture
              (source, external_id, content_sha256, blob_key, media_type, byte_size, received_by)
            VALUES (:source, :key, :sha, :blob_key, 'application/json', :size, :by)
            RETURNING id
            """
        ),
        {
            "source": source,
            "key": key,
            "sha": hashlib.sha256(payload).digest(),
            "blob_key": f"{source}/{key}.json",
            "size": len(payload),
            "by": created_by,
        },
    ).scalar_one()
    candidate_id = conn.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
            "VALUES (:capture, :key, :by, 'not_recorded') RETURNING id"
        ),
        {"capture": capture_id, "key": f"{source}:{key}", "by": created_by},
    ).scalar_one()
    return int(candidate_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.dev_candidate")
    parser.add_argument("--by", required=True)
    args = parser.parse_args(argv)
    if os.environ.get("TALENT_ENV") != "dev":
        print("error: made-up candidates are for TALENT_ENV=dev only.", file=sys.stderr)
        return 1
    with engine_from_environment().begin() as conn:
        candidate_id = create_demo_candidate(conn, args.by)
    print(f"Created made-up candidate {candidate_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
