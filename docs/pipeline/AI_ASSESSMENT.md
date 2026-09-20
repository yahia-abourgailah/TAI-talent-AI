# Jobs that are not sales: the CV is read against the job

**BR-305, BR-306, CR-01, CR-05, CR-07.** Week 7, added after the criteria version was found to score
non-sales requisitions as nonsense.

## Why

The criteria version scores one thing: a sales hire, on one of two tracks. Put a requisition for a
machine learning engineer through it and every candidate fails sales keywords nobody asked them
for, and the tier that comes out is meaningless — but it looks exactly like a real one.

So a requisition now says what kind of job it is.

| `job_type` | What happens to an application |
|---|---|
| `sales` (the default) | `score_application`, the criteria version, a tier. Nothing changed. |
| `other` | `assess_application`: the CV is read against the job's own description by the company's language model, and a person reads what comes back. |

A requisition of kind `other` **must** carry a `description` of at least 40 characters (migration
0018). An assessment nobody can check against the job is not evidence of anything.

## What the model is and is not allowed to do

- It never sets a tier or a call priority. The database refuses them: `evaluation_ai_never_sets_a_tier`.
- It never rejects anybody. The only thing an assessment produces, besides the evaluation, is a
  review item for a person (CR-05).
- Every reason it gives must quote the CV word for word. Reasons whose quote is not in the text we
  sent are dropped; if none survives, nothing is recorded and the CV goes to a person with no score
  (`src/assess/answer.py`).
- It is not given the candidate's name, and is told not to weigh age, sex, marital status,
  nationality, religion or a photograph (CR-07). The name is not withheld by asking nicely: it is
  not in the prompt.
- It runs on the company's own host, `TALENT_VLLM_BASE_URL`, which is on the egress allow-list
  (`docs/security/EGRESS.md`). No CV leaves the company (CR-01).

## What is written

One `core.evaluation` row per reading:

| Column | Value |
|---|---|
| `origin` | `ai` |
| `application_id` | the application that was read. The same person applying to two jobs gets two readings, and neither is about the other. |
| `score` | 0–100: how much of what **this** job asks for is evidenced in the CV. It is not comparable with a sales score. |
| `signals` | one per reason, each with the CV's own words |
| `flags` | `The job asks for: …` — what the description asks for and the CV does not show |
| `model_version`, `prompt_version` | what produced it, so an answer can be traced to a model and a prompt |
| `tier`, `call_priority` | always empty |

`track` and `outcome` read back as `not_applicable`: no rules ran, so there is no track to name and
no gate to have passed (BR-703).

Then one open `ai_assessment` review item, served at `/v1/candidate-review-items` like every other
kind that is not a proposed rejection, resolved as *checked* or *dismissed with a reason*. One open
item per application: a retry or a second reading does not stack another line on somebody's queue.

## When something goes wrong

| What happened | What the platform does |
|---|---|
| The model is busy, down, or our key is refused | Waits 1 min, 5 min, 30 min, 30 min. After four attempts the CV goes to a person with no score. |
| The answer quotes a document we did not send | Nothing is recorded. The CV goes to a person. |
| There is no CV text to read | No model call at all. The CV goes to a person. |
| `TALENT_VLLM_BASE_URL` is empty | The job fails and is retried; preflight warns about it. Applications wait for a person. |

A CV is never lost because a machine was busy, and never judged by silence.

## Settings

```
TALENT_VLLM_BASE_URL=https://vllm.addressinv.com/v1
TALENT_VLLM_API_KEY=…
TALENT_VLLM_MODEL=gemma-4
TALENT_VLLM_TIMEOUT_SECONDS=60
TALENT_VLLM_MAX_CONCURRENCY=1   # the model serves the chatbots too (NFR-01)
```

`python -m ops.preflight` warns when the URL is missing, refuses plain `http` to a remote host, and
warns when the key is empty.

## Trying it

In the console (dev only), create a requisition with **Kind of job = other**, write what the job
asks for, add a candidate to it, and watch the Queue tab: the item appears with the score in its
sentence, and the candidate's Evaluations table shows the reading with its quotes, no tier, and the
model that produced it.
