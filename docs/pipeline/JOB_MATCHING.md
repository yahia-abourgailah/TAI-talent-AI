# Jobs that are not sales: the CV is matched against what the job asks for

**BR-305, BR-306, CR-01, CR-05.** Week 7, added after the criteria version was found to score
non-sales requisitions as nonsense.

## Why

The criteria version scores one thing: a sales hire, on one of two tracks. Put a requisition for a
machine learning engineer through it and every candidate fails sales keywords nobody asked them
for, and the tier that comes out is meaningless — but it looks exactly like a real one.

So a requisition now says what kind of job it is.

| `job_type` | What happens to an application |
|---|---|
| `sales` (the default) | `score_application`, the criteria version, a tier. Nothing changed. |
| `other` | `assess_application`: the candidate's own CV file and the job's skills go to the company CV service's `POST /extract_and_match`, which extracts the CV and grades it against those skills in code. |

A requisition of kind `other` must list what it asks for (migration 0020). One to forty skills,
each with the level the job wants:

```json
[{"skill": "Python", "level": "advanced"},
 {"skill": "Arabic", "level": "intermediate", "category": "Language"}]
```

The four levels are the CV service's own: `beginner`, `intermediate`, `advanced`, `expert`. A level
it does not know would give a percentage that means nothing, so the database refuses it. Like
everything else about an opening, the list never changes once the opening exists — a requisition
whose requirements move is a different requisition, and every assessment made under the old ones
would quietly become an assessment of something else.

A requisition may still carry a `description`, a note for whoever reads it, but nothing is judged
by it: it was required while a language model read the CV against those words, and since the match
replaced that, nothing reads it at all (migration 0021).

## What comes back

```
POST {TALENT_OCR_BASE_URL}/extract_and_match
  file          the CV as the candidate gave it
  requirements  {"job_title": ..., "skill_types": [{"name", "levels", "skills"}]}

{"match_report": {"overall_match_percentage": 66.67,
                  "matched": [...], "below": [...], "missing": [...]},
 "metadata": {"extraction_method": "pdf_parser", "requirements_hash": "e7259d66…"}}
```

Each line names the skill, the level asked for, the level detected, and the CV's own words as
evidence. The extraction uses a model; the matching is deterministic code, about 100 ms. The whole
call takes a few seconds.

## What is written

One `core.evaluation` row per match:

| Column | Value |
|---|---|
| `origin` | `ai` |
| `application_id` | the application that was matched. The same person applying to two jobs gets two readings, and neither is about the other. |
| `score` | the percentage. It says how much of what **this** job asks for the CV shows, and is not comparable with a sales score. |
| `signals` | a line per skill found: `Python: advanced, advanced asked for — “Wrote services in Python”` |
| `flags` | a line per skill not found, or found below the level asked for |
| `model_version` | `cv-service/<how it read the file>` |
| `prompt_version` | our matcher version, plus the service's own hash of the skills it graded against |
| `tier`, `call_priority` | always empty |

`track` and `outcome` read back as `not_applicable`: no rules ran, so there is no track to name and
no gate to have passed (BR-703).

Then one open `ai_assessment` review item, served at `/v1/candidate-review-items` like every other
kind that is not a proposed rejection, resolved as *checked* or *dismissed with a reason*. One open
item per application: a retry or a second match does not stack another line on somebody's queue.

## When something goes wrong

| What happened | What the platform does |
|---|---|
| The service is busy, down, or our key is refused | Waits 1 min, 5 min, 30 min, 30 min. After four attempts the application goes to a person with no score. |
| The answer names no skill, or has no percentage | Nothing is recorded. The application goes to a person. |
| The candidate has no CV file | No call at all. The application goes to a person. Most of the 5,140 imported records are in this position: they came from a spreadsheet, not a file. |
| The file is one the service refuses | The application goes to a person, with the refusal in the job run. |

A CV is never lost because a machine was busy, and never judged by silence.

## What is sent

The CV file, and the job's skills. Nothing else — no name, age, sex, marital status, nationality,
religion or photograph is added (CR-07), and the service is on our own host, so no CV leaves the
company (CR-01, `docs/security/EGRESS.md`). On a dev machine `TALENT_OCR_MODE=fake` uses a
stand-in that never leaves the process.

## Trying it

In the console (dev only), create a requisition with **Kind of job = other**, add the skills it
asks for, then add a candidate who has a CV file on record. The Queue tab
shows the item with the percentage in its sentence, and the candidate's Evaluations row opens the
same "Show the sums" panel a score does — a line per skill, shown or not shown, with the CV's own
words beside it.
