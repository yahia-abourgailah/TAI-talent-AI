"""Records the criteria owner's signed borderline rule for a criteria version (BR-310).

    python -m scoring.sign_borderline --criteria 2026-08-04 --rule band --points 1 \\
        --signed-by "Karim AlAkkad" --signed-on 2026-09-18 \\
        --ruling "Option 1 of docs/criteria/BORDERLINE_OPTIONS.md" --by "<your name>"

Run it only with the signature in hand. A version takes one rule, once: a different number later is
a new criteria version with a replay behind it (BR-303). Until a rule is recorded, no borderline
item opens. The scoring worker reads the rule on every score, so nothing needs restarting.
"""

import argparse
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from jobs.queue import engine_from_environment
from scoring.borderline import LINES, MAX_POINTS, RULES, Policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scoring.sign_borderline")
    parser.add_argument("--criteria", required=True, help="criteria version, e.g. 2026-08-04")
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument("--points", required=True, type=int, help=f"1 to {MAX_POINTS}")
    parser.add_argument("--signed-by", required=True)
    parser.add_argument("--signed-on", required=True, type=date.fromisoformat)
    parser.add_argument("--ruling", required=True, help="what was signed, where it is kept")
    parser.add_argument("--by", required=True, help="who is recording it")
    args = parser.parse_args(argv)

    if args.criteria not in LINES:
        print(f"error: no tier lines are recorded for criteria {args.criteria}.", file=sys.stderr)
        return 2
    try:
        policy = Policy(args.rule, args.points)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for name in ("signed_by", "ruling", "by"):
        if not getattr(args, name).strip():
            print(f"error: --{name.replace('_', '-')} is empty.", file=sys.stderr)
            return 2

    try:
        with engine_from_environment().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO core.criteria_borderline (criteria_version_id, rule, points, "
                    "signed_by, signed_on, ruling, recorded_by) "
                    "VALUES (:v, :rule, :points, :signed_by, :signed_on, :ruling, :by)"
                ),
                {
                    "v": args.criteria,
                    "rule": policy.rule,
                    "points": policy.points,
                    "signed_by": args.signed_by.strip(),
                    "signed_on": args.signed_on,
                    "ruling": args.ruling.strip(),
                    "by": args.by.strip(),
                },
            )
    except IntegrityError:
        print(
            f"error: criteria {args.criteria} is unknown or already has a borderline rule. "
            "A different number is a new criteria version.",
            file=sys.stderr,
        )
        return 1
    print(f"Recorded for criteria {args.criteria}: borderline is {policy.describe()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
