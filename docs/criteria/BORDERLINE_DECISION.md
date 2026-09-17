# Borderline: the number, for Karim to pick (BR-310)

**Status: waiting for Karim.** Until a rule is signed, no borderline item opens. The code is
built and switched off.

The BRD says a borderline candidate is a real answer, not rounded up or down, but not how close is
close. A borderline candidate **keeps the tier the score gives**. A person sees an item that names
the tier above and the tier below the line, and decides.

Numbers are from the week-1 workbook (`c9c7607e…`), replayed with criteria 2026-08-04. They cover
the 4,901 candidates the gates did not disqualify, all of them Track A. The full counts are in
[BORDERLINE_OPTIONS.md](BORDERLINE_OPTIONS.md). The lines are P1 ≥ 75, P2 ≥ 55, P3 ≥ 35 (Track B:
T1 ≥ 80, T2 ≥ 60, T3 ≥ 40).

## The shape of the scores

The scorer gives lumps, not a smooth spread. Widening the band a little can add a thousand
people.

| Score | Candidates | Where it sits |
|---|---|---|
| 33 | 30 | 2 under the P3 line |
| 35 | 9 | the P3 line |
| 38 | 489 | 3 over the P3 line |
| 53 | **1,048** | 2 under the P2 line |
| 54 | 20 | 1 under the P2 line |
| 55 | 1 | the P2 line |
| 56 | 14 | 1 over the P2 line |
| 75 | 79 | the P1 line |
| 76 | 158 | 1 over the P1 line |

A band of ±2 doesn't just catch the near misses: it takes in all 1,048 people at score 53 at
once. 89 candidates sit exactly on a line.

| Band | Candidates | Share |
|---|---|---|
| ±1 | 294 | 6.0% |
| ±2 | 1,375 | 28.1% |
| ±3 | 1,893 | 38.6% |
| ±5 | 2,133 | 43.5% |

## The three options

| | Option | Queue today | What it means |
|---|---|---|---|
| **1** | **±1 point** (`band 1`) | **294** (6.0%) | Anyone within one point of a line, including on it. A queue a person can work through. |
| **2** | **Only below the line, 1 point** (`below_line 1`) | **23** (0.5%) | Only the candidate one point short, the one we risk losing. Someone one point over already has the better tier. This is much smaller than half of option 1, because most of option 1 sits *on* or *over* a line (79 at 75, 158 at 76). `below_line 2` is 1,102, because it takes in the lump at 53. |
| **3** | **A guessed input read the other way** | see below | Borderline means the tier would change if one guessed input were read the other way. This definition explains itself to a candidate (CR-04). |

Option 3, measured by re-scoring every candidate with one input flipped:

| Flip | Tier changes | Share |
|---|---|---|
| Age one year younger | 114 | 2.3% |
| Age one year older | 1 | 0.0% |
| No age at all | 0 | 0.0% |
| Location read as the other Cairo band (near New Cairo ↔ farther Cairo) | 2,893 | 59.0% |
| **Any of the above** | **2,906** | **59.3%** |

On this data, option 3 is really a question about location. A location is worth 30, 15 or 5
points, so reading "near New Cairo" as "farther Cairo" moves most candidates a tier. The age flip
alone (115 people, almost all one year younger) is a workable queue, but it catches ages at the 21 and 32 gates, not the
points bands. The workbook has no graduation year, so an age inferred from graduation couldn't be
flipped here. CVs coming in through the platform can carry one.

**Engineering's reading:** option 1 is the only one that is both small and about the score. Option
2 is almost empty. Option 3 would need its own build (the queue today supports `band` and
`below_line`) and would be dominated by location. The choice is Karim's.

## Recording the choice

The number lives with the criteria version, not in code (table `core.criteria_borderline`). A
version takes one rule, once. A different number later is a new criteria version with a replay
behind it (BR-303).

```bash
python -m scoring.sign_borderline --criteria 2026-08-04 --rule band --points 1 \
  --signed-by "Karim AlAkkad" --signed-on <date signed> \
  --ruling "Option 1, docs/criteria/BORDERLINE_DECISION.md" --by "<who records it>"
```

From then on, every new score within the distance opens a `borderline_score` item, and the
evaluation's flags read, for example, `BORDERLINE: 1 point under the P2 line (55); between P2 and
P3. Rule: within 1 of a tier line, criteria 2026-08-04`. The replay is not affected.

| Signed | Rule | By | On |
|---|---|---|---|
| — | — | — | — |
