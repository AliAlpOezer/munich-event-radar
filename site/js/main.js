/* Starts the page: wires the controls, follows the login session, routes the hash, and
   re-renders every view whenever the state changes. */

import { openAccount, openLogin, renderAccountButton, takeReturn } from "./account.js";
import * as api from "./api.js";
import * as board from "./board.js";
import { $, skeleton } from "./dom.js";
import { closeDrawer, isOpen } from "./drawer.js";
import { openEvent, refreshEvent } from "./event-detail.js";
import { loadIntakes, openAddForm, openIntake, renderIntakes } from "./intakes.js";
import { renderOrganisations, showOrganisation, wireOrganisations } from "./organisations.js";
import { changed, onChange, state } from "./state.js";
import { wireTheme } from "./theme.js";
import { renderHero, renderOrganiserFilter, renderTimeline, showOrganiserEvents, wireTimeline } from "./timeline.js";

const POLL_MS = 60000;

function renderAll() {
  renderOrganiserFilter();
  renderHero();
  renderTimeline();
  renderOrganisations();
  renderIntakes();
  renderAccountButton();
  board.renderRun();
  refreshEvent();
}

function showView() {
  const hash = location.hash;
  const event = hash.match(/^#event-([\w-]+)$/);
  if (event) {
    if (state.events.length) openEvent(event[1]);
    return;
  }
  const intake = hash.match(/^#intake-(\d+)$/);
  if (intake) {
    if (!isOpen("intake", Number(intake[1]))) openIntake(Number(intake[1]));
    return;
  }
  if (hash === "#add-organisation") {
    if (!isOpen("form")) openAddForm();
    return;
  }
  if (hash === "#account") {
    if (!isOpen("account") && !isOpen("login")) openAccount();
    return;
  }
  const view = hash === "#organisations" ? "organisations" : "timeline";
  const switched = state.tabHash !== `#${view}`;
  state.tabHash = `#${view}`;
  for (const name of ["timeline", "organisations"]) {
    $(`view-${name}`).hidden = name !== view;
    $(`tab-${name}`).setAttribute("aria-selected", String(name === view));
  }
  /* A view opens at its toolbar, not at the other view's scroll position. */
  const main = document.querySelector("main");
  const bar = document.querySelector(".bar").offsetHeight;
  if (switched && main.getBoundingClientRect().top < bar) window.scrollTo({ top: main.offsetTop - bar });
}

/* The first call starts the page; later ones reload what depends on who is logged in.
   A refreshed token for the same person changes nothing. */
let started = false;
async function sessionChanged(user) {
  const same = started && (user?.id ?? null) === (state.user?.id ?? null);
  state.user = user;
  if (same) return;
  const first = !started;
  started = true;
  try {
    state.admin = user ? Boolean((await api.me()).admin) : false;
    await Promise.all([board.load(), loadIntakes()]);
    changed();
    board.showBanner(null);
  } catch (error) {
    board.showBanner(error);
  }
  if (!user && (isOpen("account") || isOpen("intake") || isOpen("form"))) closeDrawer();
  const back = user ? takeReturn() : null;
  if (back && back !== location.hash) location.hash = back;
  else if (back || first) showView();
  else if (isOpen("login")) closeDrawer();
}

function wire() {
  wireTimeline();
  wireOrganisations();
  wireTheme();
  document.addEventListener("click", (e) => {
    const target = e.target;
    const row = target.closest(".event[data-key]");
    if (row) return openEvent(row.dataset.key, row);
    const intakeRow = target.closest("[data-intake]");
    if (intakeRow) return openIntake(Number(intakeRow.dataset.intake), intakeRow);
    const org = target.closest("[data-open-org]");
    if (org) return showOrganisation(Number(org.dataset.openOrg));
    const show = target.closest("[data-show-org]");
    if (show) return showOrganiserEvents(show.dataset.showOrg);
    const login = target.closest("[data-login]");
    if (login) return openLogin(login.dataset.login, login);
  });
  $("add-org").addEventListener("click", (e) => openAddForm(e.currentTarget));
  $("account-button").addEventListener("click", (e) => openAccount(e.currentTarget));
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
    if (document.visibilityState === "visible") board.poll();
  });
  setInterval(() => {
    if (document.visibilityState === "visible") board.poll();
  }, POLL_MS);
}

function trackBarHeight() {
  const bar = document.querySelector(".bar");
  const set = () => document.documentElement.style.setProperty("--bar-h", `${bar.offsetHeight}px`);
  new ResizeObserver(set).observe(bar);
  set();
}

trackBarHeight();
onChange(renderAll);
wire();
renderAccountButton();
$("timeline-list").innerHTML = skeleton(8);
$("org-list").innerHTML = skeleton(6);
try {
  api.watchSession(sessionChanged);
} catch (error) {
  board.showBanner(error);
}
