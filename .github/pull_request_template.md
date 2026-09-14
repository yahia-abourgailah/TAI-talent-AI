## What and why

<!-- One paragraph. Link the BRD requirement: BR-xxx / NFR-xx / CR-xx -->

Requirement:
Phase: <!-- P0–P10 -->

## Checklist

- [ ] Target branch is `dev` (or `main` for a hotfix, with an incident reference)
- [ ] No candidate data, CV, export or credential added — `.gitignore` unchanged or reviewed by compliance
- [ ] Tests added or updated; suite green locally
- [ ] Database migration is forward-only and self-contained in this PR

## Scoring changes only

- [ ] Golden-replay parity suite passes with **zero unexplained differences**
- [ ] Every difference triaged and ruled on by the criteria owner — rulings linked below
- [ ] Shadow-diff report attached (BR-304), showing exactly which candidates change tier
- [ ] Criteria version **added**, not edited — no existing version mutated

Rulings / shadow report:

## Outreach, consent or retention changes only

- [ ] Guards enforced in code, never in a prompt (BR-503)
- [ ] Consent precondition unchanged or reviewed by compliance
- [ ] Withdrawal cascade still covers every store (BR-504)

## Rollback

<!-- How to revert if this misbehaves in production -->
