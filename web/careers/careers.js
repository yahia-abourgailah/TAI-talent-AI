/* The candidate's side, using only the public endpoints: no sign-in, no candidate data in a URL.
   It is here so the whole flow can be tried end to end; the real page is the website team's. */
"use strict";

const FIELDS = ["full_name", "phone", "whatsapp", "email", "current_title", "current_employer",
  "location", "education", "years_experience"];
const LABELS = {
  full_name: "Full name", phone: "Phone", whatsapp: "WhatsApp", email: "Email",
  current_title: "Current title", current_employer: "Current employer", location: "Location",
  education: "Education", years_experience: "Years of experience",
};

// How long a candidate is asked to wait before the form is theirs to fill. The reader usually
// answers in 3-15 seconds; if it is slower than this, waiting is our problem, not theirs.
const PATIENCE_SECONDS = 20;

const state = {
  job: null, wording: null, upload: null, token: null, polls: 0, reading: false, misses: 0,
};
const $ = (id) => document.getElementById(id);
const say = (message, kind = "ok") => {
  $("message").replaceChildren(Object.assign(document.createElement("div"),
    { className: `msg ${kind}`, textContent: message }));
  if (kind === "ok") setTimeout(() => $("message").replaceChildren(), 5000);
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (payload && payload.error) || {};
    const failure = new Error(error.message || `${response.status}`);
    failure.code = error.code || String(response.status);
    throw failure;
  }
  return payload;
}
const guard = (action) => action().catch((failure) => say(`${failure.message} (${failure.code})`, "err"));

/* 1 · the jobs a candidate may apply to */
async function loadJobs() {
  const page = await api("/v1/public/requisitions?limit=20");
  const chosen = new URLSearchParams(location.search).get("job");
  if (!page.items.length) {
    $("jobs").textContent = "No jobs are open on the page right now. In the console, create a " +
      "requisition with “on the careers page: yes”.";
    return;
  }
  $("jobs").replaceChildren(...page.items.map((job) => {
    const button = document.createElement("button");
    button.className = "job";
    button.textContent = `${job.title} — ${job.brand}, ${job.department}` +
      (job.location ? ` · ${job.location}` : "");
    button.setAttribute("aria-pressed", String(job.requisition_id === chosen));
    button.addEventListener("click", () => {
      state.job = job.requisition_id;
      for (const other of document.querySelectorAll(".job")) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
    });
    if (job.requisition_id === chosen) state.job = job.requisition_id;
    return button;
  }));
}

/* 2 · the CV, read by the platform */
async function upload() {
  const file = $("cv").files[0];
  if (!file) return say("Choose a file first.", "err");
  const form = new FormData();
  form.append("file", file);
  $("cv-status").textContent = "Uploading…";
  const answer = await api("/v1/public/cv-uploads", { method: "POST", body: form });
  state.upload = answer.upload_id;
  state.token = answer.upload_token;
  $("cv-status").textContent = "Reading it…";
  state.polls = 0;
  await poll();
}

async function poll() {
  let found;
  try {
    found = await api(`/v1/public/cv-uploads/${state.upload}`, {
      headers: { "X-Upload-Token": state.token },
    });
    state.misses = 0;
  } catch (failure) {
    // One failed ask is not an answer: the platform may be restarting, or the connection may have
    // hiccuped. Only give up after several in a row, and never leave the candidate stuck.
    state.misses += 1;
    if (state.misses < 5) {
      $("cv-status").textContent = "Still reading your CV…";
      setTimeout(() => guard(poll), 3000);
      return;
    }
    state.reading = false;
    $("cv-status").textContent =
      "We have lost track of your CV while reading it. Fill the form in below and send it — the " +
      "file itself is kept, and a recruiter sees it.";
    return;
  }
  if (found.status === "ready" && found.fields) {
    state.reading = false;
    const filled = fillForm(found.fields);
    $("cv-status").textContent = filled
      ? "Read. Please check what we have — the highlighted boxes came from your CV."
      : "We read it but found nothing to fill in. Please add your details below.";
    return;
  }
  if (found.status === "failed") {
    state.reading = false;
    $("cv-status").textContent =
      "We could not read this one, so please fill the form in yourself. Your CV is kept and a " +
      "recruiter will see it.";
    return;
  }

  state.polls += 1;
  if (state.polls === PATIENCE_SECONDS) {
    // Still reading. That is our problem to wait on, not the candidate's: the form is theirs now,
    // and if the reader answers while they are still here, the empty boxes fill themselves.
    $("cv-status").textContent =
      "Your CV is still being read — no need to wait. Fill the form in below and send it; we " +
      "keep reading in the background, and anything you leave empty is filled in if it arrives.";
    $("fields").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } else if (state.polls < PATIENCE_SECONDS) {
    $("cv-status").textContent = `Reading it… (${state.polls}s)`;
  }
  if (state.polls < 180) {
    state.reading = true;
    setTimeout(() => guard(poll), state.polls < PATIENCE_SECONDS ? 1000 : 5000);
  } else {
    state.reading = false;
    $("cv-status").textContent =
      "Your CV has not come back. Fill the form in yourself — the file is kept, and a recruiter " +
      "sees it either way.";
  }
}

function fillForm(fields) {
  let filled = 0;
  for (const name of FIELDS) {
    const field = fields[name];
    const input = $(`f-${name}`);
    if (!input || !field || field.value === null || field.value === undefined) continue;
    // Never write over what the candidate typed while they were waiting for us.
    if (input.value.trim()) continue;
    input.value = field.value;
    input.parentElement.classList.add("prefilled");
    filled += 1;
  }
  return filled;
}

/* 3 · the form */
function buildForm() {
  $("fields").replaceChildren(...FIELDS.map((name) => {
    const wrapper = document.createElement("div");
    const label = document.createElement("label");
    label.setAttribute("for", `f-${name}`);
    label.textContent = LABELS[name];
    const input = document.createElement("input");
    input.id = `f-${name}`;
    input.style.width = "100%";
    wrapper.append(label, input);
    return wrapper;
  }));
}

/* 4 · consent, in the words the candidate was actually shown */
async function loadWording() {
  state.wording = await api("/v1/public/consent-wording");
  showWording();
}
function showWording() {
  const arabic = $("language").value === "ar";
  $("wording").dir = arabic ? "rtl" : "ltr";
  $("wording").textContent = arabic ? state.wording.text_ar : state.wording.text_en;
}

async function submit() {
  if (!state.job) return say("Choose a job first.", "err");
  if (!$("agreed").checked) return say("We cannot keep your details without your agreement.", "err");
  const fields = {};
  for (const name of FIELDS) {
    const value = $(`f-${name}`).value.trim();
    if (value) fields[name] = value;
  }
  // A phone or a WhatsApp number, and the number itself: an application nobody can answer is no
  // use to the candidate or to us (BR-109).
  const channel = $("channel").value;
  const number = (fields[channel] || fields.phone || fields.whatsapp || "").trim();
  if (!number) {
    return say(
      `Please give the ${channel === "whatsapp" ? "WhatsApp" : "phone"} number a recruiter should ` +
      "use — it is the only way we can come back to you.",
      "err"
    );
  }
  fields[channel] = number;
  const channels = [channel];
  if (fields.email) channels.push("email");
  const body = {
    requisition_id: state.job,
    contact_channel: { type: channel, value: number },
    fields,
    consent: {
      agreed: true,
      agreed_at: new Date().toISOString(),
      channels,
      language: $("language").value,
      wording_version: state.wording.wording_version,
    },
  };
  if (state.upload) {
    body.upload_id = state.upload;
  }
  const code = new URLSearchParams(location.search).get("code");
  if (code) body.tracking_code = code;
  const headers = { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() };
  if (state.token) headers["X-Upload-Token"] = state.token;
  const answer = await api("/v1/public/applications", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  $("step-done").classList.remove("hidden");
  $("done").textContent =
    `Thank you. Your application is ${answer.application_id} and a recruiter will be in touch. ` +
    "You are not told a score, a tier or a decision here — a person makes those.";
  for (const id of ["step-job", "step-cv", "step-form", "step-consent"]) {
    $(id).setAttribute("aria-disabled", "true");
  }
  $("step-done").scrollIntoView({ behavior: "smooth" });
}

buildForm();
$("upload").addEventListener("click", () => guard(upload));
$("submit").addEventListener("click", () => guard(submit));
$("language").addEventListener("change", () => state.wording && showWording());
guard(loadJobs);
guard(loadWording);
