/* One event as a row, shared by the timeline and the organisation cards. The star is the
   row's sibling, since one button cannot hold another. */

import { esc } from "./dom.js";
import {
  DROP_LABEL, STATUS_LABEL, TIER_MEANING, isDropped, isPast, organiserNames, registration, startOf, val, venueText,
} from "./facts.js";
import { starButton } from "./star.js";
import { state } from "./state.js";
import { formatDay, today } from "./time.js";

export function tierBadge(ev) {
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

/* The event's cover over a colour tile with the organiser's initial; the tile shows
   while the image loads or when it fails. */
function cover(ev) {
  const title = val(ev, "title") || "";
  const initial = [...(organiserNames(ev, state.orgById) || title || "?").trim()][0]?.toUpperCase() || "?";
  const img = ev.cover ? `<img src="${esc(ev.cover)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : "";
  return `<span class="cover t${tileOf(title)}" aria-hidden="true">${esc(initial)}${img}</span>`;
}

export function eventRow(ev, { withDate = false } = {}) {
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
  const meta = [organiserNames(ev, state.orgById), venueText(ev)].filter(Boolean).join(" · ");
  const classes = ["event", `tier-${ev.tier || "none"}`, isDropped(ev) || isPast(ev) ? "dropped" : ""].join(" ");
  return `<li class="event-item"><button type="button" class="${classes}" data-key="${esc(ev.key)}">
    <span class="time">${esc(time)}</span>
    ${tierBadge(ev)}
    <span class="main"><span class="title">${esc(val(ev, "title") || "Untitled event")}</span>
      ${meta ? `<span class="meta">${esc(meta)}</span>` : ""}</span>
    <span class="tags">${tags.join("")}</span>
    ${cover(ev)}
  </button>${starButton(ev)}</li>`;
}
