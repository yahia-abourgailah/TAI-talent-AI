/* The development console: a thin client over the same /v1 API the CRM dashboard will use.
   No framework and no build step on purpose — when something here looks wrong, the answer is in
   the network tab, not in a bundle. */
"use strict";

const state = { token: null, account: null, stages: [], reasons: [], names: new Map() };

/* Who a candidate is, for the tables that only carry ids. The list hands us names; anything else
   is asked for once and remembered, because a page of ids is not something a person can read. */
function remember(rows) {
  for (const row of rows) {
    if (row.id && row.full_name) state.names.set(row.id, row.full_name);
  }
}

async function nameOf(candidateId) {
  if (!candidateId) return null;
  if (state.names.has(candidateId)) return state.names.get(candidateId);
  try {
    const candidate = await api(`/v1/candidates/${candidateId}`);
    const field = (candidate.fields || {}).full_name || {};
    state.names.set(candidateId, field.value || null);
  } catch {
    state.names.set(candidateId, null); // out of scope, or locked: the id is all we may say
  }
  return state.names.get(candidateId);
}

/** The cell for a candidate: their name when we may know it, with the id underneath. */
function candidateCell(candidateId, onclick) {
  const name = state.names.get(candidateId);
  const label = el("span", {}, name || candidateId);
  const cell = el(
    "span",
    { class: "row", style: "flex-direction:column;gap:0;align-items:flex-start" },
    onclick ? el("button", { class: "quiet", style: "padding:0", onclick }, label) : label,
    name ? el("span", { class: "muted mono", style: "font-size:11px" }, candidateId) : null
  );
  if (!name) {
    nameOf(candidateId).then((found) => {
      if (found) {
        label.textContent = found;
        cell.append(el("span", { class: "muted mono", style: "font-size:11px" }, candidateId));
      }
    });
  }
  return cell;
}
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
};
const text = (value, fallback = "—") =>
  value === null || value === undefined || value === "" ? fallback : String(value);
const when = (value) => (value ? new Date(value).toLocaleString() : "—");

function say(message, kind = "ok") {
  $("message").replaceChildren(el("div", { class: `msg ${kind}` }, message));
  if (kind === "ok") setTimeout(() => $("message").replaceChildren(), 4000);
}

/* --- talking to the API ------------------------------------------------------------------- */

async function api(path, { method = "GET", body, idempotent = false } = {}) {
  const headers = { Accept: "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotent) headers["Idempotency-Key"] = crypto.randomUUID();
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload && payload.error ? payload.error : {};
    const failure = new Error(error.message || `${response.status} ${response.statusText}`);
    failure.code = error.code || String(response.status);
    failure.details = error.details;
    throw failure;
  }
  return payload;
}

async function guard(action) {
  try {
    await action();
  } catch (failure) {
    say(`${failure.message} (${failure.code})`, "err");
  }
}

/* --- signing in -------------------------------------------------------------------------- */

async function loadAccounts() {
  const accounts = await api("/dev/accounts");
  $("accounts").replaceChildren(
    ...accounts.map((account) =>
      el(
        "button",
        {
          // The endpoint calls the key "account"; it is what /dev/token wants back.
          class: account.account === state.account ? "primary" : "",
          onclick: () => guard(() => signIn(account.account)),
          title: account.purpose,
        },
        `${account.name} · ${account.roles.join(", ")}`
      )
    )
  );
}

async function signIn(account) {
  if (!account) throw Object.assign(new Error("No account to sign in as."), { code: "no_account" });
  const answer = await api("/dev/token", { method: "POST", body: { account } });
  state.token = answer.access_token;
  state.account = account;
  localStorage.setItem("talent.account", account);
  const me = await api("/v1/me");
  $("who").textContent = `${me.name || me.subject} · ${me.roles.join(", ")}`;
  $("me").replaceChildren(
    el("table", {},
      el("tbody", {},
        ...Object.entries(me).map(([key, value]) =>
          el("tr", {}, el("td", { class: "id" }, key), el("td", {}, JSON.stringify(value)))
        )
      )
    )
  );
  await loadAccounts();
  await loadReference();
  say(`Signed in as ${account}.`);
}

async function loadReference() {
  const [stages, reasons] = await Promise.all([
    api("/v1/reference/stages"),
    api("/v1/reference/reasons"),
  ]);
  state.stages = stages.stages || stages.items || [];
  state.reasons = reasons.reasons || reasons.items || [];
  const picker = $("p-stage");
  picker.replaceChildren(
    el("option", { value: "" }, "any"),
    ...state.stages.map((stage) => el("option", { value: stage.code }, stage.label || stage.code))
  );
}

/* --- requisitions ------------------------------------------------------------------------ */

async function loadRequisitions() {
  const page = await api("/v1/requisitions?limit=50");
  $("requisitions").replaceChildren(
    table(
      ["id", "title", "brand", "department", "track", "status", "on the page"],
      page.items.map((row) => ({
        cells: [
          row.id,
          text(row.title),
          row.brand,
          row.department,
          row.track,
          el("span", { class: `pill ${row.status === "open" ? "ok" : ""}` }, row.status),
          row.public ? "yes" : "no",
        ],
        onclick: () => guard(() => openRequisition(row.id)),
      })),
      "No requisitions yet. Create one above."
    )
  );
}

async function openRequisition(id) {
  const [requisition, applications] = await Promise.all([
    api(`/v1/requisitions/${id}`),
    api(`/v1/applications?requisition_id=${id}&limit=50`),
  ]);
  $("requisition-detail").replaceChildren(
    el("div", { class: "card" },
      el("h2", { style: "margin-top:0" }, `${text(requisition.title)} · ${requisition.id}`),
      el("p", { class: "muted" },
        `${requisition.brand} · ${requisition.department} · track ${requisition.track} · ` +
        `${requisition.headcount} to hire · owner ${text(requisition.owner_id)}`),
      el("div", { class: "row" },
        el("button", { onclick: () => guard(() => makeJobPost(id)) }, "New job post link"),
        requisition.status === "open"
          ? el("button", { onclick: () => guard(() => closeRequisition(id)) }, "Close")
          : null
      ),
      el("h2", {}, `Applications (${applications.items.length})`),
      table(
        ["application", "candidate", "stage", "outcome"],
        applications.items.map((row) => ({
          cells: [
            row.id,
            candidateCell(row.candidate_id),
            row.current_stage,
            text(row.outcome),
          ],
          onclick: () => guard(() => openApplication(row.id)),
        })),
        "Nobody has applied yet."
      )
    )
  );
}

async function makeJobPost(id) {
  const label = prompt("What is this link for? (e.g. LinkedIn post, 20 September)");
  if (!label) return;
  const post = await api(`/v1/requisitions/${id}/job-posts`, {
    method: "POST",
    idempotent: true,
    body: { channel: "linkedin", label },
  });
  const link = `${location.origin}/careers/?job=${id}&code=${post.code}`;
  say(`Job post ${post.code} created. Link: ${link}`);
}

async function closeRequisition(id) {
  const reason = prompt("Why is it closing?");
  if (!reason) return;
  await api(`/v1/requisitions/${id}/close`, { method: "POST", body: { reason } });
  say("Closed.");
  await loadRequisitions();
}

/* --- candidates --------------------------------------------------------------------------- */

async function searchCandidates() {
  const body = {};
  if ($("c-name").value.trim()) body.full_name = $("c-name").value.trim();
  if ($("c-phone").value.trim()) body.phone = $("c-phone").value.trim();
  if ($("c-email").value.trim()) body.email = $("c-email").value.trim();
  if (!Object.keys(body).length) return listCandidates();
  const page = await api("/v1/candidates/search", { method: "POST", body });
  showCandidates(page.items, "Nobody matches every value you sent.");
}

async function listCandidates() {
  const page = await api("/v1/candidates?limit=25");
  showCandidates(page.items, "No candidates you can see.");
}

function showCandidates(items, empty) {
  remember(items);
  $("candidates").replaceChildren(
    table(
      ["name", "id", "source", "added", "archived"],
      items.map((row) => ({
        cells: [
          row.full_name || el("span", { class: "muted" }, "not recorded"),
          el("span", { class: "mono" }, row.id),
          row.source,
          when(row.created_at),
          row.archived ? "yes" : "no",
        ],
        onclick: () => guard(() => openCandidate(row.id)),
      })),
      empty
    )
  );
}

async function openCandidate(id) {
  const [candidate, evaluations, group] = await Promise.all([
    api(`/v1/candidates/${id}`),
    api(`/v1/candidates/${id}/evaluations`).catch(() => ({ items: [] })),
    api(`/v1/candidates/${id}/group`).catch(() => null),
  ]);
  const fields = Object.entries(candidate.fields || {}).map(([name, field]) => ({
    cells: [
      name,
      field.state === "not_recorded"
        ? el("span", { class: "pill" }, "not recorded")
        : text(field.value),
      text(field.source),
      el(
        "span",
        { class: `pill ${field.verification_status === "verified" ? "ok" : "warn"}` },
        text(field.verification_status, "unchecked")
      ),
      field.state === "not_recorded" || field.verification_status === "verified"
        ? ""
        : el("span", { class: "row" },
            el("button", { onclick: () => guard(() => checkField(id, name, null)) }, "Correct"),
            el("button", { onclick: () => guard(() => checkField(id, name, field.value)) },
              "Right as it is")
          ),
    ],
  }));
  const named = (candidate.fields || {}).full_name || {};
  if (named.value) state.names.set(candidate.id, named.value);
  $("candidate-detail").replaceChildren(
    el("div", { class: "card" },
      el("h2", { style: "margin-top:0" },
        named.value ? `${named.value} · ${candidate.id}` : candidate.id),
      el("p", { class: "muted" },
        `from ${candidate.source} · added ${when(candidate.created_at)}` +
        (group && group.members.length > 1 ? ` · read under ${group.primary_candidate_id}` : "")),
      el("div", { class: "row" },
        el("button", { onclick: () => guard(() => applyCandidate(id)) }, "Add to a requisition"),
        el("button", { onclick: () => guard(() => withdrawCandidate(id)) },
          "Record “stop keeping my data”")
      ),
      el("h2", {}, "Fields"),
      el("p", { class: "muted", style: "margin-top:0" },
        "A typed-in candidate is checked field by field: the old row is always kept beside the " +
        "new one. Once nothing is left unchecked, the review item closes by itself."),
      table(["field", "value", "where it came from", "checked", ""], fields, "Nothing recorded."),
      el("h2", {}, "Evaluations"),
      table(
        ["evaluation", "score", "tier", "outcome", "flags", "when"],
        (evaluations.items || []).map((row) => ({
          cells: [
            row.id,
            text(row.score),
            el("span", { class: "pill info" }, text(row.tier)),
            text(row.outcome),
            (row.flags || []).join("; ") || "—",
            when(row.evaluated_at || row.recorded_at),
          ],
        })),
        "Not scored yet: scoring happens when the candidate is added to a requisition."
      )
    )
  );
}

async function checkField(candidateId, field, current) {
  let value = current;
  if (current === null) {
    value = prompt(`What is the right ${field}?`, "");
    if (value === null || !value.trim()) return;
  }
  await api(`/v1/candidates/${candidateId}/fields/${field}/verification`, {
    method: "POST",
    idempotent: true,
    body: { value: current === null ? value.trim() : null },
  });
  say(`${field} checked.`);
  await openCandidate(candidateId);
}

async function typeInCandidate() {
  const fields = {};
  for (const name of ["full_name", "phone", "email", "current_title", "current_employer",
                      "location", "education", "years_experience", "age"]) {
    const value = $(`n-${name}`).value.trim();
    if (value) fields[name] = value;
  }
  if (!Object.keys(fields).length) return say("Fill in at least one field.", "err");
  const created = await api("/v1/candidates", {
    method: "POST", idempotent: true, body: { fields },
  });
  say(`${created.id} created, unchecked, and waiting in the review queue: a person checks a ` +
      "typed-in candidate before anyone contacts them.");
  await listCandidates();
  await openCandidate(created.id);
}

async function applyCandidate(candidateId) {
  const page = await api("/v1/requisitions?limit=50");
  const open = page.items.filter((row) => row.status === "open");
  if (!open.length) return say("No open requisition to add them to.", "err");
  const choice = prompt(
    `Which requisition?\n${open.map((r) => `${r.id} — ${text(r.title)}`).join("\n")}`,
    open[0].id
  );
  if (!choice) return;
  let application;
  try {
    application = await api("/v1/applications", {
      method: "POST",
      idempotent: true,
      body: { requisition_id: choice.trim(), candidate_id: candidateId },
    });
  } catch (failure) {
    if (failure.code === "candidate_not_eligible") {
      return say(
        "This one came from the old sheet, and migrated candidates get no applications until " +
        "Karim rules on OPN-11. Type a candidate in, or apply through the careers page, to try " +
        "the pipeline.", "err");
    }
    throw failure;
  }
  say(`Application ${application.id} created; scoring runs in the background.`);
  await openApplication(application.id);
  show("pipeline");
}

async function withdrawCandidate(candidateId) {
  const how = prompt("How did they ask? phone, whatsapp, email, in_person, letter, other", "phone");
  if (!how) return;
  await api(`/v1/candidates/${candidateId}/withdrawals`, {
    method: "POST",
    idempotent: true,
    body: { asked_how: how.trim(), note: "Recorded from the development console" },
  });
  say("Recorded. The record is locked: it now reads as not found for everyone but an admin.");
  $("candidate-detail").replaceChildren();
  await listCandidates();
}

/* --- pipeline ----------------------------------------------------------------------------- */

async function loadApplications() {
  const stage = $("p-stage").value;
  const page = await api(`/v1/applications?limit=50${stage ? `&stage=${stage}` : ""}`);
  $("applications").replaceChildren(
    table(
      ["application", "candidate", "requisition", "stage", "since", "outcome"],
      page.items.map((row) => ({
        cells: [
          row.id,
          candidateCell(row.candidate_id, (event) => {
            event.stopPropagation();
            show("candidates");
            guard(() => openCandidate(row.candidate_id));
          }),
          row.requisition_id,
          el("span", { class: "pill info" }, row.current_stage),
          when(row.stage_since),
          text(row.outcome),
        ],
        onclick: () => guard(() => openApplication(row.id)),
      })),
      "No applications yet. Add a candidate to a requisition from the Candidates tab."
    )
  );
}

async function openApplication(id) {
  const [application, history] = await Promise.all([
    api(`/v1/applications/${id}`),
    api(`/v1/applications/${id}/transitions`),
  ]);
  const moves = (application.allowed_transitions || []).map((stage) =>
    el("button", { onclick: () => guard(() => move(application, stage)) }, `→ ${stage}`)
  );
  $("application-detail").replaceChildren(
    el("div", { class: "card" },
      el("h2", { style: "margin-top:0" },
        `${application.id} · ${state.names.get(application.candidate_id) || ""}`.trim() +
        ` (${application.candidate_id})`),
      el("p", { class: "muted" },
        `stage ${application.current_stage} since ${when(application.stage_since)}` +
        ` · owner ${text(application.owner_id)} · outcome ${text(application.outcome)}`),
      el("div", { class: "steps" }, moves.length ? moves : el("span", { class: "muted" },
        "Final: nothing moves from here.")),
      el("h2", {}, "Every move"),
      table(
        ["#", "from", "to", "reason", "who", "when"],
        (history.items || []).map((row) => ({
          cells: [row.sequence, text(row.from_stage, "—"), row.to_stage, text(row.reason_code),
                  `${row.actor.id} (${row.actor.kind})`, when(row.occurred_at)],
        })),
        "No moves yet."
      )
    )
  );
}

async function move(application, to) {
  const rejected = /reject/i.test(to);
  let reason = null;
  if (rejected) {
    const codes = state.reasons.map((r) => `${r.code} — ${r.label}`).join("\n");
    reason = prompt(`A rejection needs a reason from the list:\n${codes}`, state.reasons[0]?.code);
    if (!reason) return;
  }
  await api(`/v1/applications/${application.id}/transitions`, {
    method: "POST",
    idempotent: true,
    body: { from_stage: application.current_stage, to_stage: to, reason_code: reason },
  });
  say(`Moved to ${to}.`);
  await openApplication(application.id);
  await loadApplications();
}

/* --- review queue -------------------------------------------------------------------------- */

async function loadQueue() {
  const kind = $("q-kind").value;
  const page = await api(`/v1/review-queue?limit=50${kind ? `&kind=${kind}` : ""}`);
  $("queue").replaceChildren(
    table(
      ["item", "kind", "why it is here", "about", "waiting since", ""],
      (page.items || []).map((row) => ({
        cells: [
          row.id,
          el("span", { class: "pill warn" }, row.kind),
          row.reason,
          row.candidate_id
            ? candidateCell(row.candidate_id, (event) => {
                event.stopPropagation();
                show("candidates");
                guard(() => openCandidate(row.candidate_id));
              })
            : text(row.application_id),
          when(row.waiting_since),
          el("span", { class: "row" },
            el("button", { onclick: (event) => { event.stopPropagation();
              guard(() => resolve(row, "checked")); } }, "Checked"),
            el("button", { onclick: (event) => { event.stopPropagation();
              guard(() => resolve(row, "dismiss")); } }, "Dismiss")
          ),
        ],
      })),
      "Nothing is waiting for a person. That is the good answer."
    )
  );
}

async function resolve(item, decision) {
  if (item.kind === "unverified_candidate" && decision === "checked") {
    say(
      "Check the fields first: a typed-in candidate is cleared field by field, and this item " +
      "closes itself when none is left unchecked. Opening the candidate now.",
      "err"
    );
    show("candidates");
    return openCandidate(item.candidate_id);
  }
  const body = { decision };
  if (decision === "dismiss") {
    body.reason = prompt("Why are you dismissing it?");
    if (!body.reason) return;
  }
  // The queue says where each kind of item is resolved; guessing from its shape would break the
  // day a new kind arrives.
  await api(item.resolve_at, { method: "POST", idempotent: true, body });
  say("Resolved.");
  await loadQueue();
}

/* --- reports ------------------------------------------------------------------------------- */

async function loadReports() {
  const funnel = await api("/v1/reports/funnel");
  const all = funnel.all_candidates || {};
  const queue = funnel.review_queue || {};
  $("report-summary").replaceChildren(
    el("div", { class: "grid" },
      stat("Candidates", all.candidates),
      stat("Reachable", all.contactable),
      stat("Reachable",
        all.contactability === undefined || all.contactability === null
          ? null
          : `${(all.contactability * 100).toFixed(1)}%`),
      stat("Waiting for a person", queue.open),
      stat("Oldest wait (h)", queue.oldest_waiting_hours)
    ),
    el("p", { class: "muted" },
      `Step list ${funnel.stage_list}` +
      (funnel.provisional ? " — provisional: TA has not confirmed it yet." : ".")),
    (queue.kinds || []).length
      ? table(["waiting", "open", "oldest (h)"],
          queue.kinds.map((kind) => ({
            cells: [kind.kind, String(kind.open), text(kind.oldest_waiting_hours)],
          })), "")
      : null
  );
  const groups = funnel.groups || [];
  if (!groups.length) {
    $("report-funnel").replaceChildren(
      el("p", { class: "muted" },
        "No applications yet, so there is no funnel to draw. Type a candidate in and add them to " +
        "a requisition.")
    );
    return;
  }
  const stages = groups[0].stages || [];
  $("report-funnel").replaceChildren(
    el("h2", { style: "margin-top:0" }, "Built from the moves people recorded, never typed"),
    table(
      ["group", "applications", ...stages.map((stage) => stage.label || stage.stage)],
      groups.map((group) => ({
        cells: [
          text(group.group, "everyone"),
          String(group.applications ?? 0),
          ...(group.stages || []).map((stage) =>
            stage.reached
              ? `${stage.reached}` + (stage.still_here ? ` (${stage.still_here} now)` : "")
              : "—"),
        ],
      })),
      "Nothing to show."
    )
  );
}

const stat = (label, value) =>
  el("div", { class: "stat" },
    el("div", { class: "n" }, value === null || value === undefined ? "—" : String(value)),
    el("div", { class: "k" }, label));

/* --- small helpers ------------------------------------------------------------------------- */

function table(headers, rows, empty) {
  if (!rows.length) return el("p", { class: "muted" }, empty);
  return el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map((head) => el("th", {}, head)))),
    el("tbody", {},
      ...rows.map((row) =>
        el("tr", { class: row.onclick ? "clickable" : "", onclick: row.onclick || null },
          ...row.cells.map((cell, index) =>
            el("td", { class: index === 0 ? "id" : "" }, cell))
        )
      )
    )
  );
}

function show(name) {
  for (const button of document.querySelectorAll("nav.tabs button")) {
    button.setAttribute("aria-current", String(button.dataset.tab === name));
  }
  for (const section of document.querySelectorAll("main section")) {
    section.classList.toggle("hidden", section.id !== `tab-${name}`);
  }
  location.hash = name;
  const loaders = {
    requisitions: loadRequisitions,
    candidates: listCandidates,
    pipeline: loadApplications,
    queue: loadQueue,
    reports: loadReports,
  };
  if (state.token && loaders[name]) guard(loaders[name]);
  if (!state.token && name !== "signin") say("Sign in first.", "err");
}

/* --- wiring -------------------------------------------------------------------------------- */

document.querySelectorAll("nav.tabs button").forEach((button) =>
  button.addEventListener("click", () => show(button.dataset.tab))
);
$("r-create").addEventListener("click", () =>
  guard(async () => {
    const body = {
      title: $("r-title").value.trim(),
      brand: $("r-brand").value.trim(),
      department: $("r-dept").value.trim(),
      track: $("r-track").value,
      headcount: Number($("r-head").value),
      team: $("r-team").value.trim(),
      location: $("r-location").value.trim() || null,
      public: $("r-public").value === "true",
    };
    const created = await api("/v1/requisitions", { method: "POST", idempotent: true, body });
    say(`Created ${created.id}.`);
    await loadRequisitions();
  })
);
$("c-search").addEventListener("click", () => guard(searchCandidates));
$("n-create").addEventListener("click", () => guard(typeInCandidate));
$("c-recent").addEventListener("click", () => guard(listCandidates));
$("p-reload").addEventListener("click", () => guard(loadApplications));
$("q-reload").addEventListener("click", () => guard(loadQueue));
$("q-kind").addEventListener("change", () => guard(loadQueue));

guard(async () => {
  await loadAccounts();
  const remembered = localStorage.getItem("talent.account");
  if (remembered) await signIn(remembered);
  show(location.hash.slice(1) || (state.token ? "requisitions" : "signin"));
});
