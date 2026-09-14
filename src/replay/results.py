from dataclasses import dataclass


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

    @property
    def has_stored_score(self) -> bool:
        return self.stored.score is not None

    @property
    def score_match(self) -> bool:
        return self.has_stored_score and self.stored.score == self.replayed.score

    @property
    def tier_match(self) -> bool:
        return self.has_stored_score and self.stored.tier == self.replayed.tier
