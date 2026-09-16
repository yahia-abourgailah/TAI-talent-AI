# Week 5: reading CVs safely

Built on 16 Sep 2026, **without OCR API access**. Everything is built and tested on the fake OCR. Switching to the real API is a setting (see [OCR_ANSWER.md](OCR_ANSWER.md)).

## Done

| Task | Requirement | What exists |
|---|---|---|
| A1 | BR-309, NFR-07, BR-201 | `intake.answer` (the guessed answer shape) and `intake.cv_fields`. Arabic, English and mixed answers each map to the expected fields, and names come out exactly as the CV wrote them (`tests/unit/test_cv_fields.py`) |
| A2 | BR-311 | `python -m intake.accuracy`: builds a labels template and reports per-field, per-language and hidden-content counts, with counts only. Proven on the fake OCR |
| A3 | BR-104 | **Moved to a later week**, as planned: it needs the OCR API |
| B1 | BR-102, BR-107 | `POST /v1/public/cv-uploads` and `GET /v1/public/cv-uploads/{id}`. The file type is checked on the content: PDF, DOCX, JPEG or PNG, up to 10 MB. Uploads are rate limited. The file is kept under its hash, and its `raw.capture` row and `intake.cv_upload` are append-only. `GET /v1/candidates/{id}/documents` and `GET /v1/documents/{id}/file` record every access in `audit.document_access` |
| B2 | BR-106 | The same bytes are one capture and one candidate, whatever the file is called |
| B3 | NFR-01, NFR-04, BR-308 | The `read_cv` job. At most N CVs are read at once, with a timeout and retries at growing gaps (`jobs.job.run_after`). The answer is kept as it came. A failure, timeout, refusal, unreadable answer, missing file or dead worker ends as a `flagged_document` review item, and so does hidden content. The candidate is never told |
| B4 | BR-103, BR-202 | `POST /v1/candidates`: every field is source `manual_entry`, unverified, and carries the recruiter's name. The candidate opens an `unverified_candidate` review item. `POST /v1/candidates/{id}/fields/{field}/verification` adds a verified row and keeps the old one. The item closes once every field is checked |

Migration `0010_cv_intake`. The `/v1` contract changed additively only (`python -m api.contract --check`).

## A decision to confirm with the CRM team

`flagged_document` and `unverified_candidate` belong to a candidate, not to an application. The frozen `/v1/review-items` item always carries an `application_id`, `requisition_id` and `at_stage`, and making them nullable would break the contract. So these items are served on **`/v1/candidate-review-items`**, with the same `rvw_` ids, and `/v1/review-items` is unchanged. The `review.item_created` event covers both. For candidate items, `application_id` is `null`, and document items add a `document_id`.

## Not done, and why

- **Real OCR answers, limits and accuracy:** waiting on the OCR team for access and samples.
- **Uploading a CV on the shared test stack:** the test server is still not provisioned. Locally, the full flow runs through the API in `tests/integration/test_cv_intake.py`.
- **Bot protection and CORS on the upload:** waiting on D-WEB-2 and D-WEB-3. The rate limit is kept in the API process's memory. Use a shared store if the API runs as more than one process.
- **A retry after a failed read:** a CV is read once. Once the real API is in, re-reading failed CVs is a small follow-up.

## Waiting on others

| Request | From | Sent | Received |
|---|---|---|---|
| API access, how to call it, limits, how hidden content is reported, sample answers (Arabic, English, hidden) | OCR team | | |
| Upload limits and file types (D-WEB-1), browser or backend calls (D-WEB-2), bot protection (D-WEB-3) | Website team | | |
| The new `/v1/candidate-review-items` path for candidate-level review items | CRM team | | |
| Real CVs for the test set, and a recruiter to label them | TA | | |
| A test server | Infrastructure | | |
