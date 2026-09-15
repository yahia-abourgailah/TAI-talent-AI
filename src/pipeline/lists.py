"""Loads a step list, its allowed moves and rejection reasons, and puts it in force (BR-402).

    python -m pipeline.lists load LIST.json --by "<your name>"
    python -m pipeline.lists show

This is how TA's final list arrives: as data, with no code change. A list file looks like this:

    {
      "version": "ta-2026-10",
      "provisional": false,
      "source": "TA stage list, received 2026-10-01",
      "steps": [
        {"code": "new", "label": "New"},
        {"code": "hired", "label": "Hired", "outcome": "hired"},
        {"code": "rejected", "label": "Rejected", "outcome": "rejected"}
      ],
      "moves": [["new", "hired"], ["new", "rejected"]],
      "rejection_reasons": [{"code": "withdrew", "label": "Candidate withdrew"}]
    }

Steps are in order; the first is where every new application starts. The database checks the
list: codes, one hired and one rejected step, no move out of a final step, at least one reason.
A loaded list is never changed. A correction is a new version.
"""

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.queue import engine_from_environment
from pipeline.access import PipelineError, Refused, run
from pipeline.store import active_step_list


def load_step_list(conn: Connection, document: Mapping[str, Any], loaded_by: str) -> str:
    try:
        version = str(document["version"])
        steps = list(document["steps"])
        moves = [(str(a), str(b)) for a, b in document["moves"]]
        reasons = list(document["rejection_reasons"])
        source = str(document["source"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Refused(f"The list file is not shaped as documented: {type(exc).__name__}.") from None

    with conn.begin_nested():
        run(
            conn,
            text(
                "INSERT INTO pipeline.step_list (version, provisional, source, loaded_by) "
                "VALUES (:version, :provisional, :source, :by)"
            ),
            {
                "version": version,
                "provisional": bool(document.get("provisional", False)),
                "source": source,
                "by": loaded_by,
            },
        )
        for position, step in enumerate(steps, start=1):
            run(
                conn,
                text(
                    "INSERT INTO pipeline.step (list_version, code, label, position, outcome) "
                    "VALUES (:version, :code, :label, :position, :outcome)"
                ),
                {
                    "version": version,
                    "code": step["code"],
                    "label": step["label"],
                    "position": position,
                    "outcome": step.get("outcome"),
                },
            )
        for from_step, to_step in moves:
            run(
                conn,
                text(
                    "INSERT INTO pipeline.allowed_move (list_version, from_step, to_step) "
                    "VALUES (:version, :from_step, :to_step)"
                ),
                {"version": version, "from_step": from_step, "to_step": to_step},
            )
        for reason in reasons:
            run(
                conn,
                text(
                    "INSERT INTO pipeline.rejection_reason (list_version, code, label) "
                    "VALUES (:version, :code, :label)"
                ),
                {"version": version, "code": reason["code"], "label": reason["label"]},
            )
        run(
            conn,
            text(
                "INSERT INTO pipeline.list_activation (list_version, activated_by) "
                "VALUES (:version, :by)"
            ),
            {"version": version, "by": loaded_by},
        )
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.lists")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help="load a list file and put it in force")
    load.add_argument("path", type=Path)
    load.add_argument("--by", required=True, help="the person loading the list")
    commands.add_parser("show", help="the list in force")
    args = parser.parse_args(argv)

    engine = engine_from_environment()
    if args.command == "show":
        with engine.connect() as conn:
            print(json.dumps(active_step_list(conn), indent=2, default=str, ensure_ascii=False))
        return 0
    try:
        document = json.loads(args.path.read_text(encoding="utf-8"))
        with engine.begin() as conn:
            version = load_step_list(conn, document, args.by)
    except (OSError, json.JSONDecodeError, PipelineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Loaded and activated step list {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
