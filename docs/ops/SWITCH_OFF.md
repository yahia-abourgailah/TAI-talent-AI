# Switching the old system off (BR-705)

In this order, one step a day at most, and each only after its replacement is demonstrably
carrying the load. In three months someone will ask when the scrapers stopped, and the answer
should be a row in this table, not a memory.

| Step | Switch off | Only after | Evidence to attach |
|---|---|---|---|
| 1 | The scrapers | Candidates arrive through our own page and are scored, and the arrivals count shows how many | `python -m reports arrivals --master <sheet>` for the last four weeks: `own_page` beside `scraped_by_sheet`. **If own-page arrivals are far below what scraping brought, do not switch off.** Run both longer and take it to TA leadership. |
| 2 | The Railway portal | Recruiters record steps in the platform, and the funnel report is generated from them | `GET /v1/reports/funnel`: stage counts that move week to week |
| 3 | The Google Sheet copy | The sheet is frozen read-only and nobody has written to it for a full week | The sheet's version history showing no edit for 7 days |
| 4 | The outside AI key | Last. First check that nothing else in the company uses it | The key's usage page showing no calls; then the deletion confirmation |

## Freezing the sheet (step 3)

1. Remove every editor. Leave viewers.
2. In the first row, write: *Frozen on <date>. The live record is the Talent Platform. Do not
   edit.*
3. Keep the exact file that was imported, fingerprint
   `c9c7607e8f77c1dfc344850c2f731b922e5b7b737fb9aec4fbe213ae39efd8ae`, somewhere backed up that
   is not a person's Drive. Check the fingerprint after copying:
   `sha256sum TAI_Master.xlsx`.
4. **Never delete it.** It is the evidence behind every decision made before the platform
   existed (BR-307, CR-04).

## The record

| Step | Date | Time (Cairo) | Agreed by | Done by | Evidence |
|---|---|---|---|---|---|
| 1. Scrapers | | | | | |
| 2. Railway portal | | | | | |
| 3. Sheet frozen | | | | | |
| 4. Outside AI key deleted | | | | | |

## Go live means all six

| Condition | How it is shown | Done |
|---|---|---|
| Every new candidate arrives through our own page | Arrivals: no `scraped` week after step 1 | |
| Every active candidate has a step recorded | Funnel report. This is the CPO's instruction to recruiters, not an engineering result. Bring the report to that conversation: 15 of 5,140 is the number everyone remembers. | |
| The funnel report is generated, not typed | `GET /v1/reports/funnel` | |
| The sheet is frozen and kept | Step 3 above | |
| The three outside services are gone | Steps 1, 2 and 4 above | |
| The laptop is off for a full day and nothing stops | The Thursday after the machine lands: `ops.watch` reports `ok` all day | |
