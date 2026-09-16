# Talent Platform API Plan

For the CRM team and the website team at The Address Investments.

| | |
|---|---|
| Status | Draft for review |
| Date | 14 September 2026 |
| Owner | AI/ML team |
| Freeze target | End of week 4 of the build plan (Friday 9 October 2026, if the week 2 gate holds) |
| Built from | BRD v0.1, delivery plan v2 |

**Read this first.** Only the endpoints in section 2 exist today. Everything else in this document is a **proposal**. We want your comments on it before we build it. After the freeze at the end of week 4, the conventions in section 5 and the endpoints marked for weeks 3–4 stay stable within `/v1`.

**What each team gets**

- **CRM team.** You host the recruiter dashboard (BRD 4.2). We give you a versioned API with requisitions, candidates, scores and the reasons behind them, applications, stage moves, the review queue and the funnel report. We also send events to your system when something changes, so you do not have to poll. You can build pipeline screens from week 3. The API freezes and a shared test server opens in week 4 (BR-410, DEP-02).
- **Website team.** You own the public careers page (BRD 4.2). We give you public endpoints to list open jobs, upload a CV, get back a filled form for the candidate to check, and submit the application with consent and the job-post tracking code. Upload comes in week 5 and submission in week 6 (BR-101, BR-102, BR-109, DEP-03).

---

## Contents

1. Environments and versioning
2. What exists today
3. Authentication
4. Roles and what each can see
5. Conventions
6. CRM surface (proposed)
7. Events to the CRM (proposed)
8. Website surface (proposed)
9. Not in this plan yet
10. Timeline
11. Decisions we need from you
12. Feedback

---

## 1. Environments and versioning

| Environment | Where | Sign-in | Data | Status |
|---|---|---|---|---|
| dev | Your machine, local Docker stack. Default address `http://127.0.0.1:8090` (the `TALENT_API_PORT` setting changes the port) | Fake accounts from `/dev/token` | Fabricated only | Exists |
| staging | Shared test server for partner teams. You can use it without asking us first | Company identity provider, test accounts | Fabricated candidates. Requisitions may be real open jobs | **Not provisioned yet.** Target: week 4 |
| production | Company infrastructure | Company identity provider | Real candidates | Target: week 8 |

- Every business endpoint lives under the base path **`/v1`**.
- **Additive changes do not break `/v1`.** These include new endpoints, new optional request fields, new response fields, new event types and new enum values. Your client must ignore fields it does not know and must handle enum values it has not seen.
- **Breaking changes only ship in a new major version** (`/v2`). We would run `/v1` alongside it for an overlap period we agree with you. We propose three months.
- Interactive docs are at `/docs` and the machine-readable spec is at `/openapi.json`. Both are on in dev and staging and switched off in production.

---

## 2. What exists today

The `/v1` surface below is **built and frozen** (week 4). From here on it changes only additively (section 1). The machine-readable contract is recorded in `docs/api/openapi-v1.json`, and `/openapi.json` on dev and staging serves the same document.

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness: `{"status": "ok"}` while the process is up. No dependency checks |
| GET | `/ready` | Readiness of each dependency, 200 or 503. Names a failing dependency, never the error text |
| GET | `/v1/me` | The signed-in user: `subject`, `email`, `name`, `roles` (sorted) |
| GET | `/v1/reference/stages` | Stages in order, and the transitions allowed between them |
| GET | `/v1/reference/reasons` | Rejection reasons from the list in force |
| GET, POST | `/v1/requisitions` | List requisitions in scope; open one |
| GET | `/v1/requisitions/{requisition_id}` | One requisition, with its criteria version |
| POST | `/v1/requisitions/{requisition_id}/close` | Close with a reason. A closed requisition is final |
| GET, POST | `/v1/applications` | List applications in scope; attach a candidate to a requisition |
| GET | `/v1/applications/{application_id}` | One application, with the transitions allowed from its stage |
| GET, POST | `/v1/applications/{application_id}/transitions` | The move history; move a stage |
| POST | `/v1/applications/{application_id}/reversal` | Reverse a rejection, with a reason |
| GET | `/v1/review-items`, `/v1/review-items/{review_item_id}` | The review queue |
| POST | `/v1/review-items/{review_item_id}/resolution` | Confirm or dismiss a review item |
| GET | `/v1/candidates`, `/v1/candidates/{candidate_id}` | Candidates in scope; one candidate with where each field came from |
| POST | `/v1/candidates/search` | Find candidates by name, email or phone, sent in the body |
| GET | `/v1/candidates/{candidate_id}/evaluations`, `/v1/evaluations/{evaluation_id}` | Evaluations and the reasons behind them |
| GET | `/v1/events` | The event feed (section 7) |
| POST | `/v1/candidates` | Week 5. A candidate typed in by a recruiter: manual, unchecked, with the recruiter's name |
| POST | `/v1/candidates/{candidate_id}/fields/{field}/verification` | Week 5. A person checked one field: a new verified row, and the old row stays |
| GET | `/v1/candidates/{candidate_id}/documents`, `/v1/documents/{document_id}/file` | Week 5. A candidate's CVs, and the original file byte for byte. Every access is recorded |
| GET | `/v1/candidate-review-items`, `/v1/candidate-review-items/{review_item_id}` | Week 5. Review items about a candidate: `flagged_document`, `unverified_candidate` |
| POST | `/v1/candidate-review-items/{review_item_id}/resolution` | Week 5. `{"decision": "checked"}` or `{"decision": "dismiss", "reason": "..."}` |
| POST | `/v1/public/cv-uploads` | Week 5. Upload a CV (section 8) |
| GET | `/v1/public/cv-uploads/{upload_id}` | Week 5. Reading status, and the filled form when ready (section 8) |
| GET | `/v1/reports/funnel` | Volume and conversion per stage, with contactability. TA lead and admin |
| GET, POST | `/dev/accounts`, `/dev/token` | **Dev only.** Fake accounts `recruiter-a`, `recruiter-b`, `ta-lead`, `criteria-owner`, `admin`, and tokens for them. Absent in staging and production |

**Still to come:** the rest of the public website endpoints in week 6 (section 8).

**Where the build differs from the first proposal:**

- **Requisitions change only by closing:** `POST /v1/requisitions/{id}/close` with a reason. There is no PATCH; headcount and owner are set when the requisition is opened.
- **Transitions:** the history returns `{"items": [...]}`, oldest first. A transition carries `sequence`, `list_version` and `actor: {"id", "kind"}`. There is no `override` field yet.
- **Review resolution** takes `{"decision": "confirm" | "dismiss", "reason": "..."}`. Dismissing needs a written reason rather than a reason code. The only kind so far is `negative_verdict`.
- **Reversal is new:** reversing a rejection opens a linked application at the first stage, and the rejected one stays rejected.
- **Candidate lists return summaries** (`id`, `source`, `created_at`, `archived`). Fetch one candidate for its fields. List filters: `requisition_id`, `owner_id`.
- **Evaluations** carry `origin`, `outcome`, `track`, `score`, `tier`, `recommendation`, `call_priority`, `signals` and `flags`. Per-gate and per-component detail will be added, additively, once new applications are scored when they arrive.
- **Override reasons:** `GET /v1/reference/reasons?kind=override` returns an empty list. Reversals and dismissals take a written reason.
- **Validation errors are 400**, not 422, in the error envelope (section 5).

**Request IDs.** Every response carries `X-Request-ID`. Send your own to trace a call across systems (cut to 64 characters); otherwise we generate one. Every error body repeats it. Quote it when you report a problem.

---

## 3. Authentication

### Company sign-in (staging and production)

- The API accepts **OIDC bearer tokens from the company identity provider**: `Authorization: Bearer <token>`.
- We check the signature (RS256 or ES256) against the provider's published keys. We also check the issuer, the audience and the expiry.
- Roles come from the token's `roles` claim.
- The platform never handles passwords.
- IT has not yet given us the identity provider details. Staging depends on them.

### CRM dashboard: use the recruiter's own token (recommended)

We recommend the dashboard calls the API with **the signed-in recruiter's own token**, not a shared service key. The token must be issued for our API's audience, for example by on-behalf-of or token exchange at the identity provider.

Why:

- **Visibility.** A recruiter must see only the candidates in their scope (BR-408, NFR-09). With the recruiter's token, the API enforces this. With a shared key, the API sees one super-user, and the CRM would have to enforce scoping itself.
- **Attribution.** Every stage move, override and review decision records who did it (BR-403, BR-406, NFR-09). With a shared key, we would have to trust a user name the CRM passes along, and the audit trail would be weaker.

A service account is still needed for one thing only: receiving webhooks (section 7), which carry no personal data.

**This is decision D-CRM-1 (section 11).**

### Website: public endpoints

- Endpoints under `/v1/public` need **no sign-in**. Candidates are strangers.
- They are protected by the following. All numbers are proposals.
  - A rate limit per client IP. Too many requests get a 429 with a `Retry-After` header.
  - A request size limit.
  - File type checks on the file content, not the file extension.
  - A short-lived upload token that ties the upload, the prefill and the submission together.
- **Question for you:** does the careers site already have bot protection, such as a WAF or a challenge? If it does, we would like to verify its result on upload and submit. It must not send CV content or form fields to an outside service (CR-01). **This is decision D-WEB-3.**

### Development: get a fake-account token

Fake-account tokens are checked exactly like real company tokens, so code you write against dev works the same way on staging.

```bash
# List the fake accounts
curl -s http://127.0.0.1:8090/dev/accounts

# Get a token for recruiter-a
TOKEN=$(curl -s -X POST http://127.0.0.1:8090/dev/token \
  -H 'Content-Type: application/json' \
  -d '{"account": "recruiter-a"}' | jq -r .access_token)

# Call the API as recruiter-a
curl -s http://127.0.0.1:8090/v1/me -H "Authorization: Bearer $TOKEN"
# -> {"subject": "...", "email": "...", "name": "...", "roles": [...]}
```

When the token expires (`expires_in` seconds), request a new one.

---

## 4. Roles and what each can see

The role names match the dev accounts. The final names in the identity provider will be agreed with IT.

| Role | Sees | Can do |
|---|---|---|
| recruiter | Applications they own, and the candidates, evaluations, review items and events of those applications (BR-108, BR-408) | Open requisitions and applications they own, move stages, resolve review items, reverse rejections |
| ta-lead | Everything | Everything a recruiter can, on any recruiter's work |
| criteria-owner | Every evaluation and every review item | Read only. No moves, no resolutions |
| admin | Everything | Everything. Every action records who did it (NFR-09) |

A TA lead's scope will narrow to their organisational scope once HRIS organisation data is available.

**Out of scope means not found.** If a record is outside your scope, the API returns **404**, exactly as for a record that does not exist or an id that is malformed. This avoids revealing that the record exists.

---

## 5. Conventions (frozen in week 4)

| Topic | Rule |
|---|---|
| Format | JSON, UTF-8. File uploads are the one exception: `multipart/form-data`. |
| Timestamps | ISO-8601 in UTC with a `Z` suffix, for example `2026-10-05T09:12:44Z`. |
| IDs | Opaque strings with a type prefix, such as `cand_…`, `req_…`, `app_…`, `evl_…`, `evt_…`. Do not parse them or assume a length. |
| Names in records | Stored as written, in Arabic or English. The API never translates them (BR-309). |
| Empty values | Omitted or explicit `null` with `"state": "not_recorded"`. The API never fills in a guess (BR-201, BR-703). |
| Pagination | Cursor based. Request with `?limit=50&cursor=<next_cursor>`. Default limit 50, maximum 200. Responses look like `{"items": [...], "next_cursor": "..." \| null}`. `null` means there are no more pages. |
| Idempotency | Every POST that creates something takes an `Idempotency-Key` header (a UUID you generate), and public intake requires it. The same key with the same body returns the original response. The same key with a different body returns 409. We keep keys for 24 hours. Separately, intake is also de-duplicated on source, external ID and content hash, so the same CV uploaded twice is still one candidate (BR-106). |
| Request ID | `X-Request-ID` on every response. Send your own to trace a call across systems. **This already works today.** |
| Personal data in URLs | Never. Paths and query strings carry only IDs, stage codes, dates and similar non-personal values. Searching by name, email or phone is done with a POST body (`/v1/candidates/search`). URLs end up in logs, proxies and browser history. |
| Concurrency | Stage moves carry the stage you expect (`from_stage`). If someone moved the application first, you get 409 and should reload it. |

### Error envelope

Every error has this body:

```json
{
  "error": {
    "code": "transition_not_allowed",
    "message": "application 41: new to offer is not an allowed move",
    "request_id": "7f3c2a9e0b1d4e6f",
    "details": {"from_stage": "new", "to_stage": "offer", "allowed": ["contacted", "rejected"]}
  }
}
```

- `code` is stable and meant for your code.
- `message` is for people and may change.
- `details` is optional and depends on `code`.
- No error ever repeats a value you sent or a stored candidate value. Validation errors name the fields, never their contents.

| Status | When |
|---|---|
| 400 | Malformed request or failed validation (`details.fields` lists each problem) |
| 401 | Missing, invalid or expired token |
| 403 | Signed in, but your role cannot do this |
| 404 | Does not exist, the id is malformed, or it is outside your scope |
| 409 | The record's state does not allow it (codes below) |
| 413 / 415 | Upload too large / file type not accepted (week 5) |
| 429 | Rate limited. Respect `Retry-After` (week 5) |
| 500 | Our failure. Quote the request ID |
| 503 | A dependency is down, such as the identity provider. Safe to retry with backoff |

| Code | Status | Meaning |
|---|---|---|
| `invalid_request` | 400 | The body, a parameter or a filter is not valid |
| `invalid_cursor` | 400 | Pass `next_cursor` from the previous page unchanged |
| `invalid_idempotency_key` | 400 | `Idempotency-Key` must be a UUID |
| `unauthenticated` | 401 | Sign in again |
| `forbidden` | 403 | Your role cannot do this |
| `not_found` | 404 | Not found, malformed, or out of scope |
| `stage_changed` | 409 | The application moved since you loaded it. `details.current_stage` says where it is |
| `transition_not_allowed` | 409 | Not an allowed move. `details.allowed` lists the moves from `from_stage` |
| `stage_is_final` | 409 | Hired and rejected are final |
| `rejection_reason_required` | 409 | A rejection needs a `reason_code` from the list |
| `reason_not_allowed` | 409 | Only a rejection carries a reason code |
| `requisition_closed` | 409 | A closed requisition takes no applications and cannot close again |
| `candidate_not_eligible` | 409 | This candidate cannot receive applications yet |
| `grouping_not_available` | 400 | The report cannot be grouped that way yet |
| `review_item_resolved` | 409 | The review item was already confirmed or dismissed |
| `not_a_rejection` | 409 | Only an application's rejection, as its latest move, can be reversed |
| `already_exists` | 409 | For example, the candidate already has an application on this requisition |
| `idempotency_key_reused` | 409 | The key was used for a different request. Generate a new key |
| `internal_error` | 500 | Our failure |
| `unavailable` | 503 | A dependency is down |

Retried POSTs with the same `Idempotency-Key` and body return the first response with the header `Idempotent-Replayed: true`.

---

## 6. CRM surface

All endpoints need a bearer token, and results are filtered to the caller's scope (section 4). Lists are cursor pages, newest first (section 5). Typed ids: `req_`, `app_`, `cand_`, `trn_`, `rvw_`, `evl_`, `evt_`.

### Requisitions (job openings)

- `POST /v1/requisitions` with `brand`, `department`, `track` (`A` or `B`), `headcount`, `team`, and optionally `owner_id` (a TA lead may open one for a recruiter). The criteria version in force is set by the platform (BR-303) and returned as `criteria_version`. A TA lead or an admin may name another `criteria_version`, for example from TA's open-jobs file; a recruiter always gets the version in force.
- `GET /v1/requisitions` filters: `status` (`open`, `closed`), `brand`, `track`, `owner_id`.
- `POST /v1/requisitions/{id}/close` with `{"reason": "..."}`. It records who closed it and when. A closed requisition is final.

### Candidates

- `GET /v1/candidates` returns summaries. Filters: `requisition_id`, `owner_id`.
- `GET /v1/candidates/{candidate_id}` returns every field with its source and verification (BR-201, BR-202). A field nobody recorded is `{"value": null, "state": "not_recorded"}`; the API never fills in a guess (BR-703).
- `POST /v1/candidates/search` with any of `full_name`, `email`, `phone` (and `limit`, up to 50). Values go in the body, never the URL. Every value you send must match. Matching is exact once case, spacing and phone formats are tidied (`+20 100…`, `0020 100…`, `0100…` and Arabic-Indic digits are the same number). It is not a fuzzy or duplicate search. The answer is summaries only.
- `POST /v1/candidates` (manual entry) and documents arrive in week 5.

Example `GET /v1/candidates/cand_1042` (fabricated values):

```json
{
  "id": "cand_1042",
  "source": "careers_page",
  "created_at": "2026-10-19T08:41:09Z",
  "archived_at": null,
  "archived_reason": null,
  "fields": {
    "full_name": {"value": "Test Candidate", "source": "cv_extraction", "verification": "verified", "verified_at": "2026-10-19T08:41:07+00:00"},
    "age": {"value": "24", "source": "cv_extraction", "verification": "unverified", "verified_at": null, "inference": "inferred"},
    "current_employer": {"value": null, "state": "not_recorded"}
  }
}
```

### Evaluations (scores and reasons)

`GET /v1/candidates/{candidate_id}/evaluations` (newest first) and `GET /v1/evaluations/{evaluation_id}` return:

```json
{
  "id": "evl_88",
  "candidate_id": "cand_1042",
  "criteria_version": "2026-08-04",
  "origin": "computed",
  "model_version": null,
  "prompt_version": null,
  "track": "entry",
  "outcome": "passed",
  "score": 72.0,
  "tier": "P2",
  "recommendation": "Good Match - Call",
  "call_priority": null,
  "signals": ["Near New Cairo"],
  "flags": [],
  "evaluated_at": "2026-10-19T08:41:11Z",
  "recorded_at": "2026-10-19T08:41:11Z"
}
```

- `origin` is `stored` for a score carried over from the old system, `computed` for one the platform produced.
- `outcome` is `passed` or `failed_gate`. **A failed gate is never a rejection:** a person confirms it through the review queue (BR-405, CR-05).

### Applications and stage moves

A stage changes only by **POSTing a transition**. There is no field you can set, and no other way (BR-402, BR-403). The stage list is served by `GET /v1/reference/stages` and may change when TA confirms it; do not hard-code it.

- `POST /v1/applications` with `requisition_id`, `candidate_id`, and optionally `owner_id`. It starts at the first stage, and that start is itself a transition.
- `GET /v1/applications` filters: `requisition_id`, `stage`, `owner_id`. Each application carries `current_stage`, `outcome` (`hired`, `rejected` or null), `stage_since`, `transitions` and `allowed_transitions`.

**Transition rules** (checked by the database):

- `from_stage` must equal the current stage, or you get 409 `stage_changed` with `details.current_stage`.
- The move must be allowed, or you get 409 `transition_not_allowed` with `details.allowed`.
- Moving to `rejected` needs a `reason_code` from `GET /v1/reference/reasons` (BR-404). The stage it happened at is the `from_stage`.
- Only a person rejects. An automated verdict becomes a review item instead (BR-405).
- The actor comes from the token. You cannot set it.

```json
POST /v1/applications/app_77/transitions
Idempotency-Key: <a new UUID for each distinct request>

{"from_stage": "phone_screen", "to_stage": "rejected", "reason_code": "salary_expectation_above_range"}
```

```json
201 Created
{
  "id": "trn_503",
  "application_id": "app_77",
  "sequence": 4,
  "list_version": "provisional-brd-2026-09",
  "from_stage": "phone_screen",
  "to_stage": "rejected",
  "reason_code": "salary_expectation_above_range",
  "actor": {"id": "usr-recruiter-a", "kind": "person"},
  "occurred_at": "2026-10-21T11:02:33Z"
}
```

**Reversing a rejection** (BR-406): `POST /v1/applications/{id}/reversal` with `{"reason": "..."}`. The rejected application stays rejected, with its history. A new application for the same candidate and requisition starts at the first stage, and its `reopens_application_id` points to the rejected one. Reversals are kept as labelled signals for criteria reviews.

### Review queue

- `GET /v1/review-items` lists open items by default (`status=resolved` for the others). Filters: `kind`, `requisition_id`.
- An item carries `kind`, `application_id`, `candidate_id`, `requisition_id`, `at_stage`, `reason_code`, `proposed_by`, `proposed_at`, and `resolution` (null while open).
- `POST /v1/review-items/{id}/resolution`:
  - `{"decision": "confirm"}` records the rejection as your own transition, with the proposed reason code;
  - `{"decision": "dismiss", "reason": "..."}` leaves the application where it is and keeps your reason as a labelled signal (BR-406).
  - An item is resolved once. The criteria owner can read the queue but not resolve it.

Item kinds fill in as the platform grows: `negative_verdict` now and `borderline` in week 7. **Handle kinds you do not recognise without failing.**

**Items about a candidate (week 5): `GET /v1/candidate-review-items`.** Some items have no application: `flagged_document` (a CV a person must look at: `reason_code` `hidden_content`, or a reading failure such as `ocr_timed_out`), `unverified_candidate` (typed in by hand, `manual_entry`), and `possible_duplicate` from week 6. The frozen items above always carry an application, so these are listed on their own path, with the same `rvw_` ids. Each carries `kind`, `candidate_id`, `document_id` (or null), `reason_code`, `proposed_by`, `proposed_at` and `resolution`. The filters are `status`, `kind` and `candidate_id`. To resolve one, send `{"decision": "checked"}` (you looked at the file, or checked every field) or `{"decision": "dismiss", "reason": "..."}`. An unverified candidate resolves itself when the last field is checked. Scope follows the candidate: a recruiter sees the items of the candidates they can see. **This path is decision D-CRM-6.**

**Fields are checked one at a time:** `POST /v1/candidates/{id}/fields/{field}/verification`, with `{}` to confirm the value on record, or `{"value": "..."}` to correct it. The old row stays in the history. A candidate's fields may now also carry `verified_by` and `language` (`ar`, `en`, `mixed`).

### Reports

`GET /v1/reports/funnel`, for a TA lead or an admin (BR-601, BR-109). Counted only from recorded transitions, never from an application's current stage:

- **Parameters:** `group_by` (`requisition`, `brand`, `recruiter`, `team`), `from` and `to` (arrivals at a stage; `from` inclusive, `to` exclusive; with a time zone). `group_by=source` returns 400 `grouping_not_available` until applications carry a channel and tracking code.
- **Each group** has `applications`, `candidates`, `contactable_candidates` and `contactability`, and for each stage of the list in force: `reached`, `moved_on`, `rejected_here` with `rejected_by_reason`, `still_here`, and `conversion` (moved on ÷ reached).
- **`all_candidates`** gives contactability for every candidate on record, next to the volume.

```json
{
  "stage_list": "proposed-2026-09-15",
  "provisional": true,
  "group_by": "requisition",
  "from": null,
  "to": null,
  "groups": [
    {
      "group": "req_12",
      "applications": 2,
      "candidates": 2,
      "contactable_candidates": 1,
      "contactability": 0.5,
      "stages": [
        {"stage": "new", "label": "New", "reached": 2, "moved_on": 1, "rejected_here": 1,
         "rejected_by_reason": {"not_reachable": 1}, "still_here": 0, "conversion": 0.5}
      ]
    }
  ],
  "arrivals_at_stages_not_in_the_list": 0,
  "all_candidates": {"candidates": 5140, "contactable": 242, "contactability": 0.0471}
}
```

---

## 7. Events to the CRM

| Event type | Sent when | `data` |
|---|---|---|
| `application.stage_changed` | A transition is recorded, including the first stage of a new application (`from_stage` null) | `application_id`, `candidate_id`, `requisition_id`, `transition_id`, `from_stage`, `to_stage` |
| `review.item_created` | An item enters the review queue | `review_item_id`, `kind`, `candidate_id`, `application_id` (null for items about a candidate), and `document_id` for a `flagged_document` |
| `candidate.scored` | The platform scores a candidate. Scores carried over from the old system are not events | `candidate_id`, `evaluation_id`, `criteria_version`, `outcome`, `tier` |

The database writes an event in the same transaction as the change it describes, so no change can happen without its event.

**Delivery**

- **Webhook.** We POST JSON to your HTTPS endpoint, one event per request. Reply with any 2xx within 10 seconds, and process the event after you reply.
- **At-least-once.** You may receive the same event more than once. De-duplicate on the event `id`.
- **Order is not guaranteed.** Use `occurred_at`, and fetch current state from the API when order matters.
- **Signed.** Each request carries:
  - `X-Talent-Event-Id`
  - `X-Talent-Timestamp` (seconds since 1970)
  - `X-Talent-Signature: sha256=<hex>`, which is HMAC-SHA256 over `<timestamp>.<raw body>` with the shared secret.
  - Reject the request if the signature does not match or the timestamp is more than 5 minutes old.
- **Retries.** Without a 2xx we try again after about 1 minute, 5 minutes, 30 minutes, 2 hours, then every 6 hours, for up to 24 hours after the event. After that the event is parked, and it is still in `GET /v1/events`. Nothing is lost while the CRM is down.
- **No personal data.** Payloads carry ids, stages and codes only. Fetch details through the API with the recruiter's token, which applies scoping (CR-01).

Example:

```json
{
  "id": "evt_9120",
  "type": "application.stage_changed",
  "api_version": "v1",
  "occurred_at": "2026-10-21T11:02:33Z",
  "data": {
    "application_id": "app_77",
    "candidate_id": "cand_1042",
    "requisition_id": "req_12",
    "transition_id": "trn_503",
    "from_stage": "phone_screen",
    "to_stage": "rejected"
  }
}
```

**Catching up: `GET /v1/events`.** Oldest first, kept 30 days. Resume after the last event you processed with `after=evt_…`, or page with `cursor`. Filter with `type` (repeatable). Events are scoped like applications: a recruiter's token sees the events of their applications, and a TA lead's or admin's token sees all of them, including `candidate.scored`.

**To switch delivery on** we need your staging and production endpoint URLs (HTTPS), and a shared secret of at least 32 random characters exchanged through a company secret store (D-CRM-4).

---

## 8. Website surface (proposed, weeks 5–6)

**No sign-in, but limits apply.** All paths are under `/v1/public`. They are rate-limited and size-limited (section 3).

**CORS or backend.** If the browser calls the API directly, we allow only the careers site's origin. If your backend calls us instead, tell us (D-WEB-2).

### Candidate flow

1. The candidate opens a job-post link, such as a TikTok post pointing to your careers page with a tracking code.
2. The candidate uploads a CV. You receive an `upload_id` and an `upload_token`.
3. Poll the status until it is `ready` or `failed`. Show the prefilled form; if reading failed, show an empty form.
4. The candidate checks and corrects the fields, enters a contact channel and agrees to the consent text.
5. Submit. The candidate sees a confirmation. Scoring happens on our side. The response never contains a score, tier, gate result or flag.

### Endpoints

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/public/requisitions` | Open jobs for the careers page. Public fields only: title, brand, location, track | BR-401 | 6 |
| GET | `/v1/public/requisitions/{requisition_id}` | One open job, for the landing page of a job-post link | BR-401 | 6 |
| GET | `/v1/public/consent-wording` | The current approved consent text, in Arabic and English, with its `wording_version`. Show exactly this text | BR-101, CR-02 | 6 |
| POST | `/v1/public/cv-uploads` | Upload a CV (details below). Returns `upload_id`, `upload_token`, `status: "processing"`, `expires_at` | BR-102, BR-106, BR-107 | 5 |
| GET | `/v1/public/cv-uploads/{upload_id}` | Extraction status (details below) | BR-102, BR-309 | 5 |
| POST | `/v1/public/applications` | Submit the application (details below). Returns `201` with `application_id` and `status: "received"` | BR-101, BR-109, BR-602, CR-02 | 6 |

**Upload a CV** (`POST /v1/public/cv-uploads`)

- Send `multipart/form-data` with one `file` part, and a `Content-Length` header (a missing one gets 411).
- Proposed limits: PDF, DOCX, JPEG or PNG, up to 10 MB. The type is checked on the file content: a wrong type gets 415 `unsupported_file_type`, and a file that is too big gets 413 `file_too_large`.
- Rate limit: 10 uploads per client address every 10 minutes, then 429 with `Retry-After`. Status checks are limited to 120 a minute.
- The response is `201` with `upload_id` (`upl_…`), `upload_token` (shown once), `status` and `expires_at` (24 hours). It is never cached.
- **The same file uploaded twice** is one stored file and one candidate. Each upload still gets its own id and token, so retrying an upload is harmless and needs no `Idempotency-Key`.

**Poll extraction status** (`GET /v1/public/cv-uploads/{upload_id}`)

- Requires the `X-Upload-Token` header.
- `status` is one of `processing`, `ready` or `failed`.
- When `ready`, `fields` holds prefilled values in the language they were written in, and names are never translated. Each field is `{"value", "inference", "language"}`, or `{"value": null, "state": "not_recorded"}`. The fields are `full_name`, `phone`, `whatsapp`, `email`, `location`, `current_title`, `current_employer`, `education`, `graduation_year`, `years_experience`, `age` and `profile_url`. When `failed`, `fields` is null: show an empty form.
- A wrong or expired token gets 404.
- OCR can take time. Poll every 2 seconds, then back off, for up to about 2 minutes.

**Submit the application** (`POST /v1/public/applications`)

- Requires `Idempotency-Key` and `X-Upload-Token` (when a CV was uploaded).
- The body holds:
  - `requisition_id`;
  - `upload_id`, which is optional because a candidate may apply without a CV;
  - the confirmed `fields`;
  - a required `contact_channel`;
  - `consent`;
  - `tracking_code`.

**Rules for the website flow**

- **Contact channel is required** (BR-109). The type is `phone` or `whatsapp`. A submission without one gets 400.
- **Consent record** (BR-101, CR-02). It captures:
  - what was agreed (`purposes`);
  - the `wording_version` shown;
  - the `channels` the candidate allowed;
  - when they agreed (`agreed_at` from the page). We also record our own received time.
  - the page language.
- **Candidate-confirmed fields.** We record whether each field came from the CV or was typed or corrected by the candidate (BR-201).
- **Tracking code** (BR-602). Pass the code from the job-post link unchanged. It tells us which post brought the candidate. How codes are generated is D-WEB-5.
- **Hidden content is never shown to the candidate** (BR-308). If a CV contains hidden text or instructions, we remove it, record it, and send the CV to a recruiter for review. The candidate sees the normal flow. The upload status, the prefill and the submission response say nothing about it.
- **Nothing is lost.** If CV reading fails, the candidate can still fill the form by hand and submit, and the application goes to the review queue (NFR-04).
- **Keep candidate data on company systems.** Do not send CV files or form fields to third-party analytics, tag managers or form services on the careers page (CR-01).

Example submission (fabricated values):

```json
POST /v1/public/applications
Idempotency-Key: <a new UUID for each distinct request>
X-Upload-Token: <token from the upload response>

{
  "requisition_id": "req_01J9Q3TESTREQ",
  "upload_id": "upl_01JA2BTESTUPL",
  "tracking_code": "tt-sales-0921",
  "fields": {"full_name": "Test Candidate", "email": "test.candidate@example.com", "city": "Cairo"},
  "contact_channel": {"type": "whatsapp", "value": "<phone number>"},
  "consent": {
    "agreed": true,
    "wording_version": "consent-v1",
    "purposes": ["recruitment_contact"],
    "channels": ["whatsapp", "phone"],
    "agreed_at": "2026-10-19T08:41:07Z",
    "language": "ar"
  }
}
```

### Fallback if the website integration is late

If the careers page cannot ship upload and prefill in week 6, we host a minimal apply page on our own domain, using the same endpoints (build plan week 6). Applications cannot wait until week 8. Once your page is ready, job-post links move to it. This protects the intake route (ASM-05). It does not replace your page.

---

## 9. Not in this plan yet

| Area | BRD phase | Why not now |
|---|---|---|
| Outreach and messaging: drafts, consent guards, human-approved sending, interview scheduling and calendar (BR-501–BR-508) | P9 | **Blocked on OPN-01.** Legal and the CPO must first decide how the consent gap for the 5,140 migrated candidates is handled. Until P9, the platform sends no messages. Recruiters keep contacting candidates as they do today and record the outcome as stage moves. |
| Shortlists: ranked lists per requisition with a cap per employer (BR-409) | P8 | After week 8. A candidate list sorted by score covers the need for now. |
| AI scoring with evidence (BR-305, BR-306) | P7 | After week 8. Tiers come from deterministic rules. `model_version` and `prompt_version` already exist on evaluations (null today), so adding AI later is not a breaking change. |

When any of these is planned, it arrives as an **additive** change to `/v1`, with its own plan sent to you first.

---

## 10. Timeline

Weeks follow the build plan. If the week 2 scoring gate slips, every later week moves with it. Calendar dates assume no slip.

| Week | Dates | What becomes available | Who can start |
|---|---|---|---|
| 1 | 14–18 Sep | This plan. `/health`, `/ready`, `/v1/me`, dev sign-in with fake accounts | Both teams: review this plan, answer section 11, try dev sign-in |
| 2 | 21–25 Sep | No new partner endpoints (scoring parity gate). Draft OpenAPI for week 3–4 endpoints | CRM: sketch screens against the draft spec |
| 3 | 28 Sep–2 Oct | Requisitions, candidates, applications, transitions, reference lists, role scoping. Error envelope aligned | CRM: build pipeline screens against the local stack |
| 4 | 5–9 Oct | **API freeze.** Evaluations, candidate search, review queue (contract), funnel report, events by webhook and feed. Written docs. **Staging test server** | CRM: build against staging and connect webhooks |
| 5 | 12–16 Oct | CV upload and extraction status. Candidate documents. Manual candidate entry. Review kinds for flagged documents and unverified candidates | Website: build upload and prefill |
| 6 | 19–23 Oct | Public requisitions, consent wording, application submission with consent and tracking code. Fallback apply page if needed | Website: ship the apply flow. CRM: see new applications arrive |
| 7 | 26 Oct–30 Oct | Review queue complete (duplicates, borderline). Security check of public upload | Both: test end to end on staging |
| 8 | 2–6 Nov | Production. Both systems run side by side, then switch-over | Both: go live |

---

## 11. Decisions we need from you

Each late decision stops the week shown.

### CRM team

| ID | Decision | Our proposal | Blocks |
|---|---|---|---|
| D-CRM-1 | Does the dashboard call the API with each recruiter's own token, or with a shared service account? | Recruiter's own token (section 3), for per-recruiter visibility (BR-408) and attribution (NFR-09) | Week 3 |
| D-CRM-2 | Where is the dashboard hosted, and does it store copies of candidate details? | Company infrastructure only (CR-01). Show details live from the API and store IDs and states, not personal data | Week 3 |
| D-CRM-3 | Can your system receive webhooks from our network, or do you prefer to poll `GET /v1/events`? | Webhooks, with the feed for catch-up | Week 4 |
| D-CRM-4 | Webhook endpoint URLs (staging and production), and how we exchange the signing secret | Exchange the secret through a company secret store or in person, never over email or chat | Week 4 |
| D-CRM-6 | Review items about a candidate (flagged CVs, typed-in candidates, and duplicates from week 6) have no application, so they are served on `/v1/candidate-review-items`. Is a second queue path acceptable for the dashboard? | Yes. The frozen `/v1/review-items` stays unchanged | Week 5 |
| D-CRM-5 | Can you commit to building the dashboard against the frozen API from week 4 (ASM-04)? | If not, we build a small internal screen in week 7, which costs about 3 days of hardening (RSK-09) | Week 4 |

### Website team

| ID | Decision | Our proposal | Blocks |
|---|---|---|---|
| D-WEB-1 | Upload experience and limits: accepted file types, maximum size, and whether the job is chosen before or after upload | PDF, DOCX, JPEG, PNG; 10 MB; one file; job chosen first (from the link) | Week 5 |
| D-WEB-2 | Does the browser call the API directly, or does your backend call it? | Direct from the browser, with CORS limited to the careers site origin | Week 5 |
| D-WEB-3 | Does the careers site already have bot protection? If so, how do we verify it? | Reuse yours if it keeps data in the company. Otherwise rely on our rate and size limits | Week 5 |
| D-WEB-4 | Who owns the consent wording with Legal, and how does a candidate withdraw consent later (BR-504)? | Wording approved by Legal and served from `/v1/public/consent-wording`. A withdrawal route agreed with Legal | Week 6 |
| D-WEB-5 | How are job-post tracking codes generated and put into links? | The platform issues one code per job post. Marketing pastes the link as given | Week 6 |
| D-WEB-6 | Can the careers page ship upload, prefill and submission in week 6 (ASM-05)? | If not, confirm by the end of week 5 and we launch the fallback apply page | Week 6 |

---

## 12. Feedback

Reply to the AI/ML team. Comment on any section, especially the conventions (section 5), which are hardest to change after the freeze. When you report a problem with a call, include the `X-Request-ID`.
