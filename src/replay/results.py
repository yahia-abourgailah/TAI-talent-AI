from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from replay.rulings import Ruling


@dataclass(frozen=True, slots=True)
class Outcome:
    score: int | None
    tier: str
    recommendation: str
    flags: tuple[str, ...]
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RowResult:
    sheet_row: int
    track: str
    input_sha256: str
    parse_issues: tuple[str, ...]
    stored: Outcome
    replayed: Outcome
    replayed_disqualified: bool
    ruling: Ruling | None = None

    @property
    def has_stored_score(self) -> bool:
        return self.stored.score is not None

    @property
    def score_match(self) -> bool:
        return self.has_stored_score and self.stored.score == self.replayed.score

    @property
    def tier_match(self) -> bool:
        return self.has_stored_score and self.stored.tier == self.replayed.tier

    @property
    def unexplained_difference(self) -> bool:
        return self.has_stored_score and not self.score_match and self.ruling is None


def apply_rulings(
    results: Sequence[RowResult], workbook_sha256: str, rulings: Iterable[Ruling]
) -> tuple[list[RowResult], list[Ruling]]:
    """Attaches each ruling to the row it covers.

    A ruling applies only to its own workbook, and only while the row's stored and replayed
    outcomes are exactly the ones it records. Returns the rows, and the rulings for this workbook
    that no longer apply.
    """
    applicable: dict[int, Ruling] = {}
    for ruling in rulings:
        if ruling.workbook_sha256 != workbook_sha256:
            continue
        if ruling.sheet_row in applicable:
            raise ValueError(f"Two rulings for sheet row {ruling.sheet_row}; keep one.")
        applicable[ruling.sheet_row] = ruling

    used: set[int] = set()
    ruled: list[RowResult] = []
    for result in results:
        found = applicable.get(result.sheet_row)
        if found is not None and not result.score_match:
            recorded = (
                found.stored_score,
                found.stored_tier,
                found.replayed_score,
                found.replayed_tier,
            )
            actual = (
                result.stored.score,
                result.stored.tier,
                result.replayed.score,
                result.replayed.tier,
            )
            if recorded == actual:
                result = replace(result, ruling=found)
                used.add(found.sheet_row)
        ruled.append(result)

    stale = [ruling for row, ruling in sorted(applicable.items()) if row not in used]
    return ruled, stale
