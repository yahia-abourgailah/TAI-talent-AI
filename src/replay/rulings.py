"""Rulings on differences between stored and replayed scores (BR-702).

A ruling is tied to one workbook (by SHA-256), one sheet row, and the exact stored and replayed
outcomes. If any of those change, the ruling stops applying and the row counts as unexplained
again, so a ruling can never quietly cover a different candidate, workbook or rule.

Only sheet row numbers and scores are recorded here, never candidate values
(docs/DATA_HANDLING.md). Adding or changing a ruling needs the criteria owner's review
(CODEOWNERS).
"""

from dataclasses import dataclass

KEEP_STORED = "keep stored"


@dataclass(frozen=True, slots=True)
class Ruling:
    workbook_sha256: str
    sheet_row: int
    stored_score: int
    stored_tier: str
    replayed_score: int
    replayed_tier: str
    decision: str
    reason: str
    recorded_on: str


# TAI_Master.xlsx as handed over, baselined 2026-09-14.
MASTER_HANDOVER = "c9c7607e8f77c1dfc344850c2f731b922e5b7b737fb9aec4fbe213ae39efd8ae"

_HIDDEN_MANAGER_TITLE = (
    "Disqualified by hand: a managerial title hidden by the Wuzzuf 'at' glue, so the scorer "
    "could not see it. The manual override stands."
)

RULINGS: tuple[Ruling, ...] = (
    Ruling(
        MASTER_HANDOVER, 5355, 0, "P4", 53, "P3", KEEP_STORED, _HIDDEN_MANAGER_TITLE, "2026-09-14"
    ),
    Ruling(
        MASTER_HANDOVER, 5384, 0, "P4", 68, "P2", KEEP_STORED, _HIDDEN_MANAGER_TITLE, "2026-09-14"
    ),
)
