# Pipeline step lists

**Requirements:** BR-402, BR-404. **Status:** provisional list in force until TA sends the final one.

## What is in force now

Migration 0005 loads and activates list `provisional-brd-2026-09`, marked **provisional**. It follows the BRD stage order:

new → contacted → replied → phone screen → HR interview → test → technical interview → offer → hired · rejected

| Rule | Provisional list |
|---|---|
| First step | `new`. Every application starts there, as a recorded move. |
| Allowed moves | One step forward, and from any open step to `rejected`. Nothing else. |
| Final steps | `hired` and `rejected`. Nothing moves out of them. |
| Rejection reasons | `does_not_meet_criteria`, `not_reachable`, `no_response`, `withdrew`, `not_suitable_after_interview`, `did_not_pass_test`, `declined_offer`, `opening_filled`. Placeholders, not TA's list. |

See it at any time: `GET /v1/pipeline/steps`, or `python -m pipeline.lists show`.

## Loading TA's final list

No code changes. Write the list as a JSON file (the shape is in `src/pipeline/lists.py`), then:

```bash
docker compose run --rm api python -m pipeline.lists load /path/to/ta-list.json --by "<your name>"
```

The database checks the list before it goes in force: one hired step and one rejected step, no move out of a final step, and at least one rejection reason. A loaded list is never changed; a correction is a new version.

**Before loading, decide what happens to open applications.** New moves follow the list in force. An application sitting at a step the new list does not have cannot move until that is resolved, so agree with TA how the provisional steps map onto theirs.

## What the database enforces, whatever the code does

- A step changes only by a row in `pipeline.move`: application, from, to, who, when. The current step is the latest move (`pipeline.application_state`), never a column.
- No role, the owner included, can change or delete a move, an application, a list or a decision.
- A move is checked against the list in force. A move from a step the application is not at is refused.
- A rejection needs a reason from the list and a person (`actor_kind = 'person'`). An automated reject is a `pipeline.review_item` that a person confirms or dismisses (BR-405).
- Reversing a rejection needs a reason and a person. The rejected application stays rejected; a new application reopens it, linked to it. Dismissals and reversals are listed in `pipeline.override_signal` for criteria reviews (BR-406).
- Migrated TAI_Master candidates get no applications until OPN-11 is ruled, and their legacy Stage values stay in the raw captures until Q-01 is answered.
