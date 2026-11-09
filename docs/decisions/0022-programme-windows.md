# ADR 0022: A programme's application window is an event its page states

**Status:** DECIDED, building on branch `programme-windows`
**Date:** 2026-11-09
**Inputs:** step 2 of the network-value plan in the records repository
(`docs/context/ranking-quality.md`): "programme windows as their own source: admission
pages of CDTM, TUM.ai, Manage&More, START, TUM: Junge Akademie". Alp, 2026-11-09:
"continue to working on known issues". Amends ADR 0002 (the grounding verifier, D8, and
the scope filter's "past") and the extraction prompt's year rule in effect, not in text.

## What this is for

A programme you join by applying (CDTM's class, TUM.ai's membership round, Manage and More,
Digital Product School) is worth as much as any event on the board, and its only action is
the application. The board must show the window while it is open and drop it once it
closes; a missed window is the failure ADR 0002 names.

## What was measured

All read-only, 2026-11-09: the live pages fetched with robots.txt honoured and parsed with
`event_radar.parse`; the hosted observations through scratch probes.

| Finding | Evidence |
|---|---|
| None of the six admission pages has structured data; every one goes to the LLM | `parse(..., Kind.EVENT_PAGE)`: `jsonld@3`, no events, `needs_llm` |
| Manage and More, Digital Product School and TUM.ai state their windows in the page text | "Application for the start in summer semester 2027 (current round). 06 December 2026 Application Deadline" (https://www.manageandmore.de/application); "Batch#28: Feb. 22 to May 14, 2027 Applications open until Dec. 2, 2026" (https://www.digitalproductschool.io/apply); "Application deadline 23:59, Munich time 25 Oct (passed)" (https://www.tum-ai.com/apply), all checked 2026-11-09 |
| CDTM's page states its window only once rendered: the plain HTML says "Checking the current application phase…", the rendered page "Applications are open ... Until 30 November 2026" and "Applications: 1-30 November 2026" | https://cdtm.com/program/study/apply (https://cdtm.com/munich/admission redirects there), plain and Playwright-rendered, checked 2026-11-09 |
| START's apply page renders no text, and its homepage gives a month only: "Summer Semester applications open in April 2027" | https://www.startmunich.de/apply and https://www.startmunich.de/, checked 2026-11-09 |
| Junge Akademie's apply page names a closed round without its year: "Die Bewerbungsphase für die #class27 endete am 31. August" | https://www.ja.tum.de/ja/apply/, checked 2026-11-09 |
| TUM.ai's real window was dropped as `online_only`: the LLM gave `attendance` online with the quote "Apply now" | hosted observation of https://www.tum-ai.com/apply, 2026-11-08 |
| The same read moved the window to 2027 (2027-09-28 to 2027-10-25), since the prompt says "If the page omits the year, use the next occurrence after TODAY" and the dates had passed | same observation, every date `year_stated` false |
| The board's "TUM.ai Application Period" was A from a form that closed on 25.10.2025: it states no day of its own, and the judge's "past" read only the start and end | hosted event `f90ccb96c022`, source https://tally.so/r/BzWNEK ("Our application was closed on 25.10.2025 at 23:59") |
| Of 30 yearless dates in the latest hosted observations, the year nearest the read day differs from the LLM's in 5, each a correction: TUM.ai's four, and a Children's University opening moved to 2027 | read-only probe over the latest observation per page and method |
| Of 26 LLM answers of `attendance` online, the 6 whose quote names no online form are all wrong ("Apply now", "Kostenlos", "public", the audience of PhilML'26 at MCML, Dies Academicus); so are the 2 ungrounded `mixed` answers. The MDSI talk of 19.11 was dropped as online though its page says "Garching (Online-Teilnahme möglich)" | same probe; https://www.mdsi.tum.de/mdsi/aktuelles/veranstaltungen/volltext/article/tum-research-data-network-forschungsdatenmanagement-fdm-in-den-lebenswissenschaften/, checked 2026-11-09 |

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | An event's last day is its end, else its start, else its registration's close; a programme's close ends it however far ahead its class starts. The judge (`Judge.last_day`) and the page (`facts.js`) read it alike | A new drop reason, "closed": a closed window is past for the board, and ADR 0002 names no other state. Closing every event at its registration's close: a conference whose tickets are gone still happens, and its waitlist is worth showing |
| 2 | A date whose quote states no year takes the year that puts it nearest the day the page was read, in the verifier; `year_stated` false is kept | Trusting the LLM's year: wrong twice on TUM.ai alone. The next occurrence, as the prompt asks: a passed deadline jumps a year ahead, the reproduced error. Rejecting yearless dates: TUM.ai and Junge Akademie state none, and the window would be lost |
| 3 | `attendance` online or mixed is kept only when its quote names an online form (online, virtual, Zoom, webinar, livestream, hybrid, remote, Webex, Microsoft Teams, Google Meet) | Leaving it to the judge: the judge has only the value, not the quote. Asking the LLM again with a stricter prompt: a new extraction version re-asks every page, and the quote check catches the same errors for free |
| 4 | A programme window is an ordinary event of kind `programme`, from an ordinary `event_page` source; CDTM's is a browser source | A window entity of its own: the store, resolver, judge, stars and page all work on events, and the extraction already reads "a page about an application round" as one. A parser for CDTM's Next.js data (`intakes[].applicationsEnd`, checked 2026-11-06): the rendered page states the same, and one site's component props break silently when it is redesigned |
| 5 | The seed adds CDTM's admission page, Manage and More, Digital Product School and TUM: Junge Akademie as `community` organisers with their application pages; TUM.ai's apply page is already a source | START Munich: no page states a day (month only, apply page empty when rendered); left until it does. Seeding the programmes as anchors: Alp placed UnternehmerTUM as `community` (ADR 0021 D10), and its programmes follow it |
| 6 | The extraction prompt keeps its year rule, and its version stays `events-v2` | Rewriting the rule: a new version re-asks every cached page for no gain, since D2 replaces the year anyway. Rewrite it with the next prompt change |

## Check

- Tests first, failing before the change: the TUM.ai, Junge Akademie and Manage and More
  quotes take the year nearest the read; a stated year stays; "Apply now", "public" and
  "Teams of 2-4" ground no online attendance, "Zoom-Webinar" and "Hybrid" do; the stale
  TUM.ai form is past; a conference whose registration closed is not; a programme is past
  at its close and stays while it is open; the page agrees (node test).
- The hosted board, read-only, through `network_dump.py` with this branch's judge, the
  verifier's rules replayed over the stored LLM fields (each keeps its quote and read time),
  compared with `network-value`'s verdicts in the same minute.
- After the first harvest and browse run with the new seed: the four admission pages on
  the board with their windows, read by hand.

## How it landed (2026-11-09)

Dry run, read-only, base `network-value` (715cfc8) and this branch back to back: 420 groups
each, 4 move, none leaves or joins.

| | A | B | C | online only | past |
|---|---|---|---|---|---|
| `network-value` | 24 | 23 | 16 | 19 | 151 |
| this branch | 23 | 24 | 17 | 16 | 153 |

| Move | Event | Why |
|---|---|---|
| A to past | TUM.ai Application Period (tally form, closed 25.10.2025) | D1 |
| online only to past | TUM.ai Call for members (the 2026/27 round, closed 25.10.2026) | D2 and D3 |
| online only to B | TUM Research Data Network: FDM in den Lebenswissenschaften (MDSI, 19.11, Garching) | D3 |
| online only to C | Dies Academicus 2025 | D3; undated, as before |

Residual: "Dies Academicus 2025" is TUM's page for last year's ceremony and states no day,
so it shows as an undated C; it belongs to the undated events of the 2026-11-03 health
check, not to this ADR. The verifier's rules act on new reads only: a page's observation
keeps its stored fields until the page's content changes and its cached answer is verified
again.
