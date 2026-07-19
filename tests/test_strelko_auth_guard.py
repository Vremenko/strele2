"""Regression guard: strelko_auth mora biti uvozljiv (brez razbite REQUIRE_STRELKO_LOGIN vrstice)."""
from __future__ import annotations

from datetime import date

from strele_archive.strelko_auth import (
    REQUIRE_STRELKO_LOGIN,
    auth_enabled,
    is_podpornik_active_credits,
    is_public_path,
)


def test_require_strelko_login_name_and_type():
    assert isinstance(REQUIRE_STRELKO_LOGIN, bool)


def test_auth_enabled_returns_bool():
    assert isinstance(auth_enabled(), bool)


def test_public_path_helper():
    assert is_public_path("/public/map-embed.html")
    assert is_public_path("/api/grid-map")


def test_podpornik_active_credits_helper():
    today = date(2026, 7, 19)
    assert is_podpornik_active_credits({"plan_id": "podpornik", "has_subscription": True}, today=today)
    assert not is_podpornik_active_credits({"plan_id": "ob_skodi", "has_subscription": False}, today=today)
