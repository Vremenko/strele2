"""Testi predogleda: TTL cache, pariteta zadnje strele (geom predfilter)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["STRELKO_OBCINA_WIDGET_SECRET"] = "test-widget-secret"
os.environ["JWT_SECRET"] = "test-widget-secret"
os.environ["OBCINA_WIDGET_LEGACY_OPEN"] = "0"
os.environ["STRELKO_INTERNAL_API_KEY"] = "internal-test-key"

STORM_VENV_SITE = Path("/home/maximus/projects/StormAPI/.venv/lib/python3.10/site-packages")
if STORM_VENV_SITE.is_dir():
    sys.path.insert(0, str(STORM_VENV_SITE))

from jose import jwt

from strele_archive.obcina_widget_auth import PREVIEW_PURPOSE
from strele_archive import obcine_public_server as server

SECRET = "test-widget-secret"


@pytest.fixture
def client():
    server._preview_cache_clear_for_tests()
    return TestClient(server.app)


def _preview_token(
    *,
    sid: str = "sess-cache",
    ob_mid: int | None = 11026567,
    scope: str | None = None,
    theme: str = "dark",
    size: str = "compact",
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "purpose": PREVIEW_PURPOSE,
        "sid": sid,
        "theme": theme,
        "size": size,
        "jti": "test-jti-cache",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=8)).timestamp()),
    }
    if scope:
        payload["scope"] = scope
    elif ob_mid is not None:
        payload["ob_mid"] = ob_mid
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _fake_widget_payload(**overrides):
    base = {
        "ob_mid": 11026567,
        "ob_mids": [11026567],
        "obcina": "Test",
        "period_days": 30,
        "period_total": 12,
        "last_strike_time": "2026-07-18T13:26:00Z",
        "bounds": [[45.0, 14.0], [46.0, 15.0]],
        "updated_at": "2026-07-20T12:00:00Z",
        "data_source": "live",
        "mode": "calm",
        "muni_code": "1",
        "muni_codes": ["1"],
        "peak": None,
        "daily": [{"datum": "2026-07-01", "stevilo": 0}],
        "total_24h": 0,
        "strikes": [],
    }
    base.update(overrides)
    return base


def test_preview_cache_reuses_data_for_theme_and_size(client, monkeypatch):
    calls = {"n": 0}

    def _fake_data(**_kwargs):
        calls["n"] += 1
        return _fake_widget_payload()

    monkeypatch.setattr(server, "_api_obcina_widget_data", _fake_data)
    server._preview_cache_clear_for_tests()

    t1 = _preview_token(theme="dark", size="compact")
    r1 = client.get("/api/obcina-widget/preview", params={"token": t1})
    assert r1.status_code == 200
    assert r1.json()["theme"] == "dark"
    assert r1.json()["size"] == "compact"
    assert r1.json()["period_total"] == 12
    assert calls["n"] == 1

    t2 = _preview_token(theme="light", size="full")
    r2 = client.get("/api/obcina-widget/preview", params={"token": t2})
    assert r2.status_code == 200
    assert r2.json()["theme"] == "light"
    assert r2.json()["size"] == "full"
    assert r2.json()["period_total"] == 12
    assert r2.json()["last_strike_time"] == r1.json()["last_strike_time"]
    assert calls["n"] == 1  # cache hit — brez novega podatkovnega klica


def test_preview_cache_separate_per_municipality(client, monkeypatch):
    calls: list[int] = []

    def _fake_data(*, ob_mid=None, **_kwargs):
        calls.append(int(ob_mid))
        return _fake_widget_payload(ob_mid=ob_mid, ob_mids=[ob_mid], period_total=ob_mid)

    monkeypatch.setattr(server, "_api_obcina_widget_data", _fake_data)
    server._preview_cache_clear_for_tests()

    r1 = client.get(
        "/api/obcina-widget/preview",
        params={"token": _preview_token(ob_mid=11026516)},
    )
    r2 = client.get(
        "/api/obcina-widget/preview",
        params={"token": _preview_token(ob_mid=24063526)},
    )
    r3 = client.get(
        "/api/obcina-widget/preview",
        params={"token": _preview_token(ob_mid=11026516, theme="light")},
    )
    assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
    assert calls == [11026516, 24063526]
    assert r1.json()["period_total"] == 11026516
    assert r3.json()["period_total"] == 11026516
    assert r3.json()["theme"] == "light"


def test_preview_cache_key_ignores_theme_size():
    assert server._preview_data_cache_key(ob_mid=1, scope=None) == "ob:1"
    assert server._preview_data_cache_key(ob_mid=None, scope="slovenija") == "slovenija"


def test_last_strike_udari_query_uses_geom_envelope():
    source = Path(server.__file__).read_text(encoding="utf-8")
    fn_start = source.index("def _fetch_last_strike_time_from_udari_db")
    fn_end = source.index("\ndef _fetch_last_strike_time_from_24h_multi", fn_start)
    body = source[fn_start:fn_end]
    assert "ST_MakeEnvelope" in body
    assert "geom &&" in body
    assert "lat BETWEEN" not in body


def test_last_strike_geom_parity_with_latlon_filter():
    """Živa pariteta: isti last_strike_time kot stari lat/lon filter (če je DB na voljo)."""
    url = server._udari_database_url()
    if not url:
        pytest.skip("UDARI DB ni nastavljen")
    try:
        obs = server._find_obcine([11026516])
    except Exception as exc:
        pytest.skip(f"občine niso naložene: {exc}")

    lookback_days = int(os.getenv("WIDGET_LAST_STRIKE_DAYS", "365"))
    minx, miny, maxx, maxy = obs[0].geometry.bounds
    now = server._utc_now()
    start = now - timedelta(days=lookback_days)

    import psycopg
    from shapely.geometry import Point

    def latest(sql: str, args: tuple):
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall()
        for lat, lon, ts_raw in rows:
            if obs[0].prepared.contains(Point(lon, lat)):
                return server._iso_utc(server._parse_strike_ts(ts_raw))
        return None

    old_sql = """
        SELECT lat, lon, ts_utc FROM strele.udari
        WHERE ts_utc >= %s AND ts_utc <= %s
          AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s
        ORDER BY ts_utc DESC LIMIT 500
    """
    new_sql = """
        SELECT lat, lon, ts_utc FROM strele.udari
        WHERE geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
          AND ts_utc >= %s AND ts_utc <= %s
        ORDER BY ts_utc DESC LIMIT 500
    """
    old = latest(old_sql, (start, now, miny, maxy, minx, maxx))
    new = latest(new_sql, (minx, miny, maxx, maxy, start, now))
    current = server._fetch_last_strike_time_from_udari_db(obs)
    assert old == new == current
