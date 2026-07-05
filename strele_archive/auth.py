"""Preverjanje dostopa do zasebnih API končnih točk."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def get_private_api_key() -> str:
    return os.getenv("PRIVATE_API_KEY", "").strip()


def require_private_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Zahteva ujemajoč X-API-Key, če je PRIVATE_API_KEY nastavljen."""
    private_key = get_private_api_key()
    if not private_key:
        return
    if x_api_key != private_key:
        raise HTTPException(
            status_code=401,
            detail="Neveljaven ali manjkajoč API ključ (X-API-Key)",
        )
