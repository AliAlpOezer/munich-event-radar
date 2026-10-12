/* Loading the board, noticing when it changed, and the status line in the top bar. */

import * as api from "./api.js";
import { $, esc } from "./dom.js";
import { changed, state } from "./state.js";
import { formatMoment, munich, relative } from "./time.js";

export async function load() {
  const [events, organisers, board, run] = await Promise.all([
    api.events(),
    api.organisers(),
    api.boardStatus(),
    state.admin ? api.latestRun() : null,
  ]);
  Object.assign(state, { events, organisers, board, run, orgById: new Map(organisers.map((o) => [o.id, o])) });
  changed();
}

/* Reloads only when board_status() says something changed; the call itself is tiny. */
export async function poll() {
  try {
    const board = await api.boardStatus();
    if (board.version !== state.board?.version) await load();
    else renderRun();
    showBanner(null);
  } catch (error) {
    showBanner(error);
  }
}

export function renderRun() {
  const box = $("run-status");
  const text = box.querySelector(".run-text");
  const run = state.admin ? state.run : null;
  const harvested = state.board?.harvested_at;
  box.title = "";
  if (run) {
    const s = run.summary || {};
    if (!run.finished_at) {
      box.dataset.state = "";
      text.textContent = `Run in progress since ${munich(run.started_at).time}`;
    } else {
      box.dataset.state = run.outcome === "ok" ? "ok" : "failed";
      const surfaced = (s.tier_A || 0) + (s.tier_B || 0);
      text.textContent = run.outcome === "ok"
        ? `Last run ${relative(run.finished_at)}, ${surfaced} A or B`
        : `Last run failed ${relative(run.finished_at)}`;
    }
    box.title = [
      `Started ${formatMoment(run.started_at)}`,
      `Fetched ${s.fetched ?? 0} pages, ${s.fetch_errors ?? 0} errors`,
      `A ${s.tier_A ?? 0}, B ${s.tier_B ?? 0}, C ${s.tier_C ?? 0}`,
      s.error ? `Error: ${s.error}` : "",
    ].filter(Boolean).join("\n");
  } else if (harvested) {
    box.dataset.state = "ok";
    text.textContent = `Updated ${relative(harvested)}`;
    box.title = `Last collected ${formatMoment(harvested)}`;
  } else {
    box.dataset.state = "";
    text.textContent = state.board ? "No runs yet" : "Loading";
  }
}

export function showBanner(error) {
  const banner = $("banner");
  if (!error) {
    banner.hidden = true;
    return;
  }
  banner.innerHTML = `<span>The radar did not answer (${esc(error.message)}). It retries every minute.</span>
    <button type="button" class="btn" id="retry">Retry now</button>`;
  banner.hidden = false;
  $("retry").addEventListener("click", () => load().then(() => showBanner(null), showBanner));
}
