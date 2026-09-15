# Pipeline step lists

**Requirements:** BR-402, BR-404. **Status:** the proposed list is in force (migration 0009), marked provisional until TA confirms it.

## What is in force now

Migration 0009 puts list `proposed-2026-09-15` in force, marked **provisional** until TA confirms it: the BRD steps and moves from migration 0005's `provisional-brd-2026-09`, with 19 rejection reasons in place of its placeholders ([lists/proposed-2026-09-15.json](lists/proposed-2026-09-15.json)). It follows the BRD stage order:

new → contacted → replied → phone screen → HR interview → test → technical interview → offer → hired · rejected

| Rule | Provisional list |
|---|---|
| First step | `new`. Every application starts there, as a recorded move. |
| Allowed moves | One step forward, and from any open step to `rejected`. Nothing else. |
| Final steps | `hired` and `rejected`. Nothing moves out of them. |
| Rejection reasons | 19 reasons: 11 the company decided (`outside_hiring_area`, `age_outside_range`, `experience_not_a_fit`, `current_employee`, `not_eligible_for_rehire`, `communication_below_need`, `salary_expectation_above_range`, `did_not_pass_test`, `not_suitable_after_interview`, `checks_not_passed`, `opening_filled`) and 8 the candidate decided (`not_reachable`, `no_response`, `not_interested`, `no_show`, `commute_or_hours`, `accepted_other_offer`, `declined_offer`, `withdrew_other`). |

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
