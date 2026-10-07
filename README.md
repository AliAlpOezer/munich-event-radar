# Munich event radar

Finds, ranks and tracks the AI/ML, startup and tech events, organisations, conferences and
programmes in Munich worth Alp's time, with their registration windows. Split out of
`munich-masters-career` on 2026-10-07 (`docs/decisions/0001-separate-repo.md`).

## Layout

| Path | Holds |
|---|---|
| `radar/event-radar.md` | Organisers that matter and upcoming events, ranked A/B/C, with registration |
| `radar/clubs.md` | Munich AI and entrepreneurship map: clubs, joint institutions, programmes |
| `radar/fairs.md` | Job fairs and recruiting events |
| `timeline/calendar.md` | Every event date and registration action |
| `research/` | Raw sweeps, never cited directly |
| `docs/context/` | Handoffs: `event-tracking.md`, and the agent design in `wayfinder/event-radar/` |
| `docs/decisions/` | ADRs |

## Status (2026-10-07)

- Curated radar and calendar moved here with their git history.
- Next: the event-radar agent and app. Requirements are in wayfinding
  (`docs/context/wayfinder/event-radar/map.md`); research questions R1-R3 are open, then
  `system-design`, then UI design.
