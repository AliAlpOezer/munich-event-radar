/* The organisations tab: each organiser with its upcoming events and the health of its sources. */

import { $, debounce, esc } from "./dom.js";
import { TIERS, isPast, isUpcoming, placeDay } from "./facts.js";
import { closeDrawer } from "./drawer.js";
import { eventRow } from "./rows.js";
import { state } from "./state.js";
import { persist } from "./storage.js";
import { relative } from "./time.js";

function orgStats(org) {
  const events = state.events.filter((ev) => ev.organiser_ids.includes(org.id));
  const upcoming = events.filter(isUpcoming);
  const byTier = Object.fromEntries(TIERS.map((t) => [t, upcoming.filter((ev) => ev.tier === t).length]));
  return { upcoming, byTier, past: events.filter(isPast).length };
}

function orgHealth(org) {
  const sources = org.sources;
  if (!sources.length) return { text: "No sources", cls: "warn", problems: 1 };
  const failing = sources.filter((s) => s.error_streak > 0);
  const empty = sources.filter((s) => s.empty_streak >= 2);
  const never = sources.filter((s) => !s.last_fetched_at);
  const waitingBrowser = never.filter((s) => s.browser);
  const lastOk = sources.map((s) => s.last_ok_at).filter(Boolean).sort().pop();
  const problems = failing.length + empty.length + never.length - waitingBrowser.length;
  let text = `${sources.length} source${sources.length === 1 ? "" : "s"} OK, fetched ${relative(lastOk)}`;
  let cls = "ok";
  if (waitingBrowser.length === sources.length) [text, cls] = ["Waiting for the weekly browser run", "warn"];
  else if (failing.length) [text, cls] = [`${failing.length} of ${sources.length} sources failing`, "bad"];
  else if (empty.length) [text, cls] = [`${empty.length} source${empty.length === 1 ? "" : "s"} went empty`, "warn"];
  else if (never.length) [text, cls] = [`${never.length} not fetched yet`, "warn"];
  return { text, cls, problems };
}

function sourceTable(sources) {
  if (!sources.length) return "";
  const rows = sources
    .map((s) => {
      const problem = s.last_error || (s.empty_streak >= 2 ? `Empty ${s.empty_streak} times in a row` : "")
        || (!s.last_fetched_at ? (s.browser ? "Waiting for the browser run" : "Not fetched yet") : "");
      return `<tr><td class="url"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></td>
        <td>${esc(s.kind)}</td><td>${esc(s.source_class)}</td>
        <td class="num">${esc(relative(s.last_ok_at))}</td><td class="num">${s.last_yield ?? "-"}</td>
        <td>${esc(problem) || "-"}</td></tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Type</th><th>Class</th>
    <th>Last OK</th><th>Yield</th><th>Problem</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function orgCard({ org, stats, health }, open) {
  const own = org.sources.filter((s) => !s.added_via.startsWith("link:"));
  const followed = org.sources.filter((s) => s.added_via.startsWith("link:"));
  const upcoming = [...stats.upcoming].sort((a, b) => (placeDay(a) || "9999").localeCompare(placeDay(b) || "9999"));
  const counts = TIERS.map((t) => `<span class="tier-count${stats.byTier[t] ? "" : " zero"}"><span class="tier-badge ${t}">${t}</span>${stats.byTier[t]}</span>`).join("");
  return `<details class="org" data-id="${org.id}"${open ? " open" : ""}>
    <summary>
      <span class="org-name"><strong>${esc(org.name)}</strong><span class="domain">${esc(org.domain)}</span></span>
      <span class="org-tags"><span class="chip">${esc(org.kind || "unknown kind")}</span>
        ${org.ai_focus ? `<span class="chip accent" title="Events count as AI-relevant without an AI keyword">AI focus</span>` : ""}
        ${org.discovered_via === "manual" ? `<span class="chip" title="Added from this page">Added by hand</span>` : ""}
        ${org.status !== "active" ? `<span class="chip warn"${org.status === "dormant" ? ` title="No upcoming event found yet; its sources are still checked"` : ""}>${esc(org.status)}</span>` : ""}</span>
      <span class="org-counts">${counts}<span class="org-health"><span class="chip ${health.cls}">${esc(health.text)}</span></span></span>
    </summary>
    <div class="org-body">
      <section><h3>Upcoming events (${upcoming.length})${stats.past ? `, plus ${stats.past} past` : ""}</h3>
        ${upcoming.length ? `<ol class="org-events">${upcoming.map((ev) => eventRow(ev, { withDate: true })).join("")}</ol>`
          : `<p class="note">No upcoming events collected from this organisation.</p>`}
        <div class="row-actions"><button type="button" class="btn" data-show-org="${org.id}">Show in timeline</button></div>
      </section>
      <section><h3>Sources</h3>${sourceTable(own)}</section>
      ${followed.length ? `<details class="more"><summary>Event pages followed from its listings (${followed.length})</summary>${sourceTable(followed)}</details>` : ""}
    </div>
  </details>`;
}

const SORTERS = {
  name: (a, b) => a.org.name.localeCompare(b.org.name),
  problems: (a, b) => b.health.problems - a.health.problems || a.org.name.localeCompare(b.org.name),
  upcoming: (a, b) =>
    b.stats.byTier.A - a.stats.byTier.A || b.stats.byTier.B - a.stats.byTier.B
    || b.stats.upcoming.length - a.stats.upcoming.length || a.org.name.localeCompare(b.org.name),
};

export function renderOrganisations() {
  const kinds = [...new Set(state.organisers.map((o) => o.kind).filter(Boolean))].sort();
  const kindSelect = $("org-kind");
  const chosen = state.orgFilters.kind;
  kindSelect.innerHTML = `<option value="">All kinds</option>` +
    kinds.map((k) => `<option value="${esc(k)}">${esc(k[0].toUpperCase() + k.slice(1))}</option>`).join("");
  kindSelect.value = kinds.includes(chosen) ? chosen : "";

  const q = state.orgFilters.q.trim().toLowerCase();
  const items = state.organisers
    .filter((o) => !state.orgFilters.kind || o.kind === state.orgFilters.kind)
    .filter((o) => !q || `${o.name} ${o.domain}`.toLowerCase().includes(q))
    .map((o) => ({ org: o, stats: orgStats(o), health: orgHealth(o) }));
  items.sort(SORTERS[state.orgFilters.sort] || SORTERS.upcoming);
  $("org-count").textContent = `${items.length} of ${state.organisers.length} organisations`;
  if (!items.length) {
    $("org-list").innerHTML = `<div class="empty">No organisation matches this search.</div>`;
    return;
  }
  const open = new Set([...document.querySelectorAll(".org[open]")].map((d) => d.dataset.id));
  $("org-list").innerHTML = items.map((item) => orgCard(item, open.has(String(item.org.id)))).join("");
}

function syncInputs() {
  $("org-q").value = state.orgFilters.q;
  $("org-sort").value = state.orgFilters.sort;
}

function update(change) {
  state.orgFilters = { ...state.orgFilters, ...change };
  persist("radar.orgFilters", state.orgFilters);
  renderOrganisations();
}

/* One organisation's card, opened, on the organisations tab. */
export function showOrganisation(id) {
  closeDrawer();
  update({ q: "", kind: "" });
  syncInputs();
  location.hash = "#organisations";
  const node = document.querySelector(`.org[data-id="${id}"]`);
  if (node) {
    node.open = true;
    node.scrollIntoView({ block: "start", behavior: "smooth" });
    node.querySelector("summary").focus({ preventScroll: true });
  }
}

export function wireOrganisations() {
  syncInputs();
  $("org-q").addEventListener("input", debounce((e) => update({ q: e.target.value }), 120));
  $("org-kind").addEventListener("change", (e) => update({ kind: e.target.value }));
  $("org-sort").addEventListener("change", (e) => update({ sort: e.target.value }));
}
