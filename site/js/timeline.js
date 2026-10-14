/* The timeline tab: upcoming events by month and day, with search and filters. */

import { $, debounce, esc } from "./dom.js";
import { TIERS, isDropped, isPast, isUpcoming, placeDay, sortTime } from "./facts.js";
import { visibleEvents } from "./filter.js";
import { eventRow } from "./rows.js";
import { DEFAULT_FILTERS, state } from "./state.js";
import { persist } from "./storage.js";
import { formatDay, today } from "./time.js";

export function renderHero() {
  const upcoming = state.events.filter(isUpcoming);
  const count = (tier) => upcoming.filter((ev) => ev.tier === tier).length;
  const orgs = state.organisers.length;
  $("hero-sub").innerHTML = `<b>${count("A")}</b> not to miss and <b>${count("B")}</b> worth a look, from ${orgs} ${orgs === 1 ? "organisation" : "organisations"}.`;
}

export function renderOrganiserFilter() {
  const select = $("org-filter");
  const named = [...state.organisers].sort((a, b) => a.name.localeCompare(b.name));
  select.innerHTML = `<option value="">All organisers</option>` +
    named.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("");
  select.value = state.orgById.has(Number(state.filters.org)) ? state.filters.org : "";
}

export function renderTimeline() {
  const upcoming = state.events.filter(isUpcoming);
  for (const button of document.querySelectorAll("#tier-toggles .toggle")) {
    const tier = button.dataset.tier;
    button.setAttribute("aria-pressed", String(state.filters.tiers.includes(tier)));
    button.querySelector(".n").textContent = upcoming.filter((ev) => ev.tier === tier).length;
  }
  $("show-past").setAttribute("aria-pressed", String(state.filters.past));
  $("show-past").querySelector(".n").textContent = state.events.filter((ev) => isPast(ev) && !isDropped(ev)).length;
  $("show-dropped").setAttribute("aria-pressed", String(state.filters.dropped));
  $("show-dropped").querySelector(".n").textContent = state.events.filter(isDropped).length;
  $("starred-field").hidden = !state.user;
  $("show-starred").setAttribute("aria-pressed", String(state.filters.starred));
  $("show-starred").querySelector(".n").textContent = upcoming.filter((ev) => ev.starred).length;

  const shown = visibleEvents(state.events, state.filters, { orgById: state.orgById, loggedIn: Boolean(state.user) })
    .map((ev) => ({ ev, day: placeDay(ev), time: sortTime(ev) }))
    .sort((a, b) => (a.day || "9999").localeCompare(b.day || "9999") || a.time.localeCompare(b.time)
      || TIERS.indexOf(a.ev.tier) - TIERS.indexOf(b.ev.tier));
  $("timeline-count").textContent = `Showing ${shown.length} of ${state.events.length} events`;
  if (!shown.length) {
    const noStars = state.user && state.filters.starred && !state.events.some((ev) => ev.starred);
    $("timeline-list").innerHTML = noStars
      ? `<div class="empty"><span>No starred events yet. Star an event to keep it here.</span>
        <button type="button" class="btn" id="reset-filters">Show every event</button></div>`
      : `<div class="empty"><span>No events match these filters.</span>
        <button type="button" class="btn" id="reset-filters">Reset filters</button></div>`;
    $("reset-filters").addEventListener("click", () => setFilters(noStars ? { starred: false } : { ...DEFAULT_FILTERS }));
    return;
  }
  const months = new Map();
  for (const item of shown) {
    const month = item.day ? item.day.slice(0, 7) : "none";
    if (!months.has(month)) months.set(month, new Map());
    const days = months.get(month);
    const day = item.day || "none";
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(item.ev);
  }
  const now = today();
  $("timeline-list").innerHTML = [...months]
    .map(([month, days]) => {
      const heading = month === "none" ? "Date not known" : formatDay(`${month}-01`, { month: "long", year: "numeric" });
      const rows = [...days]
        .map(([day, events]) => {
          const rail = day === "none"
            ? `<span class="dow">No date</span>`
            : `<span class="dow">${formatDay(day, { weekday: "short" })}</span><span class="dom">${Number(day.slice(8))}</span>`;
          return `<div class="day${day === now ? " today" : ""}"><div class="day-rail">${rail}</div>
            <ol class="day-events">${events.map((ev) => eventRow(ev)).join("")}</ol></div>`;
        })
        .join("");
      return `<section class="month"><h2>${esc(heading)}</h2><div class="days">${rows}</div></section>`;
    })
    .join("");
}

function syncInputs() {
  $("q").value = state.filters.q;
  $("org-filter").value = state.filters.org;
}

/* From the page's own controls, which already show the change. */
function update(change) {
  state.filters = { ...state.filters, ...change };
  persist("radar.filters", state.filters);
  renderTimeline();
}

/* From elsewhere: the inputs follow. */
function setFilters(filters) {
  update(filters);
  syncInputs();
}

/* Every tier of one organiser's events, on the timeline tab. */
export function showOrganiserEvents(id) {
  setFilters({ org: String(id), tiers: [...TIERS] });
  location.hash = "#timeline";
}

export function wireTimeline() {
  syncInputs();
  $("q").addEventListener("input", debounce((e) => update({ q: e.target.value }), 120));
  $("org-filter").addEventListener("change", (e) => update({ org: e.target.value }));
  for (const button of document.querySelectorAll("#tier-toggles .toggle")) {
    button.addEventListener("click", () => {
      const tiers = new Set(state.filters.tiers);
      const tier = button.dataset.tier;
      if (tiers.has(tier)) tiers.delete(tier);
      else tiers.add(tier);
      update({ tiers: TIERS.filter((t) => tiers.has(t)) });
    });
  }
  $("show-past").addEventListener("click", () => update({ past: !state.filters.past }));
  $("show-dropped").addEventListener("click", () => update({ dropped: !state.filters.dropped }));
  /* Like an organiser's events, the starred ones show in every tier. */
  $("show-starred").addEventListener("click", () =>
    update(state.filters.starred ? { starred: false } : { starred: true, tiers: [...TIERS] }));
}
