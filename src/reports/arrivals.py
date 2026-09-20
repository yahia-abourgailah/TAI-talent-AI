"""Where candidates arrived from, per week (week 8: BR-602). Counts only.

The number the scraper decision needs: stopping the scrapers is safe only if our own page brings
enough people. Each candidate record is counted once, in the week it was created (weeks start
Monday, UTC), under the channel of the capture it was made from:

    careers_page      applied on our own page with no job-post link
    job_post:<name>   applied through a job post's tracking link (tiktok, linkedin, ...)
    cv_only           uploaded a CV and has not finished applying
    recruiter_typed   typed in by a recruiter
    scraped_import    migrated from TAI_Master: every one of these was found by the scrapers

Archived and locked records are left out: a count is a view, and those are out of the working
views everywhere else.

The migrated rows all carry the week they were imported, not the week they were scraped. The
scrapers still feed the sheet, not the platform, so the scraped count per week comes from the
sheet's own Date Added column when a workbook is given (`scraped_by_sheet`). That is the
comparison to put in front of TA leadership.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from replay.last_200 import added_on
from replay.workbook import MasterSheet
from reports import ReportRefused

OWN_PAGE = ("careers_page", "job_post")

_ARRIVALS = text(
    """
    SELECT c.created_at, m.source,
           (SELECT p.channel FROM core.consent k
              JOIN pipeline.job_post p ON p.code = k.tracking_code
            WHERE k.candidate_id = c.id AND k.tracking_code IS NOT NULL
            ORDER BY k.id LIMIT 1) AS post_channel,
           EXISTS (SELECT 1 FROM core.consent k WHERE k.candidate_id = c.id) AS applied
    FROM core.candidate c
    JOIN raw.capture m ON m.id = c.capture_id
    -- Put away or locked is out of the work, so out of the count too (BR-205, BR-504).
    WHERE c.archived_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM core.consent_withdrawal w
                      WHERE w.candidate_id = c.id AND w.lifted_at IS NULL)
      AND (CAST(:from AS timestamptz) IS NULL OR c.created_at >= :from)
      AND (CAST(:to AS timestamptz) IS NULL OR c.created_at < :to)
    """
)


def week_of(moment: datetime | date) -> str:
    day = moment.astimezone(UTC).date() if isinstance(moment, datetime) else moment
    return (day - timedelta(days=day.weekday())).isoformat()


def channel_of(source: str, post_channel: str | None, applied: bool) -> str:
    if post_channel:
        return f"job_post:{post_channel}"
    if source in ("public_apply", "cv_upload"):
        return "careers_page" if applied else "cv_only"
    if source == "manual_entry":
        return "recruiter_typed"
    if source == "tai_master":
        return "scraped_import"
    return "other"


def sheet_dates(sheet: MasterSheet) -> list[datetime]:
    """Every readable Date Added in the sheet. Rows without one are not counted."""
    return [moment for moment in (added_on(row) for row in sheet.rows) if moment is not None]


def _group(channel: str) -> str:
    return channel.split(":", 1)[0]


def arrivals_report(
    conn: Connection,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    scraped_dates: Iterable[date | datetime] | None = None,
) -> dict[str, Any]:
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise ReportRefused("from must be before to.")
    weeks: dict[str, Counter[str]] = defaultdict(Counter)
    for row in conn.execute(_ARRIVALS, {"from": date_from, "to": date_to}):
        weeks[week_of(row.created_at)][channel_of(row.source, row.post_channel, row.applied)] += 1

    scraped: Counter[str] = Counter()
    if scraped_dates is not None:
        for moment in scraped_dates:
            day = moment.date() if isinstance(moment, datetime) else moment
            if date_from is not None and day < date_from.date():
                continue
            if date_to is not None and day >= date_to.date():
                continue
            scraped[week_of(day)] += 1

    out = []
    for week in sorted(set(weeks) | set(scraped)):
        counts = weeks.get(week, Counter())
        own = sum(n for channel, n in counts.items() if _group(channel) in OWN_PAGE)
        out.append(
            {
                "week": week,
                "total": sum(counts.values()),
                "own_page": own,
                "by_channel": dict(sorted(counts.items())),
                "scraped_by_sheet": scraped[week] if scraped_dates is not None else None,
            }
        )
    return {
        "from": None if date_from is None else date_from.isoformat(),
        "to": None if date_to is None else date_to.isoformat(),
        "weeks": out,
        "note": (
            "scraped_import counts the week of the migration, not of the scraping. "
            "scraped_by_sheet counts the sheet's Date Added, when a workbook is given."
        ),
    }
