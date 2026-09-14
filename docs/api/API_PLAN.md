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

These endpoints are running now. Nothing else exists yet.

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/health` | None | Liveness. Returns `{"status": "ok"}` while the process is up. It does not check dependencies. |
| GET | `/ready` | None | Readiness. Checks each dependency and returns `{"status": "ready" \| "not ready", "checks": {"<dependency>": "ok" \| "unavailable"}}` with status 200 or 503. It names the failing dependency but never includes the error text. |
| GET | `/v1/me` | Bearer token | The signed-in user: `{"subject", "email", "name", "roles"}`. `roles` is a sorted list. |
| GET | `/dev/accounts` | None | **Dev only.** Lists the fake accounts: `recruiter-a`, `recruiter-b`, `ta-lead`, `criteria-owner`, `admin`. |
| POST | `/dev/token` | None | **Dev only.** Body `{"account": "recruiter-a"}`. Returns `{"access_token", "token_type": "bearer", "expires_in", "account"}`. |

**How it behaves today:**

- **Errors** use FastAPI's default body, `{"detail": ...}`. That is a message string, or a list for request-validation errors. Examples:
  - 401 `{"detail": "Sign in with your company account."}` when the token is missing, with a `WWW-Authenticate: Bearer` header.
  - 401 `{"detail": "Your sign-in is invalid or has expired. Sign in again."}` when the token is invalid or expired.
  - 503 `{"detail": "Sign-in is unavailable. Try again shortly."}` when the identity provider cannot be reached.
  - Before the freeze these move to the error envelope in section 5.
- **Request IDs.** Every response carries `X-Request-ID`. If you send one, we echo it back (cut to 64 characters). If you do not, we generate one. Quote it when you report a problem.
- **Dev routes.** The `/dev` routes exist only in dev. They are absent in staging and production.

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

## 4. Roles and what each can see (proposed)

The role names match the dev accounts. The final names in the identity provider will be agreed with IT.

| Role | Sees | Can do |
|---|---|---|
| recruiter | Only candidates and applications assigned to them or sourced by them (BR-108, BR-408) | Move stages, enter candidates by hand, resolve review items in scope |
| ta-lead | Candidates within their organisational scope | Everything a recruiter can, plus create and edit requisitions and view reports |
| criteria-owner | Evaluations and review items | Read only. No stage moves |
| admin | Everything | Everything. Every action is attributed (NFR-09) |

**Out of scope means not found.** If a candidate is outside your scope, the API returns **404**, not 403. This avoids revealing that the record exists.

---

## 5. Conventions (proposed, stable after freeze)

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

Current endpoints return `{"detail": ...}`. We will align them to this envelope before the freeze.

```json
{
  "error": {
    "code": "transition_not_allowed",
    "message": "An application cannot move from new to offer.",
    "request_id": "7f3c2a9e0b1d4e6f",
    "details": {"from_stage": "new", "to_stage": "offer", "allowed": ["contacted", "rejected"]}
  }
}
```

- `code` is stable and meant for your code.
- `message` is for people and may change.
- `details` is optional and depends on `code`.

| Status | When |
|---|---|
| 400 | Malformed request or failed validation (`details` lists the fields) |
| 401 | Missing, invalid or expired token |
| 403 | Signed in, but your role cannot do this |
| 404 | Does not exist, or is outside your scope |
| 409 | Stage changed since you loaded it, move not allowed, or `Idempotency-Key` reused with a different body |
| 413 / 415 | Upload too large / file type not accepted |
| 429 | Rate limited. Respect `Retry-After`. |
| 503 | A dependency is down, such as the identity provider. Safe to retry with backoff. |

---

## 6. CRM surface (proposed)

All endpoints need a bearer token. Results are filtered to the caller's scope (section 4). "Week" is the build-plan week in which the endpoint becomes available.

### Requisitions (job openings)

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/requisitions` | List. Filter by `status`, `brand`, `track`, `owner_id` | BR-401 | 3 |
| POST | `/v1/requisitions` | Create: brand, department, track, headcount, owning recruiter. The criteria version in force is set by the platform, not by the caller | BR-401, BR-303 | 3 |
| GET | `/v1/requisitions/{requisition_id}` | One requisition, including its criteria version | BR-401 | 3 |
| PATCH | `/v1/requisitions/{requisition_id}` | Change status, headcount or owner. Attributed | BR-401, NFR-09 | 3 |

### Candidates

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/candidates` | List in scope. Filter by `requisition_id`, `verification`, `owner_id`, `updated_after` | BR-108, BR-408 | 3 |
| POST | `/v1/candidates/search` | Search by name, email or phone in the body, never in the URL | BR-408 | 4 |
| POST | `/v1/candidates` | Recruiter enters a candidate by hand. The source is recorded as manual and the candidate starts unverified | BR-103, BR-202 | 5 |
| GET | `/v1/candidates/{candidate_id}` | One candidate. Every field carries its source, verification state and verification time. Includes the sourcing recruiter and team | BR-201, BR-202, BR-108 | 3 |
| GET | `/v1/candidates/{candidate_id}/documents` | Original files, with any hidden-content findings so the recruiter can see what was found | BR-107, BR-308 | 5 |
| GET | `/v1/documents/{document_id}/file` | Download the original file, unchanged. Access is logged | BR-107 | 5 |

Example `GET /v1/candidates/cand_01J9Q4TESTC4ND` (trimmed; values are fabricated):

```json
{
  "id": "cand_01J9Q4TESTC4ND",
  "verification": "unverified",
  "source": {"channel": "careers_page", "tracking_code": "tt-sales-0921", "sourcing_recruiter_id": "usr_01J8TESTRECA", "team": "team_sales_east"},
  "fields": {
    "full_name": {"value": "Test Candidate", "source": "cv_extraction", "verification": "confirmed_by_candidate", "verified_at": "2026-10-19T08:41:07Z"},
    "email": {"value": "test.candidate@example.com", "source": "candidate_entered", "verification": "unverified", "verified_at": null},
    "current_employer": {"value": null, "state": "not_recorded"}
  },
  "created_at": "2026-10-19T08:41:09Z",
  "updated_at": "2026-10-19T08:41:12Z"
}
```

### Evaluations (scores and reasons)

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/candidates/{candidate_id}/evaluations` | All evaluations for a candidate, newest first | BR-303, BR-307 | 4 |
| GET | `/v1/evaluations/{evaluation_id}` | One evaluation (see the fields below) | BR-303, BR-307, CR-04 | 4 |

An evaluation contains:

- the criteria version, plus model and prompt versions (null until AI scoring exists);
- the track;
- the outcome: `passed`, `failed_gate` or `needs_review`;
- the score out of 100 and the tier;
- each gate with its result;
- each component score with its reason;
- flags with their reasons.

It contains everything needed to explain a decision without re-running anything (BR-307, CR-04). A failed gate is never a rejection; a person confirms (BR-405, CR-05).

Example (trimmed; gate and component names are illustrative):

```json
{
  "id": "evl_01J9Q4TESTEVL",
  "candidate_id": "cand_01J9Q4TESTC4ND",
  "criteria_version": "2026-08-04",
  "model_version": null,
  "prompt_version": null,
  "track": "entry",
  "outcome": "passed",
  "score": 72,
  "tier": "P2",
  "gates": [{"code": "location", "result": "pass", "reason": "Lives in Cairo"}],
  "components": [{"code": "sales_experience", "points": 24, "max_points": 30, "reason": "2 years in field sales"}],
  "flags": [{"code": "hidden_content_removed", "reason": "White-on-white text found and removed"}],
  "evaluated_at": "2026-10-19T08:41:11Z"
}
```

### Applications and stage moves

A stage can only change by **POSTing a transition**. There is no field you can set, and no other way (BR-402, BR-403).

Stages, subject to TA confirming the list: `new`, `contacted`, `replied`, `phone_screen`, `hr_interview`, `aptitude_test`, `technical_interview`, `offer`, `hired`, `rejected`.

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/applications` | List in scope. Filter by `requisition_id`, `stage`, `owner_id`, `updated_after` | BR-408 | 3 |
| POST | `/v1/applications` | Attach a candidate to a requisition (recruiter-sourced). Starts at `new` | BR-108, BR-402 | 3 |
| GET | `/v1/applications/{application_id}` | One application: current stage, owner, and the moves allowed from here | BR-402 | 3 |
| GET | `/v1/applications/{application_id}/transitions` | Full move history: who, when, from, to, reason | BR-403 | 3 |
| POST | `/v1/applications/{application_id}/transitions` | Move a stage (rules below) | BR-402–BR-406, CR-05 | 3 |
| GET | `/v1/reference/stages` | Stage codes and allowed moves | BR-402 | 3 |
| GET | `/v1/reference/reasons` | Rejection reasons and override reasons (`?kind=rejection` or `?kind=override`) | BR-404, BR-406 | 3 |

**Transition rules** (checked on the server):

- `from_stage` must equal the current stage, or you get 409 `stage_changed`.
- The move must be allowed, or you get 409 `transition_not_allowed` with the allowed moves in `details`.
- Moving to `rejected` needs a `reason_code` from the rejection list. The stage at which it happened is recorded from `from_stage` (BR-404).
- A move that goes against the system's verdict needs an `override` with a reason code and is kept for criteria review (BR-406). An example is advancing a candidate who failed a gate.
- The actor comes from the token. You cannot set it.

Example request and response:

```json
POST /v1/applications/app_01J9Q4TESTAPP/transitions
Idempotency-Key: <a new UUID for each distinct request>

{"from_stage": "phone_screen", "to_stage": "rejected", "reason_code": "not_available_for_field_work", "note": null}
```

```json
201 Created
{
  "id": "trn_01J9Q5TESTTRN",
  "application_id": "app_01J9Q4TESTAPP",
  "from_stage": "phone_screen",
  "to_stage": "rejected",
  "reason_code": "not_available_for_field_work",
  "override": null,
  "actor": {"id": "usr_01J8TESTRECA", "name": "Recruiter A"},
  "occurred_at": "2026-10-21T11:02:33Z"
}
```

### Review queue

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/review-items` | Items waiting for a person, each with its reason and evidence. Filter by `kind`, `requisition_id` | BR-407, BR-405 | 4 (contract), 7 (all kinds) |
| POST | `/v1/review-items/{review_item_id}/resolution` | Resolve with a decision and a reason code. Attributed | BR-405, BR-406, CR-05 | 4 |

The endpoint shape freezes in week 4. Item kinds fill in as the platform grows:

- `negative_verdict` — failed gates waiting for human confirmation: week 4.
- `flagged_document` — hidden content, or CV reading failed: week 5.
- `unverified_candidate`: week 5.
- `possible_duplicate` — unclear identity: week 6.
- `borderline`: week 7.

Handle kinds you do not recognise without failing.

### Reports and event catch-up

| Method | Path | Purpose | Requirements | Week |
|---|---|---|---|---|
| GET | `/v1/reports/funnel` | Volume and conversion per stage, computed from saved moves. Parameters: `from`, `to`, `group_by` (`requisition`, `brand`, `recruiter`, `team`, `source`). Includes contactability next to volume | BR-601, BR-109 | 4 |
| GET | `/v1/events` | The same events we push by webhook, as a cursor feed. Use it to catch up after downtime. Kept 30 days | BR-410 | 4 |

---

## 7. Events to the CRM (proposed, week 4)

| Event type | Sent when |
|---|---|
| `candidate.scored` | An evaluation is saved |
| `application.stage_changed` | A transition is recorded |
| `review.item_created` | An item enters the review queue |

**Delivery**

- **Webhook.** We POST JSON to an HTTPS endpoint you give us, one event per request. Reply with any 2xx within 10 seconds, and process the event after you reply.
- **At-least-once.** You may receive the same event more than once. De-duplicate on the event `id`.
- **Order is not guaranteed.** Use `occurred_at`, and fetch current state from the API when order matters.
- **Signed.** Each request carries these headers:
  - `X-Talent-Event-Id`
  - `X-Talent-Timestamp`
  - `X-Talent-Signature: sha256=<hex>`, which is HMAC-SHA256 over `<timestamp>.<raw body>` with a shared secret.
  - Reject the request if the signature does not match or the timestamp is more than 5 minutes old.
- **Retries.** If you do not answer 2xx, we retry with growing gaps (about 1 min, 5 min, 30 min, 2 h, 6 h) for up to 24 hours. After that the event is parked and still available from `GET /v1/events`. Nothing is lost while the CRM is down.
- **No personal data.** Payloads carry IDs and states only: no names, contact details or CV content. When you need details, fetch them through the API with the recruiter's token, which applies scoping and logging. This is data minimisation (CR-01).

Example:

```json
{
  "id": "evt_01J9Q5TESTEVT",
  "type": "application.stage_changed",
  "api_version": "v1",
  "occurred_at": "2026-10-21T11:02:33Z",
  "data": {
    "application_id": "app_01J9Q4TESTAPP",
    "candidate_id": "cand_01J9Q4TESTC4ND",
    "requisition_id": "req_01J9Q3TESTREQ",
    "transition_id": "trn_01J9Q5TESTTRN",
    "from_stage": "phone_screen",
    "to_stage": "rejected"
  }
}
```

The other two event types follow the same shape:

- `candidate.scored` carries `candidate_id`, `evaluation_id`, `criteria_version`, `outcome` and `tier`.
- `review.item_created` carries `review_item_id`, `kind`, `candidate_id` and `application_id`.

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

- Send `multipart/form-data` with one `file` part.
- Proposed limits: PDF, DOCX, JPEG or PNG, up to 10 MB. The type is checked on the file content.

**Poll extraction status** (`GET /v1/public/cv-uploads/{upload_id}`)

- Requires the `X-Upload-Token` header.
- `status` is one of `processing`, `ready` or `failed`.
- When `ready`, `fields` holds prefilled values in the language they were written in, and names are never translated.
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
