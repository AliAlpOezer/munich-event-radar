/* One event in the drawer, with the logged-in user's own status. */

import * as api from "./api.js";
import { $, esc } from "./dom.js";
import { closeDrawer, fillDrawer, isOpen, open, setHeading, showDrawer } from "./drawer.js";
import {
  DROP_LABEL, STATUS_ACTION, STATUS_LABEL, TIER_MEANING, TRANSITIONS, humanReason, organiserNames, registration, val, when,
} from "./facts.js";
import { changed, state } from "./state.js";
import { formatMoment, relative } from "./time.js";

const dl = (rows) => `<dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
const badge = (ev) => ({ cls: ev.tier || "none", text: ev.tier || "-" });

function statusSection(ev) {
  if (!state.user) {
    return `<section><h3>Your status</h3>
      <p class="note">Log in to mark this event as applied, registered or skipped.</p>
      <div class="row-actions"><button type="button" class="btn" data-login="status">Log in</button></div>
    </section>`;
  }
  const allowed = TRANSITIONS[ev.user_status] || [];
  const buttons = ["applied", "registered", "skipped", "none"]
    .map((s) => `<button type="button" class="btn" data-status="${s}"${allowed.includes(s) ? "" : " disabled"}>${STATUS_ACTION[s]}</button>`)
    .join("");
  return `<section><h3>Your status: ${esc(STATUS_LABEL[ev.user_status])}</h3>
    <div class="row-actions" id="status-buttons">${buttons}</div>
    <p class="note" id="status-note" hidden></p>
  </section>`;
}

function render(ev) {
  const reg = registration(ev);
  const facts = [
    ["When", when(ev)],
    ["Where", [val(ev, "venue"), val(ev, "address")].filter(Boolean).join(", ")],
    ["Organiser", organiserNames(ev, state.orgById)],
    ["Kind", val(ev, "kind")],
    ["Price", val(ev, "price")],
    ["Attendance", val(ev, "attendance")],
    ["Who may attend", val(ev, "prerequisites")],
  ].filter(([, v]) => v);
  const regFacts = [
    ["Mode", val(ev, "reg_mode")],
    ["Opens", val(ev, "reg_opens_at") && formatMoment(val(ev, "reg_opens_at"))],
    ["Closes", val(ev, "reg_closes_at") && formatMoment(val(ev, "reg_closes_at"))],
    ["Spots left", val(ev, "spots_left")],
    ["Facts from", ev.fields.reg_availability?.class || ev.fields.reg_url?.class],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  const links = [
    val(ev, "reg_url") ? `<a class="btn primary" href="${esc(val(ev, "reg_url"))}" target="_blank" rel="noopener">Open registration</a>` : "",
    val(ev, "event_url") ? `<a class="btn" href="${esc(val(ev, "event_url"))}" target="_blank" rel="noopener">Event page</a>` : "",
  ].join("");
  const description = val(ev, "description");
  const notes = [];
  if (ev.merge_basis === "fuzzy") notes.push("Merged from listings with similar titles on the same day. Check it is one event.");
  if (ev.stale) notes.push("Not seen in the latest run: it may have been removed or changed.");

  setHeading(val(ev, "title") || "Untitled event", badge(ev));
  fillDrawer(`
    ${ev.cover ? `<figure class="drawer-cover"><img src="${esc(ev.cover)}" alt="" referrerpolicy="no-referrer"></figure>` : ""}
    ${notes.map((n) => `<p class="note warn">${esc(n)}</p>`).join("")}
    <section>${dl(facts)}</section>
    <section><h3>Registration</h3>
      <div><span class="chip ${reg.cls}">${esc(reg.text)}</span></div>
      ${regFacts.length ? dl(regFacts) : ""}
      ${links ? `<div class="row-actions">${links}</div>` : ""}
    </section>
    ${statusSection(ev)}
    <section><h3>Why ${ev.tier ? `tier ${ev.tier}` : esc((DROP_LABEL[ev.drop_reason] || "not tiered").toLowerCase())}</h3>
      ${ev.tier ? `<p class="note">${esc(TIER_MEANING[ev.tier])}</p>` : ""}
      ${ev.reasons.length ? `<ul class="reasons">${ev.reasons.map((r) => `<li>${esc(humanReason(r))}</li>`).join("")}</ul>` : ""}
    </section>
    ${description ? `<section><h3>Description</h3>${description.length > 500
      ? `<details class="more"><summary>${esc(description.slice(0, 280))}...</summary><p class="description">${esc(description)}</p></details>`
      : `<p class="description">${esc(description)}</p>`}</section>` : ""}
    <section><h3>Sources (${ev.sources.length})</h3>
      <ul class="sources">${ev.sources.map((s) => `<li><span class="chip">${esc(s.class || "?")}</span>
        <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></li>`).join("")}</ul>
      <p class="note">First seen ${esc(relative(ev.first_seen))}, updated ${esc(relative(ev.updated_at))}.</p>
    </section>`);
  for (const button of document.querySelectorAll("#status-buttons button")) {
    button.addEventListener("click", () => changeStatus(ev, button.dataset.status));
  }
}

const find = (key) => state.events.find((e) => e.key === key);

export function openEvent(key, from) {
  const ev = find(key);
  if (!ev) return;
  showDrawer({ mode: "event", id: key, title: val(ev, "title") || "Untitled event", badge: badge(ev), hash: `#event-${key}`, from });
  render(ev);
}

/* After the board reloads: the open event shows its new facts, or closes if it is gone. */
export function refreshEvent() {
  if (!isOpen("event")) return;
  const ev = find(open.id);
  if (ev) render(ev);
  else closeDrawer();
}

async function changeStatus(ev, status) {
  try {
    ev.user_status = await api.setStatus(ev.key, status);
    changed();
  } catch (error) {
    const note = $("status-note");
    note.textContent = `Could not save the status: ${error.message}`;
    note.hidden = false;
  }
}
