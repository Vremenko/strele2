"""Avtentikacija občinskega widgeta: preview žetoni in interni klici."""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

PREVIEW_PURPOSE = "preview"
NATIONAL_SCOPE = "slovenija"
PREVIEW_SESSION_COOKIE = "strelko_obcina_preview_sid"
INTERNAL_KEY_HEADER = "X-Strelko-Internal-Key"
JWT_ALG = "HS256"
_UNSAFE_SECRETS = frozenset({"", "CHANGE_ME_IN_ENV", "dev-only-change-me"})


def _is_production_env() -> bool:
    return os.getenv("ENV", "prod").strip().lower() in {"prod", "production"}


def _signing_secret() -> str:
    secret = (
        os.getenv("STRELKO_OBCINA_WIDGET_SECRET", "").strip()
        or os.getenv("JWT_SECRET", "").strip()
    )
    if not secret or secret in _UNSAFE_SECRETS:
        if _is_production_env():
            raise RuntimeError(
                "STRELKO_OBCINA_WIDGET_SECRET (ali JWT_SECRET) mora biti nastavljen v produkciji."
            )
        return secret or "dev-only-change-me"
    return secret


def _internal_api_key() -> str:
    key = os.getenv("STRELKO_INTERNAL_API_KEY", "").strip()
    if not key:
        if _is_production_env():
            raise RuntimeError("STRELKO_INTERNAL_API_KEY mora biti nastavljen v produkciji.")
        return ""
    return key


def legacy_widget_open() -> bool:
    """Privzeto odprto (varno za večdelni deploy); zapre se šele ob OBCINA_WIDGET_LEGACY_OPEN=0."""
    raw = os.getenv("OBCINA_WIDGET_LEGACY_OPEN", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def require_internal_key(request: Request) -> None:
    if request.query_params.get("internal_key") or request.query_params.get("key"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    expected = _internal_api_key()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Not configured")
    provided = request.headers.get(INTERNAL_KEY_HEADER, "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _signing_secret(), algorithms=[JWT_ALG])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Neveljaven žeton.") from exc


def validate_preview_token(token: str, session_id: str | None) -> dict[str, Any]:
    claims = decode_token(token)
    if claims.get("purpose") != PREVIEW_PURPOSE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Žeton ni namenjen predogledu.")
    sid = str(claims.get("sid") or "")
    if not sid or sid != (session_id or "").strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Predogledna seja ni veljavna.")
    scope = claims.get("scope")
    ob_mid = claims.get("ob_mid")
    if scope and scope != NATIONAL_SCOPE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Neveljaven scope.")
    if not scope and not ob_mid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Manjka ob_mid ali scope.")
    theme = (claims.get("theme") or "dark").strip().lower()
    size = (claims.get("size") or "compact").strip().lower()
    return {
        "scope": scope if scope == NATIONAL_SCOPE else None,
        "ob_mid": int(ob_mid) if ob_mid else None,
        "theme": theme if theme in {"dark", "light"} else "dark",
        "size": size if size in {"compact", "full"} else "compact",
    }
