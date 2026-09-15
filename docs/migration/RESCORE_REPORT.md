# Re-scoring check for the 3 August 2026 fixes (BR-704)

Workbook SHA-256 `c9c7607e8f77c1dfc344850c2f731b922e5b7b737fb9aec4fbe213ae39efd8ae`. Criteria 2026-08-04 replayed as of 2026-09-14. Sheet rows and tiers only; no candidate values.

**Pending changes: 0.** Nothing needs re-scoring: every stored result the fixes could have changed already matches today's rules, or is covered by a ruling.

## The fixes

| Fix | Column | What the old rule did | Rows it could have disqualified | Already match today | Ruled | Pending |
|---|---|---|---:|---:|---:|---:|
| country | Location | a non-Egypt country found inside a location word disqualified the row | 9 | 9 | 0 | 0 |
| manager | Title | a managerial word found inside a title word disqualified the row | 20 | 18 | 2 | 0 |

## Rows added before 2026-08-03

| Outcome | Rows |
|---|---:|
| stored result already matches today's rules | 3,926 |
| differs, covered by a ruling | 2 |
| tier would change: pending sign-off | 0 |
| score would change, same tier: pending sign-off | 0 |
| never scored | 0 |

## Rows covered by a ruling

| Sheet row | Fix | Stored | Today | Outcome |
|---:|---|---|---|---|
| 5355 | manager | 0 P4 | 53 P3 | differs, covered by a ruling |
| 5384 | manager | 0 P4 | 68 P2 | differs, covered by a ruling |

## Pending changes

None.

## Sign-off

BR-704 asks for tier changes to be reported before they are applied. The criteria owner records acceptance of this report in docs/migration/WEEK3_DECISIONS.md.

## Reproduce

```bash
python -m replay.rescore --master "$TALENT_MASTER_PATH" --run-date 2026-09-14
```
