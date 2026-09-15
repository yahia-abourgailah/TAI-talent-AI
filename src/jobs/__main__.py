"""Run queued jobs and look up what earlier runs did.

python -m jobs work [--job JOB_ID]
python -m jobs show JOB_ID
python -m jobs history [--kind KIND] [--limit N]
"""

import argparse
import json
import sys
from typing import Any

from importer.tai_master import JOB_KIND as TAI_MASTER_IMPORT
from importer.tai_master import handle as import_tai_master
from jobs.queue import Handler, claim, engine_from_environment, find_runs, run_job

HANDLERS: dict[str, Handler] = {TAI_MASTER_IMPORT: import_tai_master}


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    work = commands.add_parser("work", help="run queued jobs until none are left")
    work.add_argument("--job", type=int, help="run only this queued job")
    show = commands.add_parser("show", help="every recorded run of one job")
    show.add_argument("job_id", type=int)
    history = commands.add_parser("history", help="recent runs, newest first")
    history.add_argument("--kind")
    history.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    engine = engine_from_environment()
    if args.command == "show":
        with engine.connect() as conn:
            runs = find_runs(conn, job_id=args.job_id)
        if not runs:
            print(f"No recorded runs for job {args.job_id}.", file=sys.stderr)
            return 1
        _print(runs)
        return 0
    if args.command == "history":
        with engine.connect() as conn:
            _print(find_runs(conn, kind=args.kind, limit=args.limit))
        return 0

    failed = 0
    while True:
        with engine.begin() as conn:
            job = claim(conn, args.job)
            if job is None:
                break
            run_id = run_job(conn, job, HANDLERS)
            (run,) = [r for r in find_runs(conn, job_id=job.id, limit=1) if r["id"] == run_id]
        print(f"Job {job.id} ({job.kind}) {run['outcome']}, run {run_id}.")
        failed += run["outcome"] == "failed"
        if args.job is not None:
            break
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
