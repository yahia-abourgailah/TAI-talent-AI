# Integrating with the Talent Platform API

For the front-end team building the recruiter dashboard and the careers page. The platform is the
back end: it holds candidates, requisitions, applications, evaluations and the review queue, and
it serves them over one HTTP API. There is no user interface in this branch, by design — the
screens are yours.

## The contract

- Everything the product needs is under **`/v1`**, and `/v1` is **frozen**: paths, field names and
  the meaning of a value do not change. New fields and new endpoints are added; nothing is renamed
  or removed. Build against it without a version negotiation.
- **`docs/api/openapi-v1.json`** is the contract itself, recorded from the running code and checked
  in CI (`python -m api.contract --check` fails the build if the code and the file disagree). Use
  it to generate a client.
- With `TALENT_ENV=dev` the API also serves live docs at **`/docs`** and its schema at
  **`/openapi.json`**. Both are off everywhere else.
- `GET /health` and `GET /ready` are open; everything else needs a token or is under
  `/v1/public/`.

## Signing in

| Environment | How |
|---|---|
| staging, production | An OIDC bearer token from the company identity provider. Send `Authorization: Bearer <token>`. The platform verifies the issuer and audience itself. |
| a developer's machine (`TALENT_ENV=dev`, `TALENT_AUTH_MODE=dev`) | `GET /dev/accounts` lists fake accounts; `POST /dev/token {"account": "ta-lead"}` returns one. Same header afterwards. These routes exist nowhere else — the settings refuse fake accounts outside dev. |

Roles decide what a caller sees, and the API enforces it: a recruiter sees the candidates and
applications they own, a TA lead and an admin see everything, the criteria owner reads evaluations
and resolves nothing. A refusal is `403 forbidden`, and it is not a bug to fix in the UI — it is
the answer.

## The shapes you will meet everywhere

**Ids are opaque strings with a prefix** — `cand_1234`, `req_88`, `app_512`, `evl_22837`,
`rvw_603338`, `upl_1956`. Pass them back exactly as given; never parse them, never sort on them.

**Errors** always look like this, with the same `code` for the same refusal:

```json
{"error": {"code": "already_applied",
           "message": "This candidate has already applied to this job.",
           "request_id": "c6ac4ae0dc0c408f9aae0c7799744ca3",
           "details": {"application_id": "app_512"}}}
```

Show `message` if you need words; branch on `code`; log `request_id` — it is in our logs too, and
it is how a report becomes a fix. `details` carries what a caller needs to recover, when there is
anything.

**Creating anything needs an `Idempotency-Key` header** (a UUID you generate per distinct request).
Retrying with the same key returns the same answer instead of creating a second row. A missing or
malformed key is refused with `idempotency_key_required` / `invalid_idempotency_key`.

**Lists are cursor-paged**: `?limit=50&cursor=<next_cursor>`. The answer is
`{"items": [...], "next_cursor": "..." | null}`. `next_cursor` is opaque; `null` means the last
page. Recruiter lists are newest first; the event feed is oldest first.

**A field that nobody recorded says so.** Candidate fields come back as
`{"value": null, "state": "not_recorded"}` rather than an empty string, and a recorded one carries
where it came from and whether a person has checked it. Render "not recorded" as its own thing: it
is not the same as blank, and the platform never invents a value to fill a gap.

## The two review lists, and how to resolve an item

There are two, because an item is either about an application or about a candidate:

- `GET /v1/review-items` — proposed rejections. Resolved by confirming or dismissing with a reason.
- `GET /v1/candidate-review-items` — flagged CVs, unchecked candidates, possible duplicates,
  borderline scores, AI assessments. Resolved as *checked* or *dismissed with a reason*.

`GET /v1/review-queue` is both of them in one list, oldest first, each line carrying a `reason` in
plain words and **`resolve_at`** — the path that resolves that item. Post the resolution to
`resolve_at` rather than deciding the path from the item's kind: new kinds arrive, and a caller
that guesses breaks on the day they do.

## The careers page

Under `/v1/public/`, with no sign-in. The flow is:

1. `GET /v1/public/requisitions` — the open jobs, public fields only.
2. `POST /v1/public/cv-uploads` (multipart, one `file`) → `{upload_id, upload_token, status}`.
3. `GET /v1/public/cv-uploads/{id}` with `X-Upload-Token` → `processing` until the CV has been
   read, then `ready` with the fields to pre-fill. Poll for a while and let the candidate type
   over anything; a slow reader is our problem, not theirs.
4. `GET /v1/public/consent-wording` — the wording, Arabic and English, with its version. Show it,
   and send back the version the candidate actually saw.
5. `POST /v1/public/applications` with the fields, a contact channel, the consent, an
   `Idempotency-Key` and the `X-Upload-Token`.

**A public answer never carries a score, a tier or a gate result**, and never says why an
application will not go anywhere. If you need a number on a screen, it is a recruiter screen.

Browsers are only allowed from the origins in `TALENT_CORS_ORIGINS`; ask for yours to be added
rather than proxying around it. Public endpoints are rate-limited per address.

## What the platform will not do

- It never rejects anybody by itself. A score or an AI assessment proposes; a person decides, and
  the decision is recorded with their name (BR-405, CR-05).
- An AI evaluation never carries a tier or a call priority. The database refuses to store one, so
  a screen cannot show one.
- Nothing is deleted. "Removed" means archived, with a reason and a person; archived records stay
  readable and leave the working lists.

## Running it while you build

```bash
cp .env.example .env          # fill in the passwords; TALENT_ENV=dev, TALENT_AUTH_MODE=dev
docker compose up -d --build  # API on http://127.0.0.1:8090
curl -XPOST localhost:8090/dev/token -H 'content-type: application/json' -d '{"account":"ta-lead"}'
open http://127.0.0.1:8090/docs
```

`docs/ops/DEPLOY.md` covers the deployed environments, and `docs/api/API_PLAN.md` explains why each
endpoint is shaped the way it is. A working reference client for every flow above — sign-in, the
queue, the pipeline, the careers page — lives on the `dev` branch under `web/`. It is a developer's
tool, not a design: read it to see the calls in order, not to copy the screens.
