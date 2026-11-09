/* What an event row from api.events means: its dates, registration window and labels. */

import { daysBetween, formatMoment, munich, today } from "./time.js";

export const TIERS = ["A", "B", "C"];
/* The database enforces these (ADR 0005 D6); the page only uses them to choose which
   buttons to offer. */
export const TRANSITIONS = {
  none: ["applied", "registered", "skipped"],
  applied: ["registered", "skipped", "none"],
  registered: ["none"],
  skipped: ["none"],
};
export const STATUS_LABEL = { applied: "Applied", registered: "Registered", skipped: "Skipped", none: "No status" };
export const STATUS_ACTION = { applied: "Applied", registered: "Registered", skipped: "Skip", none: "Clear" };
/* ADR 0021: events rank by the people worth meeting there. */
export const TIER_MEANING = {
  A: "Must not miss: free, in Munich, on AI, startups or careers, and made for meeting people or opening a door.",
  B: "Worth going: free and on AI, startups or careers, or run by a community the radar follows.",
  C: "In scope, not pushed: paid, AI or tech only on the side, or no topic named.",
};
export const DROP_LABEL = {
  past: "Past",
  online_only: "Online only",
  outside_munich: "Outside Munich",
  not_event: "Not an event",
};

export const val = (ev, name) => ev.fields?.[name]?.value ?? null;

export const startOf = (ev) => munich(val(ev, "starts_at"));
/* As the judge's last day (ADR 0022 D1): the end, else the start, else the registration's
   close; a programme's close ends it, however far ahead its class starts. */
function lastDayOf(ev) {
  const closes = munich(val(ev, "reg_closes_at"));
  if (closes && val(ev, "kind") === "programme") return closes.day;
  return (munich(val(ev, "ends_at")) || startOf(ev) || closes)?.day ?? null;
}
export function isPast(ev) {
  if (ev.drop_reason === "past") return true;
  const last = lastDayOf(ev);
  return last ? last < today() : false;
}
/* Past comes before the other drops, as in the judge. An event its source stopped listing
   is not judged again, so it keeps the drop reason it had before its day passed. */
export const isDropped = (ev) => Boolean(ev.drop_reason && ev.drop_reason !== "past") && !isPast(ev);
export const isUpcoming = (ev) => !isPast(ev) && !isDropped(ev);

/* The day an event is listed under: an event that started earlier and still runs is listed today. */
export function placeDay(ev) {
  const start = startOf(ev);
  if (!start) return null;
  return start.day < today() && !isPast(ev) ? today() : start.day;
}
export function sortTime(ev) {
  const start = startOf(ev);
  if (!start || !start.time) return "";
  return start.day < today() && !isPast(ev) ? "" : start.time;
}

export function organiserNames(ev, orgById) {
  const names = ev.organiser_ids.map((id) => orgById.get(id)?.name).filter(Boolean);
  return names.length ? names.join(", ") : val(ev, "organiser") || "";
}
export function venueText(ev) {
  const venue = val(ev, "venue");
  if (venue && /^https?:/.test(venue)) return "Venue on the event page";
  return venue || val(ev, "address") || "";
}
export function searchText(ev, orgById) {
  return [val(ev, "title"), organiserNames(ev, orgById), val(ev, "venue"), val(ev, "description")].join(" ").toLowerCase();
}

/* The registration window in a few words, with the chip class that colours it. */
export function registration(ev) {
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

export function when(ev) {
  const start = munich(val(ev, "starts_at"));
  const end = munich(val(ev, "ends_at"));
  const from = formatMoment(val(ev, "starts_at"));
  if (!end || (start && end.day === start.day && end.time === start.time)) return from;
  if (start && end.day === start.day) return end.time ? `${from} to ${end.time}` : from;
  return `${from} to ${formatMoment(val(ev, "ends_at"))}`;
}

/* The judge's reasons ("price=free", "ai=core") in words, each starting with a capital. */
export function humanReason(reason) {
  if (reason === "networking") return "Built for meeting people";
  const [name, value] = reason.split("=");
  if (value === undefined) return reason.charAt(0).toUpperCase() + reason.slice(1);
  const map = {
    price: { unknown: "Price not stated", free: "Free", paid: "Paid", free_student_route: "Free route for students" },
    ai: { core: "AI keyword or stated AI focus", some: "Some AI content", unknown: "No AI keyword found", none: "Not AI-related" },
    organiser: { anchor: "Run by a community the radar is built around", community: "Run by an organiser the radar follows" },
  };
  if (name === "kind") return value === "None" ? "Kind not recognised" : `Kind: ${value.replace(/_/g, " ")}`;
  return map[name]?.[value] || reason;
}
