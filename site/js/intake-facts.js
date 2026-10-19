/* What an intake row from api.intakes means: its name, its state in words, and how far it
   got. An organisation's intake (ADR 0003) and an added event's (ADR 0014) have their own
   steps; either kind can come from the daily web search (ADR 0016). */

const DAY_MS = 86400000;
export const STEPS = {
  organisation: [
    ["checking", "Check that the website works"],
    ["profiling", "Find out who they are"],
    ["finding", "Find their event pages"],
    ["harvesting", "Collect events, tickets and deadlines"],
    ["done", "On the radar"],
  ],
  event: [
    ["checking", "Check that the page names the day and the place"],
    ["harvesting", "Read the event, its tickets and deadlines"],
    ["done", "On the board"],
  ],
};
const LABEL = {
  queued: "Waiting", checking: "Checking", profiling: "Checking who they are", finding: "Finding event pages",
  harvesting: "Collecting events", done: "Added", rejected: "Rejected", failed: "Failed", removed: "Removed",
};
export const FINAL = new Set(["done", "rejected", "failed", "removed"]);

export const isEvent = (intake) => intake.kind === "event";
const kindOf = (intake) => (isEvent(intake) ? "event" : "organisation");

/* A suggestion in review either waits for an admin before any fetch, or stopped after the
   check could not confirm the organisation. */
export const checked = (intake) => intake.log.some((entry) => entry.step === "checking");

/* An intake the daily web search proposed: its first log line says so. */
export const found = (intake) => intake.log[0]?.step === "search";

/* A found event that waits for its day and place, which an admin gives to approve it. */
export const needsDay = (intake) => isEvent(intake) && !intake.starts_on && (intake.state === "review" || intake.state === "rejected");

/* The search's candidates waiting for an admin, oldest first; an admin's refusal ends them. */
export const candidates = (intakes) => intakes.filter((i) => found(i) && i.state === "review").reverse();
const refused = (intake) => found(intake) && (intake.state === "review" || (intake.state === "rejected" && !checked(intake)));

export function label(intake, admin = false) {
  if (intake.state === "review") return admin ? "Needs your decision" : "Waiting for review";
  if (isEvent(intake)) {
    if (intake.state === "harvesting") return "Reading the event";
    if (intake.state === "rejected") return "Not added";
    if (intake.state === "done" && intake.result?.already) return "Already on the radar";
    if (intake.state === "done" && intake.result?.waiting) return "Waiting for the LLM";
  }
  return LABEL[intake.state] || intake.state;
}

/* A web address as a short name: its host, and for an event's page the path too. */
export function pageName(url, withPath = false) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    const path = parsed.pathname.replace(/\/+$/, "");
    return withPath && path ? `${host}${path}` : host;
  } catch {
    return url;
  }
}

export function title(intake) {
  if (intake.name) return intake.name;
  if (intake.profile?.name) return intake.profile.name;
  if (intake.result?.title) return intake.result.title;
  return pageName(intake.url, isEvent(intake));
}

/* The furthest step the log reached. */
function reached(intake, order) {
  const steps = intake.log.map((entry) => order.indexOf(entry.step)).filter((i) => i >= 0);
  return steps.length ? Math.max(...steps) : 0;
}

/* Each step's mark: done, active, stopped (waiting or refused), failed (or removed), or none
   yet. */
export function stepMarks(intake) {
  const order = STEPS[kindOf(intake)].map(([key]) => key);
  if (intake.state === "done") return order.map(() => "done");
  let at = Math.max(order.indexOf(intake.state), 0);
  let mark = "active";
  if (intake.state === "removed") [at, mark] = [order.length - 1, "failed"];
  if (intake.state === "review" || intake.state === "rejected") {
    at = isEvent(intake) ? reached(intake, order) : checked(intake) ? 1 : 0;
    mark = "stopped";
  }
  if (intake.state === "failed") [at, mark] = [reached(intake, order), "failed"];
  return order.map((_, i) => (i < at ? "done" : i === at ? mark : ""));
}

/* One kind's intakes worth listing: the open ones, and those finished in the last three
   days, newest first as the API returns them; not the search's candidates, listed apart. */
export const recent = (intakes, kind, now = Date.now()) => intakes
  .filter((i) => kindOf(i) === kind && !refused(i))
  .filter((i) => !FINAL.has(i.state) || now - new Date(i.updated_at).getTime() < 3 * DAY_MS)
  .slice(0, 5);
