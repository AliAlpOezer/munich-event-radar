/* Suggesting an organisation (ADR 0003) or adding one event from its page (ADR 0014), and
   following either onto the radar. A logged-in user's suggestion waits for an admin before
   anything is fetched; an admin's starts at once (ADR 0005 D8). An added event is checked
   at once, and an admin can take it off the board again. */

import { openLogin } from "./account.js";
import * as api from "./api.js";
import * as board from "./board.js";
import { $, esc, skeleton } from "./dom.js";
import { fillDrawer, isOpen, setHeading, showDrawer } from "./drawer.js";
import { DROP_LABEL, TIER_MEANING, TIERS } from "./facts.js";
import {
  FINAL, STEPS, candidates, checked, found, isEvent, label, needsDay, pageName, recent, stepMarks, title,
} from "./intake-facts.js";
import { state } from "./state.js";
import { munich, today } from "./time.js";

const POLL_MS = 2000;
let timer = null;

function chip(intake) {
  const waiting = intake.state === "done" && intake.result?.waiting;
  const cls = waiting ? "warn" : { done: "ok", review: "warn", rejected: "bad", failed: "bad", removed: "bad" }[intake.state] || "accent";
  return `<span class="chip ${cls}">${esc(label(intake, state.admin))}</span>`;
}

export async function loadIntakes() {
  state.intakes = state.user ? await api.intakes() : [];
}

/* The line under an intake's name: where it stands, or the page of an event it found. */
function meta(intake) {
  if (isEvent(intake) && intake.result?.title) return pageName(intake.url, true);
  if (found(intake) && intake.state === "review") return `${isEvent(intake) ? "Event" : "Organisation"}. ${intake.log[0].text}`;
  const last = intake.log[intake.log.length - 1];
  return last ? last.text : intake.url;
}

function list(box, intakes, heading) {
  box.hidden = !intakes.length;
  box.innerHTML = intakes.length ? `<h2>${heading}</h2>` + intakes.map((i) => {
    return `<button type="button" class="intake-row" data-intake="${i.id}">
      <span class="main"><span class="title">${esc(title(i))}</span>
        <span class="meta">${esc(meta(i))}</span></span>
      ${chip(i)}
    </button>`;
  }).join("") : "";
}

export function renderIntakes() {
  $("add-org").textContent = state.admin ? "Add organisation" : "Suggest organisation";
  $("add-event").hidden = !state.user;
  list($("found-intakes"), state.admin ? candidates(state.intakes) : [], "Found by the web search");
  list($("intakes"), recent(state.intakes, "organisation"), state.admin ? "Recently added" : "Your suggestions");
  list($("event-intakes"), recent(state.intakes, "event"), state.admin ? "Recently added events" : "Events you added");
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

/* given: what to fill in again, from an event that was not added. */
export function openEventForm(from, given = {}) {
  if (!state.user) return openLogin("add-event", from);
  clearTimeout(timer);
  showDrawer({ mode: "event-form", title: "Add an event", hash: "#add-event", from });
  fillDrawer(`
    <p class="note">Give the event's own page, and the day and the place it names. The radar reads the page and
      checks that it names that day and place, and that the event is about AI, startups, careers or coding.
      Within minutes it is on the board, with what its page says.</p>
    <form class="form" id="event-form" novalidate>
      <label class="field"><span>Event page</span>
        <input type="url" name="url" required placeholder="https://lu.ma/your-event" autocomplete="url" inputmode="url"
          value="${esc(given.url)}"></label>
      <label class="field"><span>Day <span class="hint">the day it takes place, or one of its days</span></span>
        <input type="date" name="day" required min="${today()}" value="${esc(given.day)}"></label>
      <label class="field"><span>Place <span class="hint">as the page names it: the venue or the city</span></span>
        <input type="text" name="place" required maxlength="200" placeholder="Werk1, Munich" value="${esc(given.place)}"></label>
      <p class="form-error" id="event-error" role="alert" hidden></p>
      <div class="row-actions"><button type="submit" class="btn primary">Check and add</button></div>
    </form>`);
  const form = $("event-form");
  form.elements.url.focus();
  form.addEventListener("submit", submitEvent);
}

/* What a person types, as the address the database accepts: a scheme is added if missing. */
function address(raw) {
  const text = raw.trim();
  if (!text) return "";
  return /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
}

/* Sends a form; `call` returns the new intake's id, which then opens. */
async function send(form, error, call) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const id = await call();
    upsert(await api.intake(id));
    renderIntakes();
    openIntake(id);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    button.disabled = false;
  }
}

function refuse(error, field, text) {
  error.textContent = text;
  error.hidden = false;
  field.focus();
}

async function submit(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const fields = form.elements;
  const error = $("intake-error");
  error.hidden = true;
  const url = address(fields.url.value);
  if (!url) return refuse(error, fields.url, "Enter the organisation's website.");
  await send(form, error, () =>
    api.suggest({ url, eventsUrl: address(fields.events_url.value), name: fields.name.value.trim() }));
}

async function submitEvent(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const fields = form.elements;
  const error = $("event-error");
  error.hidden = true;
  const url = address(fields.url.value);
  const day = fields.day.value;
  const place = fields.place.value.trim();
  if (!url) return refuse(error, fields.url, "Enter the event's page.");
  if (!day) return refuse(error, fields.day, "Enter the day the event takes place.");
  if (!place) return refuse(error, fields.place, "Enter the place the page names.");
  await send(form, error, () => api.addEvent({ url, day, place }));
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
    : `${found(intake) ? "The daily web search found this site." : "Someone suggested this organisation."} Nothing has been fetched yet. Approve it to check the website and collect its events.`;
  const add = review && !checked(intake) ? "Approve" : "Add anyway";
  return `<section><h3>${review ? "Your decision" : "Not added"}</h3>
    <p class="note">${note}</p>
    <div class="row-actions"><button type="button" class="btn primary" data-decide="approve">${add}</button>
      ${review ? `<button type="button" class="btn" data-decide="reject">Reject</button>` : ""}</div>
    <p class="form-error" id="decide-error" role="alert" hidden></p></section>`;
}

/* An admin's decision on an event the web search found: approving needs the day and the
   place its page names, which the check then looks for (ADR 0016). */
function eventDecision(intake) {
  if (!state.admin) return "";
  const review = intake.state === "review";
  const note = review ? "The daily web search found this page. Nothing has been fetched yet. Give the day and the place it names to check it and add the event."
    : "An admin decided not to add it. Give the day and the place it names to add it anyway.";
  return `<section><h3>${review ? "Your decision" : "Not added"}</h3>
    <p class="note">${note}</p>
    <form class="form" id="decide-form" novalidate>
      <label class="field"><span>Day <span class="hint">the day it takes place, or one of its days</span></span>
        <input type="date" name="day" required min="${today()}"></label>
      <label class="field"><span>Place <span class="hint">as the page names it: the venue or the city</span></span>
        <input type="text" name="place" required maxlength="200" placeholder="Werk1, Munich"></label>
    </form>
    <div class="row-actions"><button type="button" class="btn primary" data-decide="approve">${review ? "Check and add" : "Add anyway"}</button>
      ${review ? `<button type="button" class="btn" data-decide="reject">Reject</button>` : ""}</div>
    <p class="form-error" id="decide-error" role="alert" hidden></p></section>`;
}

function organisationResult(r) {
  return `<section><h3>Result</h3>
      <div class="row-actions"><span class="chip ${r.status === "active" ? "ok" : "warn"}">${esc(r.status)}</span>
        ${TIERS.map((t) => `<span class="tier-count"><span class="tier-badge ${t}">${t}</span>${r[t] ?? 0}</span>`).join("")}</div>
      <div class="row-actions">
        <button type="button" class="btn primary" data-open-org="${r.organiser_id}">Show the organisation</button>
        ${r.upcoming ? `<button type="button" class="btn" data-show-org="${r.organiser_id}">Show its events</button>` : ""}
      </div></section>`;
}

/* What became of an added event: on the board, waiting for the LLM, or not added and why. */
function eventResult(intake) {
  const r = intake.result;
  if (intake.state === "rejected" || intake.state === "failed") {
    const again = intake.mine ? `<div class="row-actions"><button type="button" class="btn" data-again>Change and try again</button></div>` : "";
    return `<section><h3>Not added</h3><p class="note warn">${esc(intake.error || "The check stopped.")}</p>${again}</section>`;
  }
  if (intake.state === "removed") {
    return `<section><h3>Removed</h3><p class="note">An admin took this event off the board. Its page is not added again.</p></section>`;
  }
  if (intake.state !== "done" || !r) return "";
  if (r.waiting) {
    return `<section><h3>Waiting for the LLM</h3><p class="note">The page has no structured data, so the LLM reads it in the next
      daily run. The event shows on the board once it passes the same checks.</p></section>`;
  }
  const heading = r.already ? "Already on the radar" : "On the board";
  if (!r.event_key) return `<section><h3>${heading}</h3><p class="note">The radar reads this page already; it shows no event now.</p></section>`;
  const shown = state.events.some((ev) => ev.key === r.event_key);
  const where = r.tier
    ? `<p class="tier-line"><span class="tier-badge ${esc(r.tier)}">${esc(r.tier)}</span><span>${esc(TIER_MEANING[r.tier])}</span></p>`
    : `<p class="tier-line"><span class="chip warn">${esc(DROP_LABEL[r.drop_reason] || "Not shown")}</span></p>`;
  return `<section><h3>${heading}</h3>${where}
    ${shown ? `<div class="row-actions"><a class="btn primary" href="#event-${esc(r.event_key)}">Show the event</a></div>` : ""}</section>`;
}

/* An admin's removal of an added event, confirmed in a second step. */
function removal(intake) {
  if (!state.admin || !["done", "rejected", "failed"].includes(intake.state) || intake.result?.already) return "";
  return `<section><h3>Remove from the board</h3>
    <p class="note">Takes the event and its page off the radar for good, with the stars and statuses people gave it.
      After that, only an admin can add the page again.</p>
    <div class="row-actions" id="remove-actions"><button type="button" class="btn danger" data-remove>Remove</button></div>
    <p class="form-error" id="remove-error" role="alert" hidden></p></section>`;
}

function render(intake) {
  setHeading(title(intake));
  const marks = stepMarks(intake);
  const symbol = { done: "✓", stopped: "?", failed: "!" };
  const steps = STEPS[isEvent(intake) ? "event" : "organisation"].map(([, text], i) =>
    `<li class="${marks[i]}"><span class="mark" aria-hidden="true">${symbol[marks[i]] || ""}</span><span>${esc(text)}</span></li>`).join("");
  const outcome = needsDay(intake) ? eventDecision(intake)
    : isEvent(intake) ? eventResult(intake) + removal(intake)
    : decision(intake) + (intake.result ? organisationResult(intake.result) : "") + profileCard(intake.profile);
  const log = intake.log.length
    ? `<ol class="log">${intake.log.map((entry) => `<li><time>${esc(munich(entry.at)?.time || "")}</time>
        <span class="${esc(entry.level)}">${esc(entry.text)}</span></li>`).join("")}</ol>`
    : `<p class="note">Waiting to start.</p>`;
  fillDrawer(`
    <p class="note"><a href="${esc(intake.url)}" target="_blank" rel="noopener">${esc(intake.url)}</a></p>
    <section><ol class="steps">${steps}</ol></section>
    ${outcome}
    <section><h3>What happened</h3>${log}</section>`);
  for (const button of document.querySelectorAll("[data-decide]")) {
    button.addEventListener("click", () => decide(intake, button.dataset.decide === "approve"));
  }
  document.querySelector("[data-remove]")?.addEventListener("click", () => confirmRemoval(intake));
  document.querySelector("[data-again]")?.addEventListener("click", (e) =>
    openEventForm(e.currentTarget, { url: intake.url, day: intake.starts_on, place: intake.place }));
}

export async function openIntake(id, from) {
  if (!state.user) return openLogin("intake", from);
  clearTimeout(timer);
  const known = state.intakes.find((i) => i.id === id);
  const adding = known && isEvent(known) ? "Adding an event" : "Adding an organisation";
  showDrawer({ mode: "intake", id, title: known ? title(known) : adding, hash: `#intake-${id}`, from });
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
    fillDrawer(`<p class="note warn">There is nothing with this number that you can see.</p>`);
    return;
  }
  const before = state.intakes.find((i) => i.id === id)?.state;
  upsert(intake);
  render(intake);
  renderIntakes();
  /* Its events are on the board now, or gone from it: reload, then show the link to them. */
  if ((intake.state === "done" || intake.state === "removed") && before !== intake.state) {
    board.load().then(() => isOpen("intake", id) && render(intake)).catch(board.showBanner);
  }
  if (!FINAL.has(intake.state) && intake.state !== "review") {
    clearTimeout(timer);
    timer = setTimeout(() => refresh(id), POLL_MS);
  }
}

async function decide(intake, approve) {
  try {
    const form = approve ? $("decide-form") : null;
    await api.decide(intake.id, approve, form?.elements.day.value || null, form?.elements.place.value || null);
    clearTimeout(timer);
    await refresh(intake.id);
  } catch (error) {
    const note = $("decide-error");
    note.textContent = error.message;
    note.hidden = false;
  }
}

function confirmRemoval(intake) {
  const actions = $("remove-actions");
  actions.innerHTML = `<button type="button" class="btn danger" id="remove-confirm">Remove for good</button>
    <button type="button" class="btn" id="remove-cancel">Keep it</button>`;
  $("remove-cancel").addEventListener("click", () => render(intake));
  const confirm = $("remove-confirm");
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    try {
      await api.removeEvent(intake.id);
      await refresh(intake.id);
    } catch (error) {
      const note = $("remove-error");
      note.textContent = error.message;
      note.hidden = false;
      confirm.disabled = false;
    }
  });
  confirm.focus();
}
