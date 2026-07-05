"""Dostop do /arhiv/ — samo prijavljeni uporabniki Strelko (dovoljeni e-naslovi)."""

from __future__ import annotations

import os
from urllib.parse import unquote

import requests
from fastapi import Request

REQUIRE_STRELKO_LOGIN = os.getenv("ARHIV_REQUIRE_STRELKO_LOGIN", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
ALLOWED_EMAILS = {
    email.strip().lower()
    for email in os.getenv("ARHIV_ALLOWED_EMAILS", "rok.nosan@meteoinfo.si").split(",")
    if email.strip()
}
STRELKO_API_URL = os.getenv("STRELKO_API_URL", "http://127.0.0.1:3000").rstrip("/")
COOKIE_NAME = "strelko_token"
LOGIN_URL = os.getenv("STRELKO_LOGIN_URL", "https://strelko.meteoinfo.si/").strip()


def auth_enabled() -> bool:
    if os.getenv("ARHIV_OPEN_ACCESS", "").strip().lower() in ("1", "true", "yes"):
        return False
    return REQUIRE_STRELKO_LOGIN and bool(ALLOWED_EMAILS)


_PUBLIC_API_PREFIXES = (
    "/api/health",
    "/api/archive-info",
    "/api/latest-date",
    "/api/si-daily",
    "/api/si-hourly",
    "/api/regije-daily",
    "/api/obcine-top",
    "/api/obcine-gostota",
    "/api/obcine-map",
    "/api/obcina-daily",
    "/api/obcina-by-coords",
    "/api/obcina-widget",
)


def is_public_path(path: str) -> bool:
    """Javni grafi in agregatni API — brez prijave Strelko."""
    if path.startswith("/static/"):
        return True
    if path in _PUBLIC_API_PREFIXES:
        return True
    if path == "/public" or path.startswith("/public/"):
        return True
    if path == "/embed" or path.startswith("/embed"):
        return True
    return False


def extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        token = unquote(raw).strip()
        return token or None
    return None


def verify_strelko_token(token: str) -> tuple[str | None, str | None]:
    """Vrne (email, napaka). napaka: unauthenticated | forbidden."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(
            f"{STRELKO_API_URL}/api/v1/auth/whoami",
            headers=headers,
            timeout=10,
        )
    except requests.RequestException:
        return None, "unauthenticated"

    if response.status_code == 401:
        return None, "unauthenticated"
    if response.status_code >= 400:
        return None, "unauthenticated"

    email = str(response.json().get("email", "")).strip().lower()
    if not email:
        return None, "unauthenticated"

    if email in ALLOWED_EMAILS:
        return email, None

    try:
        credits = requests.get(
            f"{STRELKO_API_URL}/api/v1/strelko/credits",
            headers=headers,
            timeout=10,
        )
        if credits.ok and credits.json().get("archive_full_access"):
            return email, None
    except requests.RequestException:
        pass

    return None, "forbidden"


def wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/api")


def gate_html(*, forbidden: bool = False) -> str:
    base = os.getenv("WEB_BASE_PATH", "/arhiv").rstrip("/") or ""
    title = "Dostop zavrnjen" if forbidden else "Prijava potrebna"
    lead = (
        "Ta arhiv je na voljo samo pooblaščenim uporabnikom."
        if forbidden
        else "Za ogled arhiva se prijavite na Strelko z dovoljenim računom."
    )
    return f"""<!DOCTYPE html>
<html lang="sl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Arhiv strel</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700&display=swap">
  <style>
    :root {{
      --mi-yellow: #fbb006;
      --mi-cyan: #05a5ce;
      --mi-gray-100: #f2f2f2;
      --mi-gray-500: #999999;
      --mi-gray-900: #333333;
      --mi-gray-950: #1a1a1a;
    }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: "Nunito Sans", system-ui, sans-serif;
      background: var(--mi-gray-950); color: var(--mi-gray-100); padding: 1.5rem;
    }}
    .card {{
      max-width: 28rem; background: var(--mi-gray-900); border: 1px solid #4d4d4d;
      border-radius: 12px; padding: 1.75rem; text-align: center;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.75rem; color: var(--mi-yellow); }}
    p {{ margin: 0 0 1rem; color: var(--mi-gray-500); line-height: 1.5; }}
    a.btn {{
      display: inline-block; padding: 0.65rem 1.2rem; border-radius: 8px;
      background: var(--mi-yellow); color: var(--mi-gray-950); text-decoration: none; font-weight: 700;
    }}
    a.btn:hover {{ background: var(--mi-cyan); color: #fff; }}
    #status {{ margin-top: 1rem; font-size: 0.9rem; color: var(--mi-gray-500); }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{lead}</p>
    {"<p>Če menite, da gre za napako, kontaktirajte skrbnika.</p>" if forbidden else ""}
    <a class="btn" href="{LOGIN_URL}">Odpri Strelko → prijava</a>
    <p id="status"></p>
  </div>
  <script>
    (function () {{
      const status = document.getElementById("status");
      const token = localStorage.getItem("strelko_token");
      if (!token) {{
        status.textContent = "Po prijavi se vrnite na to stran.";
        return;
      }}
      const secure = location.protocol === "https:" ? "; Secure" : "";
      document.cookie = "{COOKIE_NAME}=" + encodeURIComponent(token)
        + "; path=/; max-age=604800; SameSite=Lax" + secure;
      status.textContent = "Preverjam dostop …";
      location.reload();
    }})();
  </script>
</body>
</html>"""
