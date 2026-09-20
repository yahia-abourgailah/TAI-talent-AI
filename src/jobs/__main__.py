"""Run queued jobs and look up what earlier runs did.

python -m jobs work [--job JOB_ID] [--loop [--poll SECONDS]]
python -m jobs recover
python -m jobs show JOB_ID
python -m jobs history [--kind KIND] [--limit N]
"""

import argparse
import json
import sys
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from assess.worker import JOB_KIND as ASSESS_APPLICATION
from assess.worker import handle as assess_application
from importer.tai_master import JOB_KIND as TAI_MASTER_IMPORT
from importer.tai_master import handle as import_tai_master
from intake import reading
from jobs.queue import Handler, engine_from_environment, find_runs, recover_stopped, work_one
from scoring.platform import JOB_KIND as SCORE_APPLICATION
from scoring.platform import handle as score_application

HANDLERS: dict[str, Handler] = {
    TAI_MASTER_IMPORT: import_tai_master,
    SCORE_APPLICATION: score_application,
    ASSESS_APPLICATION: assess_application,
    reading.JOB_KIND: reading.handle,
}

# How often a looping worker looks for stopped jobs and CVs nobody can read any more.
RECOVER_EVERY_SECONDS = 60.0


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


def _recover(engine: Engine) -> list[tuple[int, str]]:
    """Requeues jobs whose worker stopped, then sends CVs that can no longer be read to a person."""
    recovered = recover_stopped(engine)
    for job_id, status in recovered:
        print(f"Job {job_id}: its worker had stopped. Recorded as a failed run; now {status}.")
    with engine.begin() as conn:
        settled = reading.settle_abandoned(conn)
    if settled:
        print(f"CVs no job could read, sent for review: {settled}.")
    return recovered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    work = commands.add_parser("work", help="run queued jobs until none are left")
    work.add_argument("--job", type=int, help="run only this queued job")
    work.add_argument("--loop", action="store_true", help="keep running and pick up new jobs")
    work.add_argument("--poll", type=float, default=1.0, help="seconds between checks (--loop)")
    commands.add_parser("recover", help="record and requeue jobs whose worker stopped")
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

    recovered = _recover(engine)
    if args.command == "recover":
        if not recovered:
            print("No stopped jobs.")
        return 0

    # A misconfigured OCR stops the worker now, not at the first CV.
    reading.services()

    if args.job is not None:
        with engine.connect() as conn:
            job_status = conn.execute(
                text("SELECT status FROM jobs.job WHERE id = :id"), {"id": args.job}
            ).scalar_one_or_none()
        if job_status != "queued":
            state = "not found" if job_status is None else job_status
            print(f"error: job {args.job} is {state}, not queued.", file=sys.stderr)
            return 1

    failed = 0
    last_recovery = time.monotonic()
    while True:
        try:
            if args.loop and time.monotonic() - last_recovery >= RECOVER_EVERY_SECONDS:
                _recover(engine)
                last_recovery = time.monotonic()
            result = work_one(engine, HANDLERS, args.job)
        except DBAPIError as exc:
            if not args.loop:
                raise
            print(f"Database unavailable ({type(exc).__name__}); retrying.", file=sys.stderr)
            time.sleep(max(args.poll, 5.0))
            continue
        if result is None:
            if args.loop and args.job is None:
                time.sleep(args.poll)
                continue
            break
        job, run = result
        print(f"Job {job.id} ({job.kind}) {run['outcome']}, run {run['id']}.")
        failed += run["outcome"] == "failed"
        if args.job is not None:
            break
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
