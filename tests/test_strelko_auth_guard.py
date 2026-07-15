"""Regression guard: strelko_auth mora biti uvozljiv (brez razbite REQUIRE_STRELKO_LOGIN vrstice)."""
from __future__ import annotations

from strele_archive.strelko_auth import REQUIRE_STRELKO_LOGIN, auth_enabled, is_public_path


def test_require_strelko_login_name_and_type():
    assert isinstance(REQUIRE_STRELKO_LOGIN, bool)


def test_auth_enabled_returns_bool():
    assert isinstance(auth_enabled(), bool)


def test_public_path_helper():
    assert is_public_path("/public/map-embed.html")
