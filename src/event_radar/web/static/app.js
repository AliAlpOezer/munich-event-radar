"use strict";

const TZ = "Europe/Berlin";
const POLL_MS = 60000;
const TIERS = ["A", "B", "C"];
const TRANSITIONS = {
  none: ["applied", "registered", "skipped"],
  applied: ["registered", "skipped", "none"],
  registered: ["none"],
  skipped: ["none"],
};
const STATUS_LABEL = { applied: "Applied", registered: "Registered", skipped: "Skipped", none: "No status" };
const STATUS_ACTION = { applied: "Applied", registered: "Registered", skipped: "Skip", none: "Clear" };
const TIER_MEANING = {
  A: "Must not miss: free, in Munich, with people from AI or hiring companies to meet.",
  B: "Free and AI-relevant, with some networking.",
  C: "In scope but not pushed: AI relevance unknown, paid, or not AI-focused.",
};
const DROP_LABEL = { past: "Past", online_only: "Online only", outside_munich: "Outside Munich" };
const INTAKE_POLL_MS = 2000;
const INTAKE_STEPS = [
  ["checking", "Check that the website works"],
  ["profiling", "Find out who they are"],
  ["finding", "Find their event pages"],
  ["harvesting", "Collect events, tickets and deadlines"],
  ["done", "On the radar"],
];
const INTAKE_LABEL = {
  queued: "Waiting", checking: "Checking", profiling: "Checking who they are", review: "Needs your decision",
  finding: "Finding event pages", harvesting: "Collecting events", done: "Added", rejected: "Rejected", failed: "Failed",
};
const INTAKE_FINAL = new Set(["done", "rejected", "failed"]);

const state = {
  events: [],
  organisers: [],
  orgById: new Map(),
  intakes: [],
  intakeEnabled: false,
  drawerMode: null,
  openIntakeId: null,
  intakeTimer: null,
  runMark: null,
  openKey: null,
  opener: null,
  tabHash: "#timeline",
  filters: restore("radar.filters", { q: "", org: "", tiers: TIERS, past: false, dropped: false }),
  orgFilters: restore("radar.orgFilters", { q: "", kind: "", sort: "upcoming" }),
};

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const val = (ev, name) => ev.fields?.[name]?.value ?? null;

function restore(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : { ...fallback };
  } catch {
    return { ...fallback };
  }
}
function persist(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: filters reset on reload */
  }
}

/* Dates: every moment is shown in Munich time. Date-only values stay all-day. */
const partsFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
function munich(value) {
  if (!value) return null;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return { day: text, time: null };
  if (!/([zZ]|[+-]\d{2}:?\d{2})$/.test(text)) {
    const m = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return m ? { day: m[1], time: m[2] } : null;
  }
  const moment = new Date(text);
  if (Number.isNaN(moment.getTime())) return null;
  const p = Object.fromEntries(partsFormat.formatToParts(moment).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}
const today = () => munich(new Date().toISOString()).day;
const noon = (day) => new Date(`${day}T12:00:00Z`);
const formatDay = (day, options) => new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...options }).format(noon(day));
const daysBetween = (a, b) => Math.round((noon(b) - noon(a)) / 86400000);
function formatMoment(value) {
  const m = munich(value);
  if (!m) return value ? String(value) : "";
  const options = { weekday: "short", day: "numeric", month: "short" };
  if (m.day.slice(0, 4) !== today().slice(0, 4)) options.year = "numeric";
  const day = formatDay(m.day, options);
  return m.time ? `${day}, ${m.time}` : day;
}
function relative(iso) {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 36 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}

/* Event facts */
const startOf = (ev) => munich(val(ev, "starts_at"));
const lastDayOf = (ev) => (munich(val(ev, "ends_at")) || startOf(ev))?.day ?? null;
const isDropped = (ev) => Boolean(ev.drop_reason && ev.drop_reason !== "past");
function isPast(ev) {
  if (ev.drop_reason === "past") return true;
  const last = lastDayOf(ev);
  return last ? last < today() : false;
}
const isUpcoming = (ev) => !isPast(ev) && !isDropped(ev);
function organiserNames(ev) {
  const names = ev.organiser_ids.map((id) => state.orgById.get(id)?.name).filter(Boolean);
  return names.length ? names.join(", ") : val(ev, "organiser") || "";
}
function venueText(ev) {
  const venue = val(ev, "venue");
  if (venue && /^https?:/.test(venue)) return "Venue on the event page";
  return venue || val(ev, "address") || "";
}
function registration(ev) {
  const availability = val(ev, "reg_availability");
  const mode = val(ev, "reg_mode");
  const closes = val(ev, "reg_closes_at");
  const opens = val(ev, "reg_opens_at");
  const spots = val(ev, "spots_left");
  const closeDay = munich(closes)?.day;
  if (availability === "sold_out") return { text: "Sold out", cls: "bad" };
  if (availability === "closed" || (closeDay && closeDay < today())) return { text: "Registration closed", cls: "bad" };
  if (availability === "waitlist") return { text: "Waitlist", cls: "warn" };
  if (availability === "not_open") return { text: opens ? `Opens ${formatMoment(opens)}` : "Not open yet", cls: "warn" };
  if (availability === "open" || closeDay) {
    let text = closeDay ? `Open until ${formatMoment(closes)}` : "Open";
    if (spots !== null && spots !== undefined) text += `, ${spots} left`;
    return { text, cls: closeDay && daysBetween(today(), closeDay) <= 7 ? "warn" : "ok" };
  }
  if (availability === "unchecked") return { text: "Aggregator only, unchecked", cls: "warn" };
  if (mode === "approval") return { text: "Approval needed", cls: "" };
  if (mode === "application") return { text: "Application", cls: "" };
  if (val(ev, "reg_url")) return { text: "Registration link", cls: "" };
  return { text: "No registration info", cls: "" };
}
function searchText(ev) {
  return [val(ev, "title"), organiserNames(ev), val(ev, "venue"), val(ev, "description")].join(" ").toLowerCase();
}

/* Data */
async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}
async function loadAll() {
  const [events, organisers, status, intakes] = await Promise.all([
    getJSON("/api/events"),
    getJSON("/api/organisers"),
    getJSON("/api/status"),
    getJSON("/api/intakes"),
  ]);
  state.intakes = intakes.intakes;
  state.intakeEnabled = intakes.enabled;
  state.events = events.events;
  state.organisers = organisers.organisers;
  state.orgById = new Map(state.organisers.map((o) => [o.id, o]));
  applyStatus(status.runs);
  renderAll();
}
function runMark(runs) {
  const run = runs[0];
  return run ? `${run.id}:${run.finished_at || ""}` : "none";
}
function applyStatus(runs) {
  state.runMark = runMark(runs);
  const box = $("run-status");
  const text = box.querySelector(".run-text");
  const run = runs[0];
  if (!run) {
    box.dataset.state = "";
    text.textContent = "No runs yet";
    return;
  }
  const s = run.summary || {};
  if (!run.finished_at) {
    box.dataset.state = "";
    text.textContent = `Run in progress since ${munich(run.started_at).time}`;
  } else {
    box.dataset.state = run.outcome === "ok" ? "ok" : "failed";
    const surfaced = (s.tier_A || 0) + (s.tier_B || 0);
    text.textContent =
      run.outcome === "ok"
        ? `Last run ${relative(run.finished_at)}, ${surfaced} A or B`
        : `Last run failed ${relative(run.finished_at)}`;
  }
  box.title = [
    `Started ${formatMoment(run.started_at)}`,
    `Fetched ${s.fetched ?? 0} pages, ${s.fetch_errors ?? 0} errors`,
    `A ${s.tier_A ?? 0}, B ${s.tier_B ?? 0}, C ${s.tier_C ?? 0}`,
    s.error ? `Error: ${s.error}` : "",
  ].filter(Boolean).join("\n");
}
async function poll() {
  try {
    const status = await getJSON("/api/status");
    if (runMark(status.runs) !== state.runMark) await loadAll();
    else applyStatus(status.runs);
    showBanner(null);
  } catch (error) {
    showBanner(error);
  }
}
function showBanner(error) {
  const banner = $("banner");
  if (!error) {
    banner.hidden = true;
    return;
  }
  banner.innerHTML = `<span>The radar did not answer (${esc(error.message)}). It retries every minute.</span>
    <button type="button" class="btn" id="retry">Retry now</button>`;
  banner.hidden = false;
  $("retry").addEventListener("click", () => loadAll().then(() => showBanner(null), showBanner));
}

/* Rendering helpers */
function tierBadge(ev) {
  const tier = ev.tier || "none";
  const title = ev.tier ? `Tier ${ev.tier}. ${TIER_MEANING[ev.tier]}` : DROP_LABEL[ev.drop_reason] || "Not tiered";
  return `<span class="tier-badge ${tier}" title="${esc(title)}">${ev.tier || "-"}</span>`;
}
const TILES = 5;
function tileOf(text) {
  let hash = 0;
  for (const c of text) hash = (hash * 31 + c.codePointAt(0)) >>> 0;
  return hash % TILES;
}
function cover(ev) {
  const title = val(ev, "title") || "";
  const initial = [...(organiserNames(ev) || title || "?").trim()][0]?.toUpperCase() || "?";
  const img = ev.cover ? `<img src="${esc(ev.cover)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : "";
  return `<span class="cover t${tileOf(title)}" aria-hidden="true">${esc(initial)}${img}</span>`;
}
function eventRow(ev, { withDate = false } = {}) {
  const start = startOf(ev);
  const ongoing = start && start.day < today() && !isPast(ev);
  let time = ongoing ? "ongoing" : start?.time || "all day";
  if (withDate) time = start ? formatDay(start.day, { day: "numeric", month: "short" }) + (start.time ? ` ${start.time}` : "") : "no date";
  const reg = registration(ev);
  const tags = [];
  if (isDropped(ev) || isPast(ev)) tags.push(`<span class="chip">${esc(DROP_LABEL[ev.drop_reason] || "Past")}</span>`);
  else if (ev.tier !== "C" || reg.text !== "No registration info") tags.push(`<span class="chip ${reg.cls}">${esc(reg.text)}</span>`);
  if (ev.user_status !== "none") tags.push(`<span class="chip accent">${esc(STATUS_LABEL[ev.user_status])}</span>`);
  if (ev.stale) tags.push(`<span class="chip warn" title="Not seen in the latest run">Not seen lately</span>`);
  const meta = [organiserNames(ev), venueText(ev)].filter(Boolean).join(" · ");
  const classes = ["event", `tier-${ev.tier || "none"}`, isDropped(ev) || isPast(ev) ? "dropped" : ""].join(" ");
  return `<li><button type="button" class="${classes}" data-key="${esc(ev.key)}">
    <span class="time">${esc(time)}</span>
    ${tierBadge(ev)}
    <span class="main"><span class="title">${esc(val(ev, "title") || "Untitled event")}</span>
      ${meta ? `<span class="meta">${esc(meta)}</span>` : ""}</span>
    <span class="tags">${tags.join("")}</span>
    ${cover(ev)}
  </button></li>`;
}
const skeleton = (rows) => `<div class="skeleton">${"<div></div>".repeat(rows)}</div>`;

/* Timeline */
function visibleEvents() {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  return state.events.filter((ev) => {
    if (isDropped(ev)) {
      if (!f.dropped) return false;
    } else if (isPast(ev)) {
      if (!f.past) return false;
    } else if (!f.tiers.includes(ev.tier)) {
      return false;
    }
    if (f.org && !ev.organiser_ids.includes(Number(f.org))) return false;
    return !q || searchText(ev).includes(q);
  });
}
function placeDay(ev) {
  const start = startOf(ev);
  if (!start) return null;
  return start.day < today() && !isPast(ev) ? today() : start.day;
}
function sortTime(ev) {
  const start = startOf(ev);
  if (!start || !start.time) return "";
  return start.day < today() && !isPast(ev) ? "" : start.time;
}
function renderHero() {
  const upcoming = state.events.filter(isUpcoming);
  const count = (tier) => upcoming.filter((ev) => ev.tier === tier).length;
  const orgs = state.organisers.length;
  $("hero-sub").innerHTML = `<b>${count("A")}</b> not to miss and <b>${count("B")}</b> worth a look, from ${orgs} ${orgs === 1 ? "organisation" : "organisations"}.`;
}
function renderTimeline() {
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

  const shown = visibleEvents()
    .map((ev) => ({ ev, day: placeDay(ev), time: sortTime(ev) }))
    .sort((a, b) => (a.day || "9999").localeCompare(b.day || "9999") || a.time.localeCompare(b.time)
      || TIERS.indexOf(a.ev.tier) - TIERS.indexOf(b.ev.tier));
  $("timeline-count").textContent = `Showing ${shown.length} of ${state.events.length} events`;
  if (!shown.length) {
    $("timeline-list").innerHTML = `<div class="empty"><span>No events match these filters.</span>
      <button type="button" class="btn" id="reset-filters">Reset filters</button></div>`;
    $("reset-filters").addEventListener("click", () => {
      state.filters = { q: "", org: "", tiers: [...TIERS], past: false, dropped: false };
      syncFilterInputs();
      updateFilters();
    });
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

/* Organisations */
function orgStats(org) {
  const events = state.events.filter((ev) => ev.organiser_ids.includes(org.id));
  const upcoming = events.filter(isUpcoming);
  const byTier = Object.fromEntries(TIERS.map((t) => [t, upcoming.filter((ev) => ev.tier === t).length]));
  return { events, upcoming, byTier, past: events.filter(isPast).length };
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
  if (waitingBrowser.length === sources.length) [text, cls] = ["Needs the browser unit, not built yet", "warn"];
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
        || (!s.last_fetched_at ? (s.browser ? "Waiting for the browser unit" : "Not fetched yet") : "");
      return `<tr><td class="url"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a></td>
        <td>${esc(s.kind)}</td><td>${esc(s.source_class)}</td>
        <td class="num">${esc(relative(s.last_ok_at))}</td><td class="num">${s.last_yield ?? "-"}</td>
        <td>${esc(problem) || "-"}</td></tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Type</th><th>Class</th>
    <th>Last OK</th><th>Yield</th><th>Problem</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function renderOrganisations() {
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
  const sorters = {
    name: (a, b) => a.org.name.localeCompare(b.org.name),
    problems: (a, b) => b.health.problems - a.health.problems || a.org.name.localeCompare(b.org.name),
    upcoming: (a, b) =>
      b.stats.byTier.A - a.stats.byTier.A || b.stats.byTier.B - a.stats.byTier.B
      || b.stats.upcoming.length - a.stats.upcoming.length || a.org.name.localeCompare(b.org.name),
  };
  items.sort(sorters[state.orgFilters.sort] || sorters.upcoming);
  $("org-count").textContent = `${items.length} of ${state.organisers.length} organisations`;
  if (!items.length) {
    $("org-list").innerHTML = `<div class="empty">No organisation matches this search.</div>`;
    return;
  }
  const open = new Set([...document.querySelectorAll(".org[open]")].map((d) => d.dataset.id));
  $("org-list").innerHTML = items
    .map(({ org, stats, health }) => {
      const own = org.sources.filter((s) => !s.added_via.startsWith("link:"));
      const followed = org.sources.filter((s) => s.added_via.startsWith("link:"));
      const upcoming = [...stats.upcoming].sort((a, b) => (placeDay(a) || "9999").localeCompare(placeDay(b) || "9999"));
      const counts = TIERS.map((t) => `<span class="tier-count${stats.byTier[t] ? "" : " zero"}"><span class="tier-badge ${t}">${t}</span>${stats.byTier[t]}</span>`).join("");
      return `<details class="org" data-id="${org.id}"${open.has(String(org.id)) ? " open" : ""}>
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
    })
    .join("");
}

/* Detail drawer */
function humanReason(reason) {
  const [name, value] = reason.split("=");
  if (value === undefined) return reason;
  const map = {
    price: { unknown: "Price not stated", free: "Free", paid: "Paid", free_student_route: "Free route for students" },
    ai: { core: "AI keyword or stated AI focus", some: "Some AI content", unknown: "No AI keyword found", none: "Not AI-related" },
  };
  if (name === "kind") return value === "None" ? "Kind not recognised" : `Kind: ${value.replace(/_/g, " ")}`;
  return map[name]?.[value] || reason;
}
function when(ev) {
  const start = munich(val(ev, "starts_at"));
  const end = munich(val(ev, "ends_at"));
  const from = formatMoment(val(ev, "starts_at"));
  if (!end || (start && end.day === start.day && end.time === start.time)) return from;
  if (start && end.day === start.day) return end.time ? `${from} to ${end.time}` : from;
  return `${from} to ${formatMoment(val(ev, "ends_at"))}`;
}
function renderDrawer(ev) {
  const reg = registration(ev);
  const facts = [
    ["When", when(ev)],
    ["Where", [val(ev, "venue"), val(ev, "address")].filter(Boolean).join(", ")],
    ["Organiser", organiserNames(ev)],
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
  const allowed = TRANSITIONS[ev.user_status] || [];
  const statusButtons = ["applied", "registered", "skipped", "none"]
    .map((s) => `<button type="button" class="btn" data-status="${s}"${allowed.includes(s) ? "" : " disabled"}>${STATUS_ACTION[s]}</button>`)
    .join("");
  const description = val(ev, "description");
  const notes = [];
  if (ev.merge_basis === "fuzzy") notes.push("Merged from listings with similar titles on the same day. Check it is one event.");
  if (ev.stale) notes.push("Not seen in the latest run: it may have been removed or changed.");
  const dl = (rows) => `<dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;

  $("drawer-tier").className = `tier-badge ${ev.tier || "none"}`;
  $("drawer-tier").textContent = ev.tier || "-";
  $("drawer-title").textContent = val(ev, "title") || "Untitled event";
  $("drawer-body").innerHTML = `
    ${ev.cover ? `<figure class="drawer-cover"><img src="${esc(ev.cover)}" alt="" referrerpolicy="no-referrer"></figure>` : ""}
    ${notes.map((n) => `<p class="note warn">${esc(n)}</p>`).join("")}
    <section>${dl(facts)}</section>
    <section><h3>Registration</h3>
      <div><span class="chip ${reg.cls}">${esc(reg.text)}</span></div>
      ${regFacts.length ? dl(regFacts) : ""}
      ${links ? `<div class="row-actions">${links}</div>` : ""}
    </section>
    <section><h3>Your status: ${esc(STATUS_LABEL[ev.user_status])}</h3>
      <div class="row-actions" id="status-buttons">${statusButtons}</div>
      <p class="note" id="status-note" hidden></p>
    </section>
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
    </section>`;
  for (const button of document.querySelectorAll("#status-buttons button")) {
    button.addEventListener("click", () => changeStatus(ev, button.dataset.status));
  }
}
function openDrawer(key, opener) {
  const ev = state.events.find((e) => e.key === key);
  if (!ev) return;
  stopIntakePoll();
  state.drawerMode = "event";
  state.openIntakeId = null;
  state.openKey = key;
  state.opener = opener || null;
  $("drawer-tier").hidden = false;
  renderDrawer(ev);
  history.replaceState(null, "", `#event-${key}`);
  $("drawer").hidden = false;
  $("scrim").hidden = false;
  $("drawer-close").focus();
}
function closeDrawer() {
  stopIntakePoll();
  state.drawerMode = null;
  state.openIntakeId = null;
  state.openKey = null;
  history.replaceState(null, "", state.tabHash);
  $("drawer").hidden = true;
  $("scrim").hidden = true;
  if (state.opener && document.body.contains(state.opener)) state.opener.focus();
}
async function changeStatus(ev, status) {
  const note = $("status-note");
  try {
    const result = await getJSON(`/api/events/${encodeURIComponent(ev.key)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    ev.user_status = result.user_status;
    renderAll();
  } catch (error) {
    note.textContent = `Could not save the status: ${error.message}`;
    note.hidden = false;
  }
}

/* Adding an organisation (ADR 0003) */
function intakeTitle(intake) {
  if (intake.name) return intake.name;
  if (intake.profile?.name) return intake.profile.name;
  try {
    return new URL(intake.url).hostname.replace(/^www\./, "");
  } catch {
    return intake.url;
  }
}
function intakeChip(intake) {
  const cls = { done: "ok", review: "warn", rejected: "bad", failed: "bad" }[intake.state] || "accent";
  return `<span class="chip ${cls}">${esc(INTAKE_LABEL[intake.state] || intake.state)}</span>`;
}
function renderIntakes() {
  $("add-org").hidden = !state.intakeEnabled;
  const recent = state.intakes
    .filter((i) => !INTAKE_FINAL.has(i.state) || Date.now() - new Date(i.updated_at).getTime() < 3 * 86400000)
    .slice(0, 5);
  const box = $("intakes");
  box.hidden = !recent.length;
  if (!recent.length) return;
  box.innerHTML = `<h2>Recently added</h2>` + recent.map((i) => {
    const last = i.log[i.log.length - 1];
    return `<button type="button" class="intake-row" data-intake="${i.id}">
      <span class="main"><span class="title">${esc(intakeTitle(i))}</span>
        <span class="meta">${esc(last ? last.text : i.url)}</span></span>
      ${intakeChip(i)}
    </button>`;
  }).join("");
}
function stopIntakePoll() {
  clearTimeout(state.intakeTimer);
  state.intakeTimer = null;
}
function showDrawer(title, opener) {
  $("drawer-tier").hidden = true;
  $("drawer-title").textContent = title;
  if (opener) state.opener = opener;
  $("drawer").hidden = false;
  $("scrim").hidden = false;
}
function openAddForm(opener) {
  stopIntakePoll();
  state.drawerMode = "form";
  state.openKey = null;
  state.openIntakeId = null;
  history.replaceState(null, "", "#add-organisation");
  showDrawer("Add an organisation", opener);
  $("drawer-body").innerHTML = `
    <p class="note">Give the organisation's website. The radar checks that it works, asks the LLM who they are,
      finds their event pages, and collects their events with tickets and registration deadlines.</p>
    <form class="form" id="intake-form" novalidate>
      <label class="field"><span>Website</span>
        <input type="url" name="url" required placeholder="https://www.cdtm.com" autocomplete="url" inputmode="url"></label>
      <label class="field"><span>Events page <span class="hint">optional, if you know it</span></span>
        <input type="url" name="events_url" placeholder="https://www.example.org/events" inputmode="url"></label>
      <label class="field"><span>Name <span class="hint">optional</span></span>
        <input type="text" name="name" autocomplete="organization"></label>
      <p class="form-error" id="intake-error" role="alert" hidden></p>
      <div class="row-actions"><button type="submit" class="btn primary">Check and add</button></div>
    </form>`;
  const form = $("intake-form");
  form.elements.url.focus();
  form.addEventListener("submit", submitIntake);
}
async function submitIntake(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const fields = form.elements;
  const error = $("intake-error");
  const button = form.querySelector("button[type=submit]");
  error.hidden = true;
  if (!fields.url.value.trim()) {
    error.textContent = "Enter the organisation's website.";
    error.hidden = false;
    fields.url.focus();
    return;
  }
  button.disabled = true;
  try {
    const intake = await getJSON("/api/intakes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: fields.url.value,
        events_url: fields.events_url.value || null,
        name: fields.name.value || null,
      }),
    });
    upsertIntake(intake);
    renderIntakes();
    openIntake(intake.id);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
    button.disabled = false;
  }
}
function upsertIntake(intake) {
  const at = state.intakes.findIndex((i) => i.id === intake.id);
  if (at >= 0) state.intakes[at] = intake;
  else state.intakes.unshift(intake);
}
function stepMarks(intake) {
  const order = INTAKE_STEPS.map(([key]) => key);
  if (intake.state === "done") return order.map(() => "done");
  let at = Math.max(order.indexOf(intake.state), 0);
  let mark = "active";
  if (intake.state === "review" || intake.state === "rejected") [at, mark] = [1, "stopped"];
  if (intake.state === "failed") {
    const reached = intake.log.map((entry) => order.indexOf(entry.step)).filter((i) => i >= 0);
    [at, mark] = [reached.length ? Math.max(...reached) : 0, "failed"];
  }
  return order.map((_, i) => (i < at ? "done" : i === at ? mark : ""));
}
function profileCard(profile) {
  if (!profile || !profile.summary) return "";
  const verdict = (value, label) => {
    const cls = value === "yes" ? "ok" : value === "no" ? "bad" : "warn";
    const text = value === "yes" ? label : value === "no" ? `Not: ${label.toLowerCase()}` : `${label}: unclear`;
    return `<span class="chip ${cls}">${esc(text)}</span>`;
  };
  return `<section class="profile-card"><h3>What the LLM found</h3>
    <p class="description">${esc(profile.summary)}</p>
    <div class="chips">${verdict(profile.legitimate, "Genuine")}${verdict(profile.runs_events, "Runs events")}
      ${verdict(profile.based_in_munich, "In Munich")}<span class="chip">${esc(profile.kind || "kind unknown")}</span>
      ${profile.ai_focus ? `<span class="chip accent">AI focus</span>` : ""}</div>
    ${profile.reasons?.length ? `<ul class="reasons">${profile.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
  </section>`;
}
function renderIntake(intake) {
  $("drawer-title").textContent = intakeTitle(intake);
  const marks = stepMarks(intake);
  const symbol = { done: "✓", stopped: "?", failed: "!" };
  const steps = INTAKE_STEPS.map(([, label], i) =>
    `<li class="${marks[i]}"><span class="mark" aria-hidden="true">${symbol[marks[i]] || ""}</span><span>${esc(label)}</span></li>`).join("");
  const review = intake.state === "review";
  const decide = review || intake.state === "rejected"
    ? `<section><h3>${review ? "Your decision" : "Not added"}</h3>
        <p class="note">${review
          ? "The check could not confirm this organisation. Add it if you know it is real and runs events in Munich."
          : "If the check got it wrong, you can still add it."}</p>
        <div class="row-actions"><button type="button" class="btn primary" data-decide="approve">Add anyway</button>
          ${review ? `<button type="button" class="btn" data-decide="reject">Reject</button>` : ""}</div>
        <p class="form-error" id="decide-error" role="alert" hidden></p></section>`
    : "";
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
  $("drawer-body").innerHTML = `
    <p class="note"><a href="${esc(intake.url)}" target="_blank" rel="noopener">${esc(intake.url)}</a></p>
    <section><ol class="steps">${steps}</ol></section>
    ${decide}${result}${profileCard(intake.profile)}
    <section><h3>What happened</h3>${log}</section>`;
  for (const button of document.querySelectorAll("[data-decide]")) {
    button.addEventListener("click", () => decideIntake(intake, button.dataset.decide === "approve"));
  }
}
async function openIntake(id, opener) {
  stopIntakePoll();
  state.drawerMode = "intake";
  state.openKey = null;
  state.openIntakeId = id;
  history.replaceState(null, "", `#intake-${id}`);
  const known = state.intakes.find((i) => i.id === id);
  showDrawer(known ? intakeTitle(known) : "Adding an organisation", opener);
  if (known) renderIntake(known);
  else $("drawer-body").innerHTML = skeleton(4);
  $("drawer-close").focus();
  await refreshIntake(id);
}
async function refreshIntake(id) {
  if (state.openIntakeId !== id) return;
  let intake;
  try {
    intake = await getJSON(`/api/intakes/${id}`);
  } catch (error) {
    $("drawer-body").innerHTML = `<p class="note warn">${esc(error.message)}</p>`;
    return;
  }
  if (state.openIntakeId !== id) return;
  const before = state.intakes.find((i) => i.id === id)?.state;
  upsertIntake(intake);
  renderIntake(intake);
  renderIntakes();
  if (intake.state === "done" && before !== "done") loadAll().catch(showBanner);
  if (!INTAKE_FINAL.has(intake.state) && intake.state !== "review") {
    state.intakeTimer = setTimeout(() => refreshIntake(id), INTAKE_POLL_MS);
  }
}
async function decideIntake(intake, approve) {
  try {
    upsertIntake(await getJSON(`/api/intakes/${intake.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve }),
    }));
    stopIntakePoll();
    await refreshIntake(intake.id);
  } catch (error) {
    const note = $("decide-error");
    note.textContent = error.message;
    note.hidden = false;
  }
}
function showOrganisation(id) {
  closeDrawer();
  state.orgFilters = { ...state.orgFilters, q: "", kind: "" };
  syncFilterInputs();
  location.hash = "#organisations";
  updateOrgFilters();
  const node = document.querySelector(`.org[data-id="${id}"]`);
  if (node) {
    node.open = true;
    node.scrollIntoView({ block: "start", behavior: "smooth" });
    node.querySelector("summary").focus({ preventScroll: true });
  }
}

/* Views, filters and wiring */
function renderAll() {
  const select = $("org-filter");
  const named = [...state.organisers].sort((a, b) => a.name.localeCompare(b.name));
  select.innerHTML = `<option value="">All organisers</option>` +
    named.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("");
  select.value = state.orgById.has(Number(state.filters.org)) ? state.filters.org : "";
  renderHero();
  renderTimeline();
  renderOrganisations();
  renderIntakes();
  if (state.drawerMode === "event" && state.openKey) {
    const ev = state.events.find((e) => e.key === state.openKey);
    if (ev) renderDrawer(ev);
    else closeDrawer();
  }
}
function syncFilterInputs() {
  $("q").value = state.filters.q;
  $("org-filter").value = state.filters.org;
  $("org-q").value = state.orgFilters.q;
  $("org-sort").value = state.orgFilters.sort;
}
function updateFilters() {
  persist("radar.filters", state.filters);
  renderTimeline();
}
function updateOrgFilters() {
  persist("radar.orgFilters", state.orgFilters);
  renderOrganisations();
}
function showView() {
  const linked = location.hash.match(/^#event-([\w-]+)$/);
  if (linked) {
    if (state.events.length) openDrawer(linked[1]);
    return;
  }
  const intake = location.hash.match(/^#intake-(\d+)$/);
  if (intake) {
    if (state.openIntakeId !== Number(intake[1])) openIntake(Number(intake[1]));
    return;
  }
  if (location.hash === "#add-organisation") {
    if (state.drawerMode !== "form") openAddForm();
    return;
  }
  const view = location.hash === "#organisations" ? "organisations" : "timeline";
  state.tabHash = `#${view}`;
  for (const name of ["timeline", "organisations"]) {
    $(`view-${name}`).hidden = name !== view;
    $(`tab-${name}`).setAttribute("aria-selected", String(name === view));
  }
}
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function wire() {
  syncFilterInputs();
  $("q").addEventListener("input", debounce((e) => { state.filters.q = e.target.value; updateFilters(); }, 120));
  $("org-filter").addEventListener("change", (e) => { state.filters.org = e.target.value; updateFilters(); });
  for (const button of document.querySelectorAll("#tier-toggles .toggle")) {
    button.addEventListener("click", () => {
      const tier = button.dataset.tier;
      const tiers = new Set(state.filters.tiers);
      tiers.has(tier) ? tiers.delete(tier) : tiers.add(tier);
      state.filters.tiers = TIERS.filter((t) => tiers.has(t));
      updateFilters();
    });
  }
  $("show-past").addEventListener("click", () => { state.filters.past = !state.filters.past; updateFilters(); });
  $("show-dropped").addEventListener("click", () => { state.filters.dropped = !state.filters.dropped; updateFilters(); });
  $("org-q").addEventListener("input", debounce((e) => { state.orgFilters.q = e.target.value; updateOrgFilters(); }, 120));
  $("org-kind").addEventListener("change", (e) => { state.orgFilters.kind = e.target.value; updateOrgFilters(); });
  $("org-sort").addEventListener("change", (e) => { state.orgFilters.sort = e.target.value; updateOrgFilters(); });
  document.addEventListener("click", (e) => {
    const row = e.target.closest(".event[data-key]");
    if (row) return openDrawer(row.dataset.key, row);
    const intakeRow = e.target.closest("[data-intake]");
    if (intakeRow) return openIntake(Number(intakeRow.dataset.intake), intakeRow);
    const org = e.target.closest("[data-open-org]");
    if (org) return showOrganisation(Number(org.dataset.openOrg));
    const show = e.target.closest("[data-show-org]");
    if (show) {
      state.filters = { ...state.filters, org: show.dataset.showOrg, tiers: [...TIERS] };
      syncFilterInputs();
      updateFilters();
      location.hash = "#timeline";
    }
  });
  $("add-org").addEventListener("click", (e) => openAddForm(e.currentTarget));
  const themeToggle = $("theme-toggle");
  const labelTheme = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    themeToggle.setAttribute("aria-label", `Switch to ${next} theme`);
  };
  labelTheme();
  themeToggle.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    if (dark) document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
    labelTheme();
    try {
      localStorage.setItem("radar.theme", dark ? "dark" : "light");
    } catch {
      /* storage blocked: the theme resets to light on reload */
    }
  });
  /* A cover that fails to load leaves the colour tile behind it, or no cover in the drawer. */
  document.addEventListener("error", (e) => {
    if (e.target.matches?.(".cover img")) e.target.remove();
    else if (e.target.matches?.(".drawer-cover img")) e.target.parentElement.remove();
  }, true);
  $("drawer-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("drawer").hidden) closeDrawer();
  });
  window.addEventListener("hashchange", showView);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") poll();
  });
  showView();
}

function trackBarHeight() {
  const bar = document.querySelector(".bar");
  const set = () => document.documentElement.style.setProperty("--bar-h", `${bar.offsetHeight}px`);
  new ResizeObserver(set).observe(bar);
  set();
}

trackBarHeight();
wire();
$("timeline-list").innerHTML = skeleton(8);
$("org-list").innerHTML = skeleton(6);
loadAll().then(showView, showBanner);
setInterval(poll, POLL_MS);
