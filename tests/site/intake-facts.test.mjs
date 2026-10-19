import assert from "node:assert/strict";
import { test } from "node:test";

import { candidates, label, needsDay, pageName, recent, stepMarks, title } from "../../site/js/intake-facts.js";

const DAY = 86400000;
const intake = (fields = {}) => ({
  kind: "organisation",
  state: "queued",
  url: "https://www.example.org/",
  name: null,
  profile: null,
  result: null,
  log: [],
  updated_at: new Date().toISOString(),
  ...fields,
});
const event = (fields = {}) => intake({ kind: "event", url: "https://www.example.org/events/ai-night/", ...fields });
const logged = (...steps) => steps.map((step) => ({ step, text: "", level: "info" }));

test("an intake is named by what it found, else by its address", () => {
  assert.equal(title(intake({ name: "CDTM" })), "CDTM");
  assert.equal(title(intake({ profile: { name: "Example AI Club" } })), "Example AI Club");
  assert.equal(title(intake()), "example.org");
  assert.equal(title(event({ result: { title: "Applied AI Night" } })), "Applied AI Night");
  assert.equal(title(event()), "example.org/events/ai-night");
  assert.equal(pageName("not a url"), "not a url");
});

test("an added event says where it stands in its own words", () => {
  assert.equal(label(event({ state: "harvesting" })), "Reading the event");
  assert.equal(label(event({ state: "rejected" })), "Not added");
  assert.equal(label(event({ state: "done", result: { tier: "B" } })), "Added");
  assert.equal(label(event({ state: "done", result: { already: true } })), "Already on the radar");
  assert.equal(label(event({ state: "done", result: { waiting: true } })), "Waiting for the LLM");
  assert.equal(label(event({ state: "removed" })), "Removed");
  assert.equal(label(intake({ state: "review" }), true), "Needs your decision");
  assert.equal(label(intake({ state: "review" }), false), "Waiting for review");
});

test("an organisation's steps stop where it waits or failed", () => {
  assert.deepEqual(stepMarks(intake({ state: "finding" })), ["done", "done", "active", "", ""]);
  assert.deepEqual(stepMarks(intake({ state: "review" })), ["stopped", "", "", "", ""]);
  assert.deepEqual(stepMarks(intake({ state: "review", log: logged("checking") })), ["done", "stopped", "", "", ""]);
  assert.deepEqual(stepMarks(intake({ state: "failed", log: logged("checking", "profiling", "error") })),
    ["done", "failed", "", "", ""]);
});

test("an added event's steps stop at the check that refused it", () => {
  assert.deepEqual(stepMarks(event({ state: "checking" })), ["active", "", ""]);
  assert.deepEqual(stepMarks(event({ state: "done" })), ["done", "done", "done"]);
  assert.deepEqual(stepMarks(event({ state: "rejected", log: logged("checking") })), ["stopped", "", ""]);
  assert.deepEqual(stepMarks(event({ state: "rejected", log: logged("checking", "harvesting") })),
    ["done", "stopped", ""]);
  assert.deepEqual(stepMarks(event({ state: "failed", log: logged("error") })), ["failed", "", ""]);
  assert.deepEqual(stepMarks(event({ state: "removed", log: logged("checking", "done", "removed") })),
    ["done", "done", "failed"]);
});

test("the recent intakes of one kind are the open ones and those finished in the last three days", () => {
  const now = Date.now();
  const at = (days) => new Date(now - days * DAY).toISOString();
  const rows = [
    event({ id: 1, state: "checking", updated_at: at(9) }),
    event({ id: 2, state: "done", updated_at: at(1) }),
    event({ id: 3, state: "rejected", updated_at: at(4) }),
    intake({ id: 4, state: "done", updated_at: at(1) }),
    ...[5, 6, 7, 8, 9].map((id) => event({ id, state: "queued" })),
  ];
  assert.deepEqual(recent(rows, "event", now).map((i) => i.id), [1, 2, 5, 6, 7]);
  assert.deepEqual(recent(rows, "organisation", now).map((i) => i.id), [4]);
});

const search = (fields = {}) => intake({ log: [{ step: "search", text: "Found", level: "info" }], state: "review", ...fields });

test("the web search's candidates wait apart from the recent intakes", () => {
  const waiting = [search({ url: "https://b.example/" }), search({ url: "https://a.example/" })];
  const refused = search({ state: "rejected" });
  const approved = search({ state: "queued" });
  const all = [...waiting, refused, approved, intake()];
  assert.deepEqual(candidates(all).map((i) => i.url), ["https://a.example/", "https://b.example/"]);
  assert.deepEqual(recent(all, "organisation"), [approved, all[4]]);
  const lostCheck = search({ state: "rejected", log: logged("search", "checking") });
  assert.deepEqual(recent([lostCheck], "organisation"), [lostCheck]);
});

test("a found event waits for its day and place until an admin gives them", () => {
  assert.equal(needsDay(event({ state: "review", log: logged("search") })), true);
  assert.equal(needsDay(event({ state: "rejected", log: logged("search") })), true);
  assert.equal(needsDay(event({ state: "rejected", starts_on: "2026-11-05" })), false);
  assert.equal(needsDay(intake({ state: "review" })), false);
});
