/* Logging in with GitHub or Google through Supabase Auth (ADR 0004), and the account button. */

import * as api from "./api.js";
import { $, esc } from "./dom.js";
import { fillDrawer, isOpen, showDrawer } from "./drawer.js";
import { state } from "./state.js";
import { stash, unstash } from "./storage.js";

const RETURN_KEY = "radar.afterLogin";
const PROVIDERS = { github: "GitHub", google: "Google" };
const REASONS = {
  status: "Log in to keep your own status on events.",
  suggest: "Log in to suggest an organisation.",
  intake: "Log in to follow your suggestion.",
};

const nameOf = (user) => user.user_metadata?.full_name || user.user_metadata?.name || user.email || "Account";

export function renderAccountButton() {
  const button = $("account-button");
  const user = state.user;
  if (!user) {
    button.innerHTML = `<span class="account-name">Log in</span>`;
    button.setAttribute("aria-label", "Log in");
    return;
  }
  const name = nameOf(user);
  const initial = [...name.trim()][0]?.toUpperCase() || "?";
  button.innerHTML = `<span class="avatar" aria-hidden="true">${esc(initial)}</span><span class="account-name">${esc(name)}</span>`;
  button.setAttribute("aria-label", `Account: ${name}`);
}

/* The login choice, remembering the place to come back to: the drawer or tab the person
   was on when they chose to log in. */
export function openLogin(reason, from) {
  if (!isOpen("login")) stash(RETURN_KEY, location.hash || state.tabHash);
  showDrawer({ mode: "login", title: "Log in", hash: "#account", from });
  fillDrawer(`
    ${REASONS[reason] ? `<p class="note warn">${esc(REASONS[reason])}</p>` : ""}
    <p class="lede">Logging in lets you keep your own applied, registered or skipped status on events and
      suggest organisations for the radar. Everything else is open without an account.</p>
    <div class="login-options">${Object.entries(PROVIDERS).map(([id, label]) =>
      `<button type="button" class="btn" data-provider="${id}">Continue with ${label}</button>`).join("")}</div>
    <p class="note">Google or GitHub shares your name, email address and profile picture with the radar.
      The <a href="privacy">privacy notice</a> says what is kept and how to delete it.</p>
    <p class="form-error" id="login-error" role="alert" hidden></p>`);
  for (const button of document.querySelectorAll("[data-provider]")) {
    button.addEventListener("click", () => logIn(button.dataset.provider, button));
  }
}

async function logIn(provider, button) {
  button.disabled = true;
  try {
    await api.logIn(provider, location.origin + location.pathname);
  } catch (error) {
    const note = $("login-error");
    note.textContent = `Could not start the login: ${error.message}`;
    note.hidden = false;
    button.disabled = false;
  }
}

export function openAccount(from) {
  const user = state.user;
  if (!user) return openLogin(null, from);
  const provider = PROVIDERS[user.app_metadata?.provider] || user.app_metadata?.provider || "an account";
  showDrawer({ mode: "account", title: "Your account", hash: "#account", from });
  fillDrawer(`
    <section><dl class="facts">
      <dt>Name</dt><dd>${esc(nameOf(user))}</dd>
      ${user.email ? `<dt>Email</dt><dd>${esc(user.email)}</dd>` : ""}
      <dt>Logged in with</dt><dd>${esc(provider)}</dd>
      ${state.admin ? `<dt>Role</dt><dd>Admin</dd>` : ""}
    </dl></section>
    <div class="row-actions"><button type="button" class="btn" id="log-out">Log out</button></div>
    <p class="form-error" id="logout-error" role="alert" hidden></p>`);
  $("log-out").addEventListener("click", logOut);
}

async function logOut() {
  try {
    await api.logOut();
  } catch (error) {
    const note = $("logout-error");
    note.textContent = `Could not log out: ${error.message}`;
    note.hidden = false;
  }
}

/* Where to go once a login has come back, if the page left for one. */
export const takeReturn = () => unstash(RETURN_KEY);
