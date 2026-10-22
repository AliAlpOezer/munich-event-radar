import assert from "node:assert/strict";
import { test } from "node:test";

import { visibleEvents } from "../../site/js/filter.js";
import { today } from "../../site/js/time.js";

const ALL = { q: "", org: "", tiers: ["A", "B", "C"], past: false, dropped: false, starred: false };
const day = (days) => {
  const d = new Date(`${today()}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const event = (key, { tier = "B", days = 3, starred = false, organiser = 1 } = {}) => ({
  key,
  tier,
  starred,
  drop_reason: null,
  organiser_ids: [organiser],
  fields: { title: { value: `Event ${key}` }, starts_at: { value: `${day(days)}T18:00:00` } },
});
const keys = (events, filters, loggedIn = true) =>
  visibleEvents(events, { ...ALL, ...filters }, { orgById: new Map(), loggedIn }).map((ev) => ev.key);

const events = [
  event("a", { starred: true }),
  event("b"),
  event("c", { tier: "C", starred: true }),
  event("d", { days: -3, starred: true }),
  event("e", { organiser: 2, starred: true }),
];

test("starred shows only the user's starred events, within the other filters", () => {
  assert.deepEqual(keys(events, {}), ["a", "b", "c", "e"]);
  assert.deepEqual(keys(events, { starred: true }), ["a", "c", "e"]);
  assert.deepEqual(keys(events, { starred: true, past: true }), ["a", "c", "d", "e"]);
  assert.deepEqual(keys(events, { starred: true, tiers: ["B"], org: "1" }), ["a"]);
  assert.deepEqual(keys(events, { starred: true, q: "event c" }), ["c"]);
});

test("an online event that has ended shows with the past events, not the online ones", () => {
  const online = (key, days) => ({ ...event(key, { tier: null, days }), drop_reason: "online_only" });
  const both = [online("ended", -11), online("coming", 3)];
  assert.deepEqual(keys(both, { dropped: true }), ["coming"]);
  assert.deepEqual(keys(both, { past: true }), ["ended"]);
});

test("a remembered starred filter does not empty the board once logged out", () => {
  assert.deepEqual(keys(events, { starred: true }, false), keys(events, {}, false));
});
