/* Per-browser conveniences: filters, and where to return after logging in. The page works
   the same when storage is blocked; it only forgets. */

export function restore(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : { ...fallback };
  } catch {
    return { ...fallback };
  }
}

export function persist(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: filters reset on reload */
  }
}

/* Kept for this tab only, across the round trip to the login provider. */
export function stash(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* storage blocked: the page opens on the timeline after logging in */
  }
}

export function unstash(key) {
  try {
    const value = sessionStorage.getItem(key);
    sessionStorage.removeItem(key);
    return value;
  } catch {
    return null;
  }
}
