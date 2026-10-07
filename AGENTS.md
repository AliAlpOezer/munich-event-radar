# Repo rules: munich-event-radar

## What this repo is
The hunt for AI/ML events, organisations, conferences, clubs and programmes in Munich:
a curated record (prose and tables) today, and the home of the event-radar agent and its
app next. Split out of `~/dev/munich-masters-career` on 2026-10-07 (ADR 0001). Study
planning stays there; link to it, never copy study facts here.

## Non-negotiables

- **Never invent a date, deadline, price, organiser or club name.** Every factual claim
  about an event or organisation carries a source URL and the date it was checked, inline:
  `(source: <url>, checked 2026-10-07)`. If it could not be verified, write `UNVERIFIED`.
- **An aggregator listing is not the event page.** Open the organiser's own page and the
  ticket platform before recording "no registration info". Mark rows OWN or AGG.
- **Raw first, curated second.** Scraped pages and sweeps go to `research/`. Only distilled
  conclusions, with sources, move into `radar/` and `timeline/`.
- **Every event date goes into `timeline/calendar.md`**, even if it is also mentioned
  elsewhere. (Wayfinder Q1 makes the agent's database the owner once it ships; until then
  this file is.)
- **An event is not recorded until its registration is.** For every event worth attending,
  add a row to "Upcoming actions" in `timeline/calendar.md` for getting the ticket: when
  registration opens, when it closes or sells out, and the link. If the page shows none,
  the action is "recheck weekly", never silence. Default assumption: popular free events
  sell out weeks ahead. (AI@STRATEGY 2026 was missed this way: listed 2026-10-02 as a
  date only, sold out by the event on 2026-10-07.)
- Decisions that are expensive to reverse get an ADR in `docs/decisions/`, numbered,
  including the alternatives that were rejected and why.

## Style
- German terms stay in German, with a short gloss on first use.
- Plain dashes, never em dashes.
- Tables over paragraphs for anything comparable.

## Shared knowledge

Keep this file as the canonical project contract for every harness. Native adapters
reference it. Preserve existing context/wiki paths, source evidence, and domain rules.
For documentation or memory work, follow `~/dotfiles/agents/docs/knowledge.md` when
available; outside this workstation, keep Markdown pages with YAML metadata, relative
links, a compact index, and a short update log. Read only task-relevant knowledge.
Personal skills live at `~/.agents/skills/`; project skills at `.agents/skills/` if present.

Start with `docs/context/event-tracking.md`; the agent's requirements live in
`docs/context/wayfinder/event-radar/map.md` and its architecture in
`docs/decisions/0002-event-radar-architecture.md`.
