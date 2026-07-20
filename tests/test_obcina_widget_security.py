"""Varnostni testi občinskega widgeta v strele2."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["STRELKO_OBCINA_WIDGET_SECRET"] = "test-widget-secret"
os.environ["JWT_SECRET"] = "test-widget-secret"
os.environ["OBCINA_WIDGET_LEGACY_OPEN"] = "0"
os.environ["STRELKO_INTERNAL_API_KEY"] = "internal-test-key"

from strele_archive.obcina_widget_auth import PREVIEW_SESSION_COOKIE, legacy_widget_open
from strele_archive.obcine_public_server import app

STORM_VENV_SITE = Path("/home/maximus/projects/StormAPI/.venv/lib/python3.10/site-packages")
if STORM_VENV_SITE.is_dir():
    sys.path.insert(0, str(STORM_VENV_SITE))

from jose import jwt
from datetime import datetime, timedelta, timezone

from strele_archive.obcina_widget_auth import PREVIEW_PURPOSE, validate_preview_token

SECRET = "test-widget-secret"


@pytest.fixture
def client():
    return TestClient(app)


def _preview_token(*, sid: str, ob_mid: int = 11026567, exp_minutes: int = 8) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": PREVIEW_PURPOSE,
        "sid": sid,
        "ob_mid": ob_mid,
        "theme": "dark",
        "size": "compact",
        "jti": "test-jti",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp_minutes)).timestamp()),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_legacy_obcina_widget_api_rejected(client):
    res = client.get("/api/obcina-widget", params={"ob_mid": 11026567})
    assert res.status_code == 403


def test_preview_api_requires_token(client):
    res = client.get("/api/obcina-widget/preview", params={"token": "not-valid-jwt"})
    assert res.status_code == 401


def test_preview_api_rejects_invalid_signature(client):
    token = _preview_token(sid="sess-1")
    bad = token[:-6] + "xxxxxx"
    res = client.get(
        "/api/obcina-widget/preview",
        params={"token": bad},
        cookies={PREVIEW_SESSION_COOKIE: "sess-1"},
    )
    assert res.status_code == 401


def test_preview_api_accepts_token_without_session_cookie(client, monkeypatch):
    token = _preview_token(sid="sess-1")

    def _fake_data(**_kwargs):
        return {
            "ob_mid": 11026567,
            "obcina": "Test",
            "period_days": 30,
            "period_total": 0,
            "last_strike_time": None,
            "bounds": None,
            "total_24h": 0,
            "strikes": [],
            "daily": [],
        }

    monkeypatch.setattr(
        "strele_archive.obcine_public_server._api_obcina_widget_data",
        _fake_data,
    )
    res = client.get("/api/obcina-widget/preview", params={"token": token})
    assert res.status_code == 200
    assert res.json()["preview"] is True
    assert res.json()["ob_mid"] == 11026567


def test_preview_api_rejects_expired_token(client):
    token = _preview_token(sid="sess-1", exp_minutes=-1)
    res = client.get(
        "/api/obcina-widget/preview",
        params={"token": token},
        cookies={PREVIEW_SESSION_COOKIE: "sess-1"},
    )
    assert res.status_code == 401


def test_preview_api_rejects_extra_ob_mid_param(client):
    token = _preview_token(sid="sess-1")
    res = client.get(
        "/api/obcina-widget/preview",
        params={"token": token, "ob_mid": 99999999},
        cookies={PREVIEW_SESSION_COOKIE: "sess-1"},
    )
    assert res.status_code == 400


def test_preview_html_csp_is_http_response_header(client):
    res = client.get("/public/obcina-preview.html")
    assert res.status_code == 200
    csp = res.headers.get("content-security-policy", "")
    assert "frame-ancestors" in csp.lower()
    assert "https://strelko.meteoinfo.si" in csp


def test_preview_html_has_no_meta_frame_ancestors(client):
    res = client.get("/public/obcina-preview.html")
    body = res.text.lower()
    assert "http-equiv" not in body or "frame-ancestors" not in body


def test_production_html_rejects_legacy_query_params():
    html = (os.path.join(os.path.dirname(__file__), "..", "web", "public", "obcina-widget.html"))
    with open(html, encoding="utf-8") as f:
        source = f.read()
    assert "legacyQueryBlocked" in source
    assert "Manjka ID widgeta" in source
    assert "obcina-widget/preview" in source
    assert "obcina-widgets/public" in source


def test_legacy_widget_open_default_open_when_env_missing(monkeypatch):
    monkeypatch.delenv("OBCINA_WIDGET_LEGACY_OPEN", raising=False)
    assert legacy_widget_open() is True


def test_legacy_widget_open_closed_when_zero(monkeypatch):
    monkeypatch.setenv("OBCINA_WIDGET_LEGACY_OPEN", "0")
    assert legacy_widget_open() is False


def test_validate_preview_token_purpose():
    token = _preview_token(sid="sess-1")
    config = validate_preview_token(token, "sess-1")
    assert config["ob_mid"] == 11026567
    assert config["theme"] == "dark"


def test_validate_preview_token_without_cookie():
    token = _preview_token(sid="sess-1")
    config = validate_preview_token(token, None)
    assert config["ob_mid"] == 11026567
    assert config["size"] == "compact"


def test_internal_endpoint_requires_key(client):
    res = client.get("/api/obcina-widget/internal", params={"ob_mid": 11026567})
    assert res.status_code == 403


def test_internal_endpoint_rejects_key_in_query(client):
    res = client.get(
        "/api/obcina-widget/internal",
        params={"ob_mid": 11026567, "internal_key": "internal-test-key"},
        headers={"X-Strelko-Internal-Key": "internal-test-key"},
    )
    assert res.status_code == 403


def test_internal_endpoint_accepts_header_key(client):
    res = client.get(
        "/api/obcina-widget/internal",
        params={"ob_mid": 11026567},
        headers={"X-Strelko-Internal-Key": "internal-test-key"},
    )
    assert res.status_code == 200
