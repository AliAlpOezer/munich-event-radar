/* The radar's Data API (ADR 0005): views and functions in the api schema, reached through
   supabase-js, which the page vendors (vendor/README.md). Every call throws an Error whose
   message is fit to show. */

import { API_SCHEMA, SUPABASE_KEY, SUPABASE_URL } from "../config.js";

const client = globalThis.supabase?.createClient(SUPABASE_URL, SUPABASE_KEY, {
  db: { schema: API_SCHEMA },
  // PKCE returns a ?code= query, which leaves the hash routes alone.
  auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});
const PAGE = 1000; // Supabase answers at most this many rows per request

async function read(request) {
  const { data, error } = await request;
  if (error) throw new Error(error.message || "The radar did not answer");
  return data;
}

async function every(view, order) {
  const rows = [];
  for (let from = 0; ; from += PAGE) {
    const page = await read(client.from(view).select("*").order(order).range(from, from + PAGE - 1));
    rows.push(...page);
    if (page.length < PAGE) return rows;
  }
}

export const events = () => every("events", "key");
export const organisers = () => every("organisers", "id");
export const boardStatus = () => read(client.rpc("board_status"));
export const me = () => read(client.rpc("me"));
/* Admins only; anyone else is refused. */
export const latestRun = async () => (await read(client.from("runs").select("*").order("id", { ascending: false }).limit(1)))[0] ?? null;
/* A user's own suggestions and added events, or every one for an admin. */
export const intakes = () => read(client.from("intakes").select("*").order("id", { ascending: false }).limit(20));
export const intake = (id) => read(client.from("intakes").select("*").eq("id", id).maybeSingle());

export const setStatus = (key, status) => read(client.rpc("set_status", { event_key: key, status }));
export const setStar = (key, starred) => read(client.rpc("set_star", { event_key: key, starred }));
export const suggest = ({ url, eventsUrl, name }) =>
  read(client.rpc("suggest_organisation", { url, events_url: eventsUrl || null, name: name || null }));
export const decide = (id, approve) => read(client.rpc("decide_intake", { intake_id: id, approve }));
export const addEvent = ({ url, day, place }) => read(client.rpc("suggest_event", { url, starts_on: day, place }));
/* Admins only. */
export const removeEvent = (id) => read(client.rpc("remove_event", { intake_id: id }));

/* Leaves the page for the provider, which returns to `returnTo` with a code to exchange. */
export async function logIn(provider, returnTo) {
  const { error } = await client.auth.signInWithOAuth({ provider, options: { redirectTo: returnTo } });
  if (error) throw new Error(error.message);
}

/* Logs in with an ID token the provider issued on this page (google.js); `nonce` is the one
   whose hash the token carries. */
export async function logInWithIdToken(provider, token, nonce) {
  const { error } = await client.auth.signInWithIdToken({ provider, token, nonce });
  if (error) throw new Error(error.message);
}

/* Ends the session in this browser only. */
export async function logOut() {
  const { error } = await client.auth.signOut({ scope: "local" });
  if (error) throw new Error(error.message);
}

/* Calls back with the logged-in user or null: once on start, then on every change.
   supabase-js runs this callback under its auth lock, and a client call made inside it
   would wait for that lock forever, so the callback runs after it. */
export function watchSession(callback) {
  if (!client) throw new Error("The page's database client did not load");
  client.auth.onAuthStateChange((_event, session) => {
    setTimeout(() => callback(session?.user ?? null), 0);
  });
}
