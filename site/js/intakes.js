/* Suggesting an organisation and following it onto the radar (ADR 0003). A logged-in user's
   suggestion waits for an admin before anything is fetched; an admin's starts at once
   (ADR 0005 D8). */

import { openLogin } from "./account.js";
import * as api from "./api.js";
import * as board from "./board.js";
import { $, esc, skeleton } from "./dom.js";
import { fillDrawer, isOpen, setHeading, showDrawer } from "./drawer.js";
import { TIERS } from "./facts.js";
import { state } from "./state.js";
import { munich } from "./time.js";

const POLL_MS = 2000;
const STEPS = [
  ["checking", "Check that the website works"],
  ["profiling", "Find out who they are"],
  ["finding", "Find their event pages"],
  ["harvesting", "Collect events, tickets and deadlines"],
  ["done", "On the radar"],
];
const LABEL = {
  queued: "Waiting", checking: "Checking", profiling: "Checking who they are", finding: "Finding event pages",
  harvesting: "Collecting events", done: "Added", rejected: "Rejected", failed: "Failed",
};
const FINAL = new Set(["done", "rejected", "failed"]);
let timer = null;

const label = (intake) => (intake.state === "review" ? (state.admin ? "Needs your decision" : "Waiting for review") : LABEL[intake.state] || intake.state);
/* A suggestion in review either waits for an admin before any fetch, or stopped after the
   check could not confirm the organisation. */
const checked = (intake) => intake.log.some((entry) => entry.step === "checking");

function title(intake) {
  if (intake.name) return intake.name;
  if (intake.profile?.name) return intake.profile.name;
  try {
    return new URL(intake.url).hostname.replace(/^www\./, "");
  } catch {
    return intake.url;
  }
}

function chip(intake) {
  const cls = { done: "ok", review: "warn", rejected: "bad", failed: "bad" }[intake.state] || "accent";
  return `<span class="chip ${cls}">${esc(label(intake))}</span>`;
}

export async function loadIntakes() {
  state.intakes = state.user ? await api.intakes() : [];
}

export function renderIntakes() {
  $("add-org").textContent = state.admin ? "Add organisation" : "Suggest organisation";
  const recent = state.intakes
    .filter((i) => !FINAL.has(i.state) || Date.now() - new Date(i.updated_at).getTime() < 3 * 86400000)
    .slice(0, 5);
  const box = $("intakes");
  box.hidden = !recent.length;
  if (!recent.length) return;
  box.innerHTML = `<h2>${state.admin ? "Recently added" : "Your suggestions"}</h2>` + recent.map((i) => {
    const last = i.log[i.log.length - 1];
    return `<button type="button" class="intake-row" data-intake="${i.id}">
      <span class="main"><span class="title">${esc(title(i))}</span>
        <span class="meta">${esc(last ? last.text : i.url)}</span></span>
      ${chip(i)}
    </button>`;
  }).join("");
}

function upsert(intake) {
  const at = state.intakes.findIndex((i) => i.id === intake.id);
  if (at >= 0) state.intakes[at] = intake;
  else state.intakes.unshift(intake);
}

export function openAddForm(from) {
  if (!state.user) return openLogin("suggest", from);
  clearTimeout(timer);
  showDrawer({ mode: "form", title: state.admin ? "Add an organisation" : "Suggest an organisation", hash: "#add-organisation", from });
  fillDrawer(`
    <p class="note">${state.admin
      ? "Give the organisation's website. The radar checks that it works, asks the LLM who they are, finds their event pages, and collects their events with tickets and registration deadlines."
      : "Give the organisation's website. An admin reviews each suggestion; once it is approved, the radar checks the website, finds the event pages, and collects the events with tickets and registration deadlines."}</p>
    <form class="form" id="intake-form" novalidate>
      <label class="field"><span>Website</span>
        <input type="url" name="url" required placeholder="https://www.cdtm.com" autocomplete="url" inputmode="url"></label>
      <label class="field"><span>Events page <span class="hint">optional, if you know it</span></span>
        <input type="url" name="events_url" placeholder="https://www.example.org/events" inputmode="url"></label>
      <label class="field"><span>Name <span class="hint">optional</span></span>
        <input type="text" name="name" autocomplete="organization"></label>
      <p class="form-error" id="intake-error" role="alert" hidden></p>
      <div class="row-actions"><button type="submit" class="btn primary">${state.admin ? "Check and add" : "Suggest"}</button></div>
    </form>`);
  const form = $("intake-form");
  form.elements.url.focus();
  form.addEventListener("submit", submit);
}

/* What a person types, as the address the database accepts: a scheme is added if missing. */
function address(raw) {
  const text = raw.trim();
  if (!text) return "";
  return /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
}

async function submit(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const fields = form.elements;
  const error = $("intake-error");
  const button = form.querySelector("button[type=submit]");
  error.hidden = true;
  const url = address(fields.url.value);
  if (!url) {
    error.textContent = "Enter the organisation's website.";
    error.hidden = false;
    fields.url.focus();
    return;
  }
  button.disabled = true;
  try {
    const id = await api.suggest({ url, eventsUrl: address(fields.events_url.value), name: fields.name.value.trim() });
    upsert(await api.intake(id));
    renderIntakes();
    openIntake(id);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    button.disabled = false;
  }
}

function stepMarks(intake) {
  const order = STEPS.map(([key]) => key);
  if (intake.state === "done") return order.map(() => "done");
  let at = Math.max(order.indexOf(intake.state), 0);
  let mark = "active";
  if (intake.state === "review" || intake.state === "rejected") [at, mark] = [checked(intake) ? 1 : 0, "stopped"];
  if (intake.state === "failed") {
    const reached = intake.log.map((entry) => order.indexOf(entry.step)).filter((i) => i >= 0);
    [at, mark] = [reached.length ? Math.max(...reached) : 0, "failed"];
  }
  return order.map((_, i) => (i < at ? "done" : i === at ? mark : ""));
}

function profileCard(profile) {
  if (!profile || !profile.summary) return "";
  const verdict = (value, text) => {
    const cls = value === "yes" ? "ok" : value === "no" ? "bad" : "warn";
    const shown = value === "yes" ? text : value === "no" ? `Not: ${text.toLowerCase()}` : `${text}: unclear`;
    return `<span class="chip ${cls}">${esc(shown)}</span>`;
  };
  return `<section class="profile-card"><h3>What the LLM found</h3>
    <p class="description">${esc(profile.summary)}</p>
    <div class="chips">${verdict(profile.legitimate, "Genuine")}${verdict(profile.runs_events, "Runs events")}
      ${verdict(profile.based_in_munich, "In Munich")}<span class="chip">${esc(profile.kind || "kind unknown")}</span>
      ${profile.ai_focus ? `<span class="chip accent">AI focus</span>` : ""}</div>
    ${profile.reasons?.length ? `<ul class="reasons">${profile.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
  </section>`;
}

function decision(intake) {
  const review = intake.state === "review";
  if (!review && intake.state !== "rejected") return "";
  if (!state.admin) {
    const note = !review ? "An admin decided not to add it."
      : checked(intake) ? "The check could not confirm this organisation. An admin decides whether to add it."
      : "An admin decides whether the radar adds this organisation. Nothing is fetched until then.";
    return `<section><h3>${review ? "Waiting for review" : "Not added"}</h3><p class="note">${note}</p></section>`;
  }
  const note = !review ? "If the check got it wrong, you can still add it."
    : checked(intake) ? "The check could not confirm this organisation. Add it if you know it is real and runs events in Munich."
    : "Someone suggested this organisation. Nothing has been fetched yet. Approve it to check the website and collect its events.";
  const add = review && !checked(intake) ? "Approve" : "Add anyway";
  return `<section><h3>${review ? "Your decision" : "Not added"}</h3>
    <p class="note">${note}</p>
    <div class="row-actions"><button type="button" class="btn primary" data-decide="approve">${add}</button>
      ${review ? `<button type="button" class="btn" data-decide="reject">Reject</button>` : ""}</div>
    <p class="form-error" id="decide-error" role="alert" hidden></p></section>`;
}

function render(intake) {
  setHeading(title(intake));
  const marks = stepMarks(intake);
  const symbol = { done: "✓", stopped: "?", failed: "!" };
  const steps = STEPS.map(([, text], i) =>
    `<li class="${marks[i]}"><span class="mark" aria-hidden="true">${symbol[marks[i]] || ""}</span><span>${esc(text)}</span></li>`).join("");
  const r = intake.result;
  const result = r ? `<section><h3>Result</h3>
      <div class="row-actions"><span class="chip ${r.status === "active" ? "ok" : "warn"}">${esc(r.status)}</span>
        ${TIERS.map((t) => `<span class="tier-count"><span class="tier-badge ${t}">${t}</span>${r[t] ?? 0}</span>`).join("")}</div>
      <div class="row-actions">
        <button type="button" class="btn primary" data-open-org="${r.organiser_id}">Show the organisation</button>
        ${r.upcoming ? `<button type="button" class="btn" data-show-org="${r.organiser_id}">Show its events</button>` : ""}
      </div></section>` : "";
  const log = intake.log.length
    ? `<ol class="log">${intake.log.map((entry) => `<li><time>${esc(munich(entry.at)?.time || "")}</time>
        <span class="${esc(entry.level)}">${esc(entry.text)}</span></li>`).join("")}</ol>`
    : `<p class="note">Waiting to start.</p>`;
  fillDrawer(`
    <p class="note"><a href="${esc(intake.url)}" target="_blank" rel="noopener">${esc(intake.url)}</a></p>
    <section><ol class="steps">${steps}</ol></section>
    ${decision(intake)}${result}${profileCard(intake.profile)}
    <section><h3>What happened</h3>${log}</section>`);
  for (const button of document.querySelectorAll("[data-decide]")) {
    button.addEventListener("click", () => decide(intake, button.dataset.decide === "approve"));
  }
}

export async function openIntake(id, from) {
  if (!state.user) return openLogin("intake", from);
  clearTimeout(timer);
  const known = state.intakes.find((i) => i.id === id);
  showDrawer({ mode: "intake", id, title: known ? title(known) : "Adding an organisation", hash: `#intake-${id}`, from });
  if (known) render(known);
  else fillDrawer(skeleton(4));
  await refresh(id);
}

/* Follows the open intake every few seconds until it finishes or waits for a person. */
async function refresh(id) {
  if (!isOpen("intake", id)) return;
  let intake;
  try {
    intake = await api.intake(id);
  } catch (error) {
    fillDrawer(`<p class="note warn">${esc(error.message)}</p>`);
    return;
  }
  if (!isOpen("intake", id)) return;
  if (!intake) {
    fillDrawer(`<p class="note warn">There is no suggestion with this number that you can see.</p>`);
    return;
  }
  const before = state.intakes.find((i) => i.id === id)?.state;
  upsert(intake);
  render(intake);
  renderIntakes();
  if (intake.state === "done" && before !== "done") board.load().catch(board.showBanner);
  if (!FINAL.has(intake.state) && intake.state !== "review") {
    clearTimeout(timer);
    timer = setTimeout(() => refresh(id), POLL_MS);
  }
}

async function decide(intake, approve) {
  try {
    await api.decide(intake.id, approve);
    clearTimeout(timer);
    await refresh(intake.id);
  } catch (error) {
    const note = $("decide-error");
    note.textContent = error.message;
    note.hidden = false;
  }
}
