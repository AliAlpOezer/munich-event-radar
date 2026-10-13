"""Run the page in `site/` against a local Postgres, standing in for Supabase (ADR 0005,
"Local stand-in"), since `supabase start` does not fit in 4 GB.

- `/rest/v1/` goes to PostgREST, started here as `authenticator` on the api schema, as
  Supabase's Data API does.
- `/auth/v1/` is a stand-in for Supabase Auth: the PKCE login flow supabase-js runs, but the
  "provider" is a form that logs anyone in by email. It mints tokens PostgREST accepts.
- Everything else is the site, with the headers in `site/_headers`, and a `config.js`
  pointing the page here.

    python tools/devsite.py --database postgresql://postgres@localhost/radar_dev

Make a logged-in email an admin with `event-radar grant-admin EMAIL` against the same
database. Only a database on this machine is accepted.
"""

import argparse
import base64
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from event_radar.store import Store

SITE = Path(__file__).resolve().parents[1] / "site"
API_KEY = "sb_publishable_devsite"
TOKEN_SECONDS = 3600
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}
TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Tokens:
    """HS256 tokens shaped like Supabase Auth's, signed with the secret PostgREST checks."""

    def __init__(self) -> None:
        self.secret = secrets.token_hex(32)

    def sign(self, claims: dict) -> str:
        head = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = b64url(json.dumps(claims).encode())
        mac = hmac.new(self.secret.encode(), f"{head}.{body}".encode(), hashlib.sha256)
        return f"{head}.{body}.{b64url(mac.digest())}"

    def verify(self, token: str) -> dict | None:
        try:
            head, body, mac = token.split(".")
        except ValueError:
            return None
        expected = hmac.new(self.secret.encode(), f"{head}.{body}".encode(), hashlib.sha256)
        if not hmac.compare_digest(b64url(expected.digest()), mac):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return claims if claims["exp"] > time.time() else None


class Auth:
    """The parts of Supabase Auth that supabase-js uses for an OAuth login with PKCE."""

    def __init__(self, dsn: str, tokens: Tokens) -> None:
        self.dsn = dsn
        self.tokens = tokens
        self.codes: dict[str, dict] = {}  # code -> user and PKCE challenge
        self.refresh: dict[str, dict] = {}  # refresh token -> user

    def user(self, email: str, provider: str) -> dict:
        """The user for this email, created in auth.users on first login."""
        email = email.strip().lower()
        user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"mailto:{email}"))
        with psycopg.connect(self.dsn, autocommit=True) as db:
            db.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (user_id, email),
            )
        return {
            "id": user_id,
            "aud": "authenticated",
            "role": "authenticated",
            "email": email,
            "app_metadata": {"provider": provider, "providers": [provider]},
            "user_metadata": {"email": email, "full_name": email.split("@")[0]},
        }

    def code(self, user: dict, challenge: str, method: str) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"user": user, "challenge": challenge, "method": method.lower()}
        return code

    def session(self, user: dict) -> dict:
        now = int(time.time())
        claims = {
            "aud": "authenticated",
            "iat": now,
            "exp": now + TOKEN_SECONDS,
            "sub": user["id"],
            "email": user["email"],
            "role": "authenticated",
            "app_metadata": user["app_metadata"],
            "user_metadata": user["user_metadata"],
            "is_anonymous": False,
        }
        refresh = secrets.token_urlsafe(24)
        self.refresh[refresh] = user
        return {
            "access_token": self.tokens.sign(claims),
            "token_type": "bearer",
            "expires_in": TOKEN_SECONDS,
            "expires_at": now + TOKEN_SECONDS,
            "refresh_token": refresh,
            "user": user,
        }

    def exchange(self, code: str, verifier: str) -> dict | None:
        found = self.codes.pop(code, None)
        if not found:
            return None
        if found["method"] == "s256":
            verifier = b64url(hashlib.sha256(verifier.encode()).digest())
        return self.session(found["user"]) if verifier == found["challenge"] else None

    def renew(self, refresh: str) -> dict | None:
        user = self.refresh.pop(refresh, None)
        return self.session(user) if user else None


def read_headers(path: Path) -> list[tuple[re.Pattern, list[tuple[str, str]]]]:
    """Cloudflare Pages' `_headers`: a path pattern, then indented `Name: value` lines."""
    rules: list[tuple[re.Pattern, list[tuple[str, str]]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            pattern = re.escape(line.strip()).replace(r"\*", ".*")
            rules.append((re.compile(f"^{pattern}$"), []))
        else:
            name, value = line.strip().split(":", 1)
            rules[-1][1].append((name.strip(), value.strip()))
    return rules


def handler(auth: Auth, postgrest_port: int, api_schema: str):
    rules = read_headers(SITE / "_headers")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.route()

        def do_POST(self) -> None:
            self.route()

        def do_PATCH(self) -> None:
            self.route()

        def do_DELETE(self) -> None:
            self.route()

        def do_HEAD(self) -> None:
            self.route()

        def route(self) -> None:
            parts = urlsplit(self.path)
            if parts.path.startswith("/rest/v1/"):
                return self.proxy(parts.path.removeprefix("/rest/v1"), parts.query)
            if parts.path.startswith("/auth/v1/"):
                return self.auth(parts.path.removeprefix("/auth/v1"), parse_qs(parts.query))
            if parts.path == "/config.js":
                return self.config()
            return self.static(parts.path)

        def origin(self) -> str:
            return f"http://{self.headers['Host']}"

        def body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def send(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, status: int, payload) -> None:
            self.send(status, json.dumps(payload).encode(), {"Content-Type": "application/json"})

        def proxy(self, path: str, query: str) -> None:
            """As Supabase's gateway: an apikey is required; a bearer that is only the key
            means no user, so PostgREST runs the request as anon."""
            key = self.headers.get("apikey")
            if key != API_KEY:
                return self.json(401, {"message": "No API key found in request"})
            anonymous = self.headers.get("Authorization") == f"Bearer {API_KEY}"
            dropped = HOP_BY_HOP | {"host", "apikey", "accept-encoding"}
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in dropped and not (anonymous and k.lower() == "authorization")
            }
            upstream = HTTPConnection("127.0.0.1", postgrest_port, timeout=30)
            target = path + (f"?{query}" if query else "")
            upstream.request(self.command, target, self.body() or None, headers)
            answer = upstream.getresponse()
            payload = answer.read()
            kept = {k: v for k, v in answer.getheaders() if k.lower() not in HOP_BY_HOP}
            upstream.close()
            self.send(answer.status, payload, kept)

        def auth(self, path: str, query: dict) -> None:
            first = {k: v[0] for k, v in query.items()}
            if path == "/authorize":
                return self.authorize(first)
            if path == "/dev-login":
                return self.dev_login(first)
            if path == "/token" and self.command == "POST":
                body = json.loads(self.body() or b"{}")
                grant = first.get("grant_type")
                if grant == "pkce":
                    session = auth.exchange(
                        body.get("auth_code", ""), body.get("code_verifier", "")
                    )
                elif grant == "refresh_token":
                    session = auth.renew(body.get("refresh_token", ""))
                else:
                    session = None
                if session is None:
                    return self.json(
                        400, {"error": "invalid_grant", "msg": "The login code is not valid"}
                    )
                return self.json(200, session)
            if path == "/user":
                bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                claims = auth.tokens.verify(bearer)
                if claims is None:
                    return self.json(401, {"msg": "Not logged in"})
                return self.json(
                    200, auth.user(claims["email"], claims["app_metadata"]["provider"])
                )
            if path == "/logout":
                return self.send(204)
            return self.json(404, {"msg": f"The stand-in has no {path}"})

        def safe_return(self, target: str) -> bool:
            return target == self.origin() or target.startswith(self.origin() + "/")

        def authorize(self, query: dict) -> None:
            back = query.get("redirect_to", "")
            if not self.safe_return(back):
                return self.json(400, {"msg": "redirect_to must be this site"})
            provider = html.escape(query.get("provider", "github"))
            hidden = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in query.items()
            )
            page = f"""<!doctype html><meta charset="utf-8"><title>Local login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<body style="font: 16px system-ui; max-width: 26rem; margin: 15vh auto; padding: 0 16px">
<h1 style="font-size: 1.3rem">Local login standing in for {provider}</h1>
<p>tools/devsite.py logs in any email; nothing leaves this machine.</p>
<form action="/auth/v1/dev-login">{hidden}
<label>Email <input name="email" type="email" required value="you@example.org" autofocus
 style="font: inherit; width: 100%; padding: 6px; margin: 6px 0 12px"></label>
<button style="font: inherit; padding: 6px 14px">Log in</button></form>"""
            self.send(200, page.encode(), {"Content-Type": "text/html; charset=utf-8"})

        def dev_login(self, query: dict) -> None:
            back = query.get("redirect_to", "")
            if not self.safe_return(back) or not query.get("email"):
                return self.json(400, {"msg": "An email and this site's address are needed"})
            user = auth.user(query["email"], query.get("provider", "github"))
            code = auth.code(
                user, query.get("code_challenge", ""), query.get("code_challenge_method", "plain")
            )
            base, _, fragment = back.partition("#")
            joiner = "&" if "?" in base else "?"
            location = f"{base}{joiner}{urlencode({'code': code})}"
            self.send(302, headers={"Location": location + (f"#{fragment}" if fragment else "")})

        def config(self) -> None:
            text = (
                "/* Served by tools/devsite.py in place of site/config.js. */\n"
                f'export const SUPABASE_URL = "{self.origin()}";\n'
                f'export const SUPABASE_KEY = "{API_KEY}";\n'
                f'export const API_SCHEMA = "{api_schema}";\n'
                # No Google button locally: Google accepts only the origins its client lists.
                'export const GOOGLE_CLIENT_ID = "";\n'
            )
            self.send(200, text.encode(), {"Content-Type": "text/javascript", **self.policy("/")})

        def policy(self, path: str) -> dict:
            found: dict[str, str] = {}
            for pattern, headers in rules:
                if pattern.match(path):
                    found.update(headers)
            return found

        def static(self, path: str) -> None:
            file = (SITE / path.lstrip("/")).resolve()
            if file.is_dir():
                file = file / "index.html"
            elif not file.suffix and file.with_suffix(".html").is_file():
                file = file.with_suffix(".html")  # Pages serves /privacy from privacy.html
            if not file.is_relative_to(SITE) or not file.is_file() or file.name == "_headers":
                return self.send(404, b"Not found", {"Content-Type": "text/plain"})
            kind = TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0]
            headers = {"Content-Type": kind or "application/octet-stream", **self.policy(path)}
            headers.setdefault("Cache-Control", "no-cache")
            self.send(200, file.read_bytes(), headers)

    return Handler


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind a port already in use, and requests
    # then reach whichever one Windows picks; without it a second start fails loudly.
    allow_reuse_address = os.name != "nt"


def start_postgrest(binary: str, dsn: str, api: str, port: int, secret: str) -> subprocess.Popen:
    params = conninfo_to_dict(dsn)
    params.pop("password", None)  # authenticator logs in as the local server allows
    env = {
        "PGRST_DB_URI": make_conninfo(**{**params, "user": "authenticator"}),
        "PGRST_DB_SCHEMAS": api,
        "PGRST_DB_ANON_ROLE": "anon",
        # PostgREST 16.4 on Windows hung at startup in about 4 of 10 tries when its LISTEN
        # connection opened beside the pool's, and never in 12 without it (2026-10-12).
        # The schema is migrated before it starts, so no reload notice is needed.
        "PGRST_DB_CHANNEL_ENABLED": "false",
        "PGRST_JWT_SECRET": secret,
        "PGRST_SERVER_HOST": "127.0.0.1",
        "PGRST_SERVER_PORT": str(port),
    }
    inherited = {k: v for k, v in os.environ.items() if not k.startswith("PGRST_")}
    process = subprocess.Popen([binary], env={**inherited, **env})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2):
                return process
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.3)
    stop(process)
    raise SystemExit("PostgREST did not start; is `postgrest` installed (scoop install postgrest)?")


def stop(process: subprocess.Popen) -> None:
    """Scoop's `postgrest` is a shim that starts the real binary as its child, so on Windows
    the whole tree goes."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        process.terminate()
    process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database", default="postgresql://postgres@localhost/radar_dev")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--postgrest-port", type=int, default=3010)
    parser.add_argument("--postgrest", default=shutil.which("postgrest"))
    args = parser.parse_args()
    if conninfo_to_dict(args.database).get("host") not in LOCAL_HOSTS:
        raise SystemExit("Only a database on this machine; never the hosted one.")
    if not args.postgrest:
        raise SystemExit("PostgREST is not installed (scoop install postgrest).")
    Store(args.database).close()  # migrations and the api schema are current
    tokens = Tokens()
    # The port first, so a second start fails before it leaves a PostgREST behind.
    try:
        server = Server(
            ("127.0.0.1", args.port),
            handler(Auth(args.database, tokens), args.postgrest_port, "api"),
        )
    except OSError as exc:
        message = f"Port {args.port} is taken; is devsite already running? ({exc})"
        raise SystemExit(message) from None
    try:
        postgrest = start_postgrest(
            args.postgrest, args.database, "api", args.postgrest_port, tokens.secret
        )
    except BaseException:
        server.server_close()
        raise
    print(f"The radar runs on http://127.0.0.1:{args.port}/ (Ctrl+C stops it)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop(postgrest)


if __name__ == "__main__":
    main()
