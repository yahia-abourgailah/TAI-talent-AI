"""Run the week 4 reports against the database. Counts only.

python -m reports funnel [--group-by opening|brand|recruiter|team] [--from ISO] [--to ISO]
python -m reports timing [--from ISO] [--to ISO]
python -m reports review-queue
python -m reports arrivals [--from ISO] [--to ISO] [--master WORKBOOK]

The funnel carries the review queue next to it: open items per kind, and the oldest one's wait.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from candidates import queue
from jobs.queue import engine_from_environment
from pipeline.access import Actor
from replay.workbook import read_master
from reports import ReportRefused
from reports.arrivals import arrivals_report, sheet_dates
from reports.funnel import funnel_report
from reports.timing import timing_report


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("give a time zone, for example 2026-09-21T00:00:00+03:00")
    return moment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reports")
    commands = parser.add_subparsers(dest="report", required=True)
    for name in ("funnel", "timing", "arrivals"):
        command = commands.add_parser(name)
        command.add_argument("--from", dest="date_from", type=_moment)
        command.add_argument("--to", dest="date_to", type=_moment)
        if name == "funnel":
            command.add_argument("--group-by")
        if name == "arrivals":
            command.add_argument(
                "--master", help="the sheet, for scraped arrivals per week by its Date Added"
            )
    commands.add_parser("review-queue")
    args = parser.parse_args(argv)

    everyone = Actor("reports-cli", sees_all=True)
    report: dict[str, object]
    try:
        with engine_from_environment().connect() as conn:
            if args.report == "funnel":
                report = funnel_report(
                    conn, group_by=args.group_by, date_from=args.date_from, date_to=args.date_to
                )
                report["review_queue"] = queue.summary(conn, everyone)
            elif args.report == "arrivals":
                scraped = None
                if args.master:
                    scraped = sheet_dates(read_master(Path(args.master).expanduser()))
                report = arrivals_report(
                    conn, date_from=args.date_from, date_to=args.date_to, scraped_dates=scraped
                )
            elif args.report == "review-queue":
                report = queue.summary(conn, everyone)
            else:
                report = timing_report(conn, date_from=args.date_from, date_to=args.date_to)
    except ReportRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
