# The public intake, reviewed

**17 September 2026 · the careers-page endpoints, which take files and forms from strangers**

Everything under `/v1/public` is unauthenticated by design: a candidate has no account. This is a
read of those paths end to end — the upload middleware, the upload and status handlers, the file
sniffer, the apply flow, consent, the rate limiter and what reaches storage and the logs — against
the ways they could be abused. It is not a penetration test; it is the review week 7 asks for
before the page goes live.

## Fixed in this review

| What | Why it mattered | Now |
|---|---|---|
| **No CORS at all** | The careers page is a browser on another origin (D-WEB-2), so its calls would have failed; and adding CORS carelessly later is how a wildcard ends up in production | `TALENT_CORS_ORIGINS` names the exact origins allowed — the careers site and the dashboard. Empty by default, credentials off, and only the methods and headers we actually use |
| **Rate limits keyed on the wrong address** | Behind a load balancer every candidate looked like one address, so one noisy client would have used up everyone's allowance — and a candidate could have claimed any address with a header | The address is the peer's, unless `TALENT_TRUSTED_PROXY_HOPS` says how many proxies stand in front, in which case we step back exactly that many entries in `X-Forwarded-For` |
| **No size limit on public forms** | Only the upload path checked `Content-Length`. A 100 MB JSON body to `/v1/public/applications` would have been read into memory first | Every public POST that is not the file upload is refused above 64 KB, before the body is read |

## Checked, and sound as built

- **The file is what it says it is.** The type comes from the content, never the name or the
  browser's `Content-Type`: `%PDF-`, the PNG and JPEG signatures, or a ZIP that really contains
  `word/document.xml`. Anything else is refused with 415.
- **A ZIP cannot be used to exhaust us.** The DOCX check reads the archive's index only — it never
  decompresses — and stops after 2,000 entries, so a zip bomb is refused rather than expanded.
- **10 MB is enforced twice**: on `Content-Length` before the body is read, and again while
  reading, so a lying header gains nothing. An upload with no length at all is refused (411).
- **We never parse the file ourselves.** It is stored byte for byte and handed to the OCR service.
  A malicious PDF has nothing to attack here.
- **The upload token is a real secret**: 256 bits from `secrets`, shown once, stored only as its
  SHA-256, expires in 24 hours, sent in a header rather than a URL. The status of an upload cannot
  be read without it, and a wrong or expired token is "not found", the same as an unknown id.
- **Nothing a candidate sent comes back in an error.** Every failure is the frozen envelope with a
  stable code; the messages name limits and types, never the input.
- **Hidden content is never revealed.** A CV flagged for hidden text is `ready` like any other; only
  the review queue knows (BR-308).
- **Storage cannot be overwritten.** Keys are content hashes and writes are put-if-absent, so one
  upload can never replace another's file, and the same file twice is one stored object.
- **Staff downloads are inert**: `Content-Disposition: attachment`, `X-Content-Type-Options:
  nosniff`, `Cache-Control: no-store`, and every download is recorded with the name of who did it.
- **Nothing outbound is candidate-controlled.** The OCR URL comes from configuration; there is no
  path where a submitted value becomes a request we make, so there is no SSRF here.
- **The logs and the alerts hold no candidate data** — ids, codes and counts only — and the rate
  limiter hashes the address it counts rather than keeping it.
- **Applying needs an `Idempotency-Key`**, a contact channel (BR-109) and consent carrying the
  wording version the candidate was actually shown; the same key with a different body is refused.

## Known limits, and whose they are

- **The rate limiter counts in one process's memory.** With more than one API process the real
  limit is the sum. Before more than one process runs, move it to the Redis already in the stack.
- **Bot protection is the website's** (D-WEB-3). We have rate and size limits; we do not have a
  CAPTCHA, and a determined script can still upload ten CVs every ten minutes per address.
- **The file is opened by the OCR service**, so the risk of a malicious document lives there. The
  OCR team should confirm what their reader does with embedded scripts and external references.
- **Idempotency records keep the request body for 24 hours**, so a copy of an application exists
  there briefly. It expires on its own; nothing else reads it.
- **TLS is the proxy's job.** Nothing here should ever be reachable over plain HTTP.

## Before the page goes live

1. Set `TALENT_CORS_ORIGINS` to the real careers-page origin (and the dashboard's).
2. Set `TALENT_TRUSTED_PROXY_HOPS` to the number of proxies actually in front of the API — one for
   a single load balancer. Getting this wrong in either direction breaks the rate limits.
3. Move the rate limiter to Redis if more than one API process will run.
4. Ask the website team what bot protection the page has, and the OCR team how their reader handles
   hostile files.
