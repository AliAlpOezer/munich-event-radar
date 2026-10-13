/* Google's own sign-in button (Google Identity Services). Google then names this site on its
   consent screen, not the Supabase project's domain, and hands back an ID token that
   Supabase checks. The script loads only once someone opens the login, so visitors who
   never log in send nothing to Google.
   https://developers.google.com/identity/gsi/web/guides/display-button */

const SCRIPT = "https://accounts.google.com/gsi/client";
let loading = null;

function load() {
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = () => resolve(globalThis.google.accounts.id);
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new Error("Google's sign-in button did not load"));
    };
    document.head.append(script);
  });
  return loading;
}

/* Google puts the hash of the nonce into the token; Supabase gets the nonce itself and
   compares, so a token cannot be replayed into another login. */
async function nonces() {
  const raw = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  const hashed = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return { raw, hashed };
}

/* Renders the button into `slot`; `onToken(token, nonce)` runs once Google has signed the
   person in. */
export async function renderGoogleButton(slot, clientId, onToken) {
  const [id, nonce] = await Promise.all([load(), nonces()]);
  id.initialize({
    client_id: clientId,
    nonce: nonce.hashed,
    callback: ({ credential }) => onToken(credential, nonce.raw),
  });
  id.renderButton(slot, {
    type: "standard",
    theme: document.documentElement.dataset.theme === "dark" ? "filled_black" : "outline",
    size: "large",
    text: "continue_with",
    shape: "rectangular",
    logo_alignment: "center",
    locale: "en",
    width: Math.min(400, Math.round(slot.getBoundingClientRect().width)),
  });
}
