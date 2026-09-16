# The CV test set (BR-311)

**Owner:** Person A. The CVs and their labels are candidate data. They stay **outside the repository** (see [DATA_HANDLING.md](../DATA_HANDLING.md)), in the folder named by `TALENT_CV_TEST_SET`.

## Collect

- Real CVs in Arabic, in English, and in both languages, plus a few with hidden text (white text, tiny fonts, hidden instructions). Aim for at least 20 of each language group and 5 with hidden text.
- Supported file types: PDF, DOCX, JPEG and PNG.
- Give files neutral names, such as `cv-001.pdf`, never a person's name.

## Label

```bash
python -m intake.accuracy template --set "$TALENT_CV_TEST_SET"
```

This writes `labels.template.json`, with one entry per CV. A recruiter fills it in and saves it as `labels.json`:

```json
{"format": "cv-labels/1", "labelled_by": "<name>", "cvs": [
  {"file": "cv-001.pdf", "language": "ar", "hidden_content": false,
   "fields": {"full_name": "<exactly as the CV writes it>", "phone": "...", "current_employer": null}}
]}
```

How to fill in a field:

- **A value:** what the CV says. Write names exactly as the CV writes them.
- **`null`:** the CV does not have this field.
- **`""` or left out:** not checked.

## Run

```bash
python -m intake.accuracy run --set "$TALENT_CV_TEST_SET" --reader api \
  --out ~/TAI-data/cv-accuracy --report-copy docs/intake/CV_ACCURACY.md
```

The run reads every CV through the same adapter and mapping the platform uses. For each field it counts:

- **correct**
- **wrong:** a different value came out
- **missed:** nothing came out
- **invented:** a value came out that the CV does not have

It also counts, overall and per language, whether hidden content was caught. The report holds counts only, so the copy may be committed.

How values are compared:

- **Names:** must match exactly.
- **Phones:** match on their digits.
- **Numbers:** match on their value.
- **Emails:** match ignoring case.
- **Other text:** matches ignoring case and spacing.

With `--reader fake`, the numbers mean nothing: the run only proves that the runner works. Run it again on every change to the adapter or the mapping.

## Status

| Step | State |
|---|---|
| Runner | Built and tested on the fake OCR |
| CVs collected | Waiting on TA |
| Labels written | Waiting on a recruiter from TA |
| Real accuracy | Waiting on OCR API access |
