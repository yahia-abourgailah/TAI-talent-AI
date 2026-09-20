# The OCR adapter and the answer it expects

**Status: switched on and working, 17 September 2026.** `TALENT_OCR_MODE=api`, against the real service. A CV uploaded on the careers page comes back as a filled form in **about 9 seconds**, end to end, with the age inferred from the graduation year and marked as inferred.

Two things had to be right before it worked, and only one of them was a key:

* **The key.** The first one we were given was refused (`403 InvalidAPIKeyError`) in every header form. The second works.
* **The filename.** The service chooses its reader from the **filename's extension**, not from the content type, and refuses a name that has none — we were sending the part as `cv`, and every CV came back `UnsupportedFileFormatError` and went to a person. We now send `cv.pdf`, `cv.docx`, `cv.jpg` or `cv.png`, built from the type we sniffed ourselves. **The candidate's own filename is never sent**: it is theirs, and it can say anything at all.

## One door, two versions

Everything that reads a CV goes through `intake.ocr.OcrReader`. `TALENT_OCR_MODE` picks the version:

| Mode | Class | Where it runs |
|---|---|---|
| `api` | `intake.ocr_http.HttpOcrReader` | Staging and production (the default there) |
| `fake` | `intake.fake_ocr.FakeOcrReader` | Dev and tests only (the default in dev). It refuses to start anywhere else |

The fake returns the saved samples in `src/intake/fake_samples/` (`en`, `ar`, `mixed`, `hidden`). A marker inside a file changes what it does: `FAKE-OCR:slow`, `fail`, `timeout`, `reject` or `garbled`.

## How we call it

```
POST {TALENT_OCR_BASE_URL}/extract
Content-Type: multipart/form-data        one part named "file"
X-API-Key: {TALENT_OCR_API_KEY}
```

The service offers four extraction paths (`/extract`, `/extract_magic`, `/extract_pdf2`,
`/extract_and_match`). We use `/extract`: it picks the fast parser or the OCR by itself, which is
the decision we would otherwise have to make per file. Its own numbers: English PDFs 3–5 s, Arabic
PDFs and images 8–15 s — comfortably inside our 60 s timeout.

### What it answers

```json
{"name": "…", "email": "…", "phone": "…", "address": "…",
 "experience": [{"role": "…", "company": "…", "duration": "…"}],
 "education":  [{"degree": "…", "institution": "…", "year": "…"}],
 "skills": ["…"], "languages": ["…"], "inferred_skills": ["…"],
 "links": {"linkedin": {"url": "…", "source": "…", "page": 1}}}
```

`src/intake/cv_extractor.py` translates that into the one shape the platform reads, and the rules
it applies are ours, not the service's: the newest job is the current one, the newest study gives
the degree and the graduation year, `address` is the location as written, a LinkedIn link is the
profile, and each value's language is read from its own letters because the service does not say.
The answer is still stored exactly as it came (BR-107) — the translation happens on the way in, not
to the stored copy.

### Read once by each reader, not once forever

A file is read once (BR-106) and its answer is kept and reused. That is right while one reader is
in force, and wrong the day it changes: a candidate re-uploading the CV they sent last week would
be shown what the stand-in invented, under their own name. Found by doing exactly that.

So the rule is now **one reading per file per reader** (migration 0016). Upload a file whose newest
answer came from a reader no longer in force and it is read again; until the new answer arrives the
upload reads as `processing`, never as ready-with-somebody-else's-answer. Nothing is edited or
removed — the old answer stays beside the new one, which is how we can still say what a candidate
was shown and when.

A *failure* counts whoever recorded it, including the worker that gave up: the CV needs a person
either way.

### What the OCR team still has to answer

1. **Hidden content (BR-308).** The answer says nothing about hidden text, and the rule requires
   that a CV with hidden instructions goes to a person. We record `not_reported` rather than
   "none found", because silence is not a clean bill of health, and **no CV is marked safe on it**.
   Does the service look for it, and can it tell us?
2. **Arabic and mixed CVs.** English is proved end to end. The accuracy set
   ([CV_TEST_SET.md](CV_TEST_SET.md)) needs real Arabic and mixed files before we can put a number
   on BR-309, and the rule that matters there is that **a name is never translated**.

| Answer | What we do |
|---|---|
| 200 with JSON | Save the answer as it came (a raw capture with source `ocr_answer`), then map it |
| 408, 425, 429, 5xx, timeout, no connection | Try again after 30 s, 2 min, then 10 min, up to `TALENT_OCR_MAX_ATTEMPTS` (4) attempts, then send the CV to a person |
| Any other 4xx | Send the CV to a person straight away (`ocr_rejected`) |

A key that is wrong or missing (401, 403) is treated as the service being unavailable, not as the
CV being bad: the CV waits and is read once the configuration is fixed. Nothing about a candidate
is lost because of our own settings.

## Limits (NFR-01)

| Setting | Default | Meaning |
|---|---|---|
| `TALENT_OCR_MAX_CONCURRENCY` | 2 | CVs read at the same time, across every worker (Postgres advisory-lock slots) |
| `TALENT_OCR_TIMEOUT_SECONDS` | 60 | How long one read may take |
| `TALENT_OCR_MAX_ATTEMPTS` | 4 | Attempts before the CV goes to a person |

These are safe defaults. Replace them with the OCR team's real numbers.

## The answer shape (guessed)

```json
{
  "schema": "talent-ocr-answer/guess-2026-09-16",
  "document": {"language": "ar | en | mixed", "pages": 2},
  "fields": {
    "full_name": {"text": "...", "language": "ar", "confidence": 0.97}
  },
  "hidden_content": {"found": true, "kinds": ["white_text", "instructions"], "removed": true}
}
```

- **Field names:** `full_name`, `phone`, `whatsapp`, `email`, `location`, `current_title`, `current_employer`, `education`, `graduation_year`, `years_experience`, `age`, `date_of_birth`, `profile_url`. Any other field name is ignored and counted.
- **`text`** is what the CV says, after the OCR has removed hidden content.
- **`hidden_content.kinds`** holds codes only, never the hidden text.

## From answer to fields (A1)

Each field becomes one `core.candidate_field` row: source `cv_extraction`, `unverified`, with its `language`.

- **Text is kept as written.** Values are only trimmed at the ends. A name is never translated or re-spelled.
- **Tidying is for matching only.** Arabic-Indic digits and phone formats are tidied when comparing values, never in what is stored.
- **Numbers:** `age`, `years_experience` and `graduation_year` must be plain numbers. Arabic-Indic digits are allowed, and the value is stored as plain digits. Anything else (for example "3+ years") is not recorded, and is logged as unreadable.
- **No guesses:** a missing or blank field is `not_recorded`, and an age of 0 counts as no age.
- **Age, in this order:**
  1. The age the CV states (`stated`).
  2. Worked out from the date of birth (`inferred`). The date of birth itself is not stored.
  3. Worked out from the graduation year, using the criteria's own rule (`inferred`, OPN-02).
- **Hidden content:** if the OCR reports any, the CV is read as usual and also sent to a person (`flagged_document` / `hidden_content`). The candidate is never told.

## What we need from the OCR team

1. Access: the base URL on our host, and how to authenticate.
2. How to call it: the path, the name of the file field, and the accepted file types and sizes.
3. Sample answers: Arabic, English, mixed, and one CV with hidden content.
4. Their limits: requests at once, a typical and a worst-case read time, and how they report being busy.
5. How they report hidden content, and whether they remove it from the text.

## When access arrives

1. Set `TALENT_OCR_MODE=api`, `TALENT_OCR_BASE_URL` and `TALENT_OCR_API_KEY`.
2. Fix the path and the parser if the real contract differs. Replace the samples in `fake_samples/` with real answers from made-up CVs, then run `pytest tests/unit/test_cv_fields.py`.
3. Set the three limits above from the OCR team's numbers.
4. Run the test set: `python -m intake.accuracy run --reader api` (see [CV_TEST_SET.md](CV_TEST_SET.md)).
5. Upload a real CV on the test stack and check the form that comes back.
