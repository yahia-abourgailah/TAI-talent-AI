# Where the platform connects to (CR-01, OBJ-03)

"No candidate data leaves the company" is enforced in two places:

1. **In the code.** `tests/unit/test_egress.py` lists every module that can open a network
   connection. A new one, or a new network library in a listed one, fails CI until it is added
   here in a reviewed change.
2. **On the network** (job 9, once the machine exists). Allow the destinations below, deny
   everything else, and keep the log.

## The destinations

| Destination | Why | Carries candidate data? | Setting | Code |
|---|---|---|---|---|
| **Object storage** | The original CVs and submissions (BR-107) | Yes, inside our network | `TALENT_BLOB_ENDPOINT` | `src/importer/blobs.py`, `src/infra/probes.py`, `src/infra/dev_storage.py` |
| **The OCR host** | Reading CVs | Yes, on our own host (CR-01) | `TALENT_OCR_BASE_URL` | `src/intake/ocr_http.py` |
| **The company language model** | Reading a CV against a job that the criteria version cannot score (BR-305) | Yes, on the same host as the OCR, inside the company (CR-01) | `TALENT_VLLM_BASE_URL` | `src/assess/model.py` |
| **The CRM webhook** | Telling the dashboard something happened | Ids and codes only | `TALENT_CRM_WEBHOOK_URL` | `src/integrations/webhooks.py` |
| **Redis** | Queue and limits | No | `TALENT_REDIS_URL` | `src/infra/probes.py` |
| **The company identity provider** | Staff sign-in: discovery and signing keys | No. Our own people's accounts, never a candidate's | `TALENT_OIDC_ISSUER` | `src/auth/oidc.py` |
| **The alert chat webhook** | Telling on-call something is wrong | Counts and job kinds only | `TALENT_ALERT_WEBHOOK_URL` | `src/ops/watch.py` |

The database is inside the deployment and is not a destination.

**About the identity provider:** our staff sign in through the company identity provider, and no
candidate appears in that traffic. The platform fetches the provider's public configuration and
signing keys, and nothing else.

**About the alert webhook:** the week 8 brief lists five destinations. The watch added in week 7
is a sixth, and it is listed here so the network rule includes it. Its message holds counts, ages
and job kinds, never a candidate. If the chat service is outside the company, point
`TALENT_ALERT_WEBHOOK_URL` at an internal relay, or leave it empty and let the monitoring agent
read `python -m ops.watch --json`.

## Not destinations

| Code | What it does |
|---|---|
| `src/auth/dev_identity.py` | Signs development tokens locally. It connects nowhere, and it refuses to run outside dev. |
| `src/pipeline/load_openings.py` | An operator's tool that loads open jobs through **our own** API, at the address the operator gives. |

## Nothing else

- **No language model is called.** `TALENT_LLM_BASE_URL` and `TALENT_EMBED_BASE_URL` are read by
  nothing, and `python -m ops.preflight` warns when they are set.
- **The outside AI key is deleted last** (BR-705, step 4). Even if something tried to call it,
  there would be nothing to authenticate with.

## The network rule (job 9)

Once the machine exists:

1. Allow outbound traffic to the hosts behind the six settings above, and to the database.
   Allow DNS and time, if the company's resolvers and time servers are outside the host.
2. Deny and log everything else.
3. Keep a week of the deny log. Attach the count of denied attempts, which should be zero, here:

| From | To | Denied attempts | Log kept at |
|---|---|---|---|
| — | — | — | — |

## What the model is sent, and what it is not

A CV read against a job is the one place a candidate's words are given to a language model. What
goes: the job's own description, and the CV as the reader extracted it — title, employer, years,
education, location, and the CV's text. What does not: the candidate's **name**, and nothing about
age, sex, marital status, nationality, religion or a photograph is asked for or weighed (CR-07).
The name is withheld rather than merely forbidden, because the surest way to keep something out of
an answer is not to send it.

It runs on `vllm.addressinv.com` — the same machine as the CV reader, inside the company — so this
is not an outside AI service and CR-01 holds. Nothing from an answer is logged, and the answer
itself is kept as it came.

The model serves the chatbots too, so the platform asks for one assessment at a time with a
timeout, and waits rather than queueing behind itself (NFR-01). **It never decides anything**: the
score cannot become a tier, the database refuses to store one on an AI evaluation, and a person
reads every assessment before anything happens (BR-306, CR-05).
