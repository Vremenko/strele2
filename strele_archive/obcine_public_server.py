"""Javni proxy za občinske grafe — brez Strelko auth (začasno odprt dostop)."""

from __future__ import annotations

import os
import json
import pathlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from strele_archive.obcina_widget_auth import (
    PREVIEW_SESSION_COOKIE,
    legacy_widget_open,
    require_internal_key,
    validate_preview_token,
)
from shapely.geometry import Point, mapping
from shapely.ops import unary_union

from strele_archive.obcine import load_obcine
from strele_archive.obcina_widget_daily import (
    StormObcinaLiveStats,
    StormUnavailable,
    apply_live_daily_sync,
    daily_value_for_date,
    local_today,
    parse_storm_hourly_payload,
)
from strele_archive.regions import load_regions
from strele_archive.grid_map import (
    fetch_grid_cell_daily,
    fetch_grid_map_from_daily,
    resolve_grid_cell,
    today_cache_basename,
)

_GRID_CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache" / "grid-map"
_GRID_CACHE_DAYS = {7, 14, 30, 90}
_GRID_CELL_DAILY_MAX_DAYS = 90
_GRID_CACHE_VERSION = 4


def _grid_cache_path(days: int) -> pathlib.Path:
    return _GRID_CACHE_DIR / f"grid-map-{days}.json"


def _grid_today_cache_path() -> pathlib.Path:
    return _GRID_CACHE_DIR / today_cache_basename()


def _grid_cache_etag(p: pathlib.Path) -> str:
    st = p.stat()
    return f'W/"{st.st_mtime_ns}-{st.st_size}"'


def _grid_cache_is_valid(parsed: dict) -> bool:
    if int(parsed.get("cache_version") or 0) != _GRID_CACHE_VERSION:
        return False
    if "storm_radius_km" in parsed or "storm_days" in parsed:
        return False
    if not parsed.get("cached"):
        return False
    return True


def _accepts_gzip(request: Request) -> bool:
    accept = (request.headers.get("accept-encoding") or "").lower()
    return "gzip" in accept


def _grid_cache_response(request: Request, p: pathlib.Path) -> Response:
    if not p.exists():
        raise HTTPException(status_code=503, detail="Grid cache še ni pripravljen.")
    try:
        etag = _grid_cache_etag(p)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        body = p.read_bytes()
        try:
            parsed = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Grid cache je poškodovan.") from exc
        if not _grid_cache_is_valid(parsed):
            raise HTTPException(
                status_code=503,
                detail="Grid cache ni kompatibilen (potreben je nov backfill).",
            )
        headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=300",
            "X-Grid-Cache": "hit",
        }
        if _accepts_gzip(request):
            import gzip

            compressed = gzip.compress(body, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
            return Response(content=compressed, media_type="application/json", headers=headers)
        return Response(content=body, media_type="application/json", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Grid cache je poškodovan.") from exc
from strele_archive.si_widget_counts import (
    bucket_hourly_rolling_24h,
    dedup_pip_strikes,
    filter_pip_strikes,
    pip_strikes_to_map_records,
)

SI_WIDGET_MAP_MAX_STRIKES = 50_000

load_dotenv()

_LJ_TZ = ZoneInfo("Europe/Ljubljana")
_STORM_API = os.getenv("STORM_API_BASE_URL", "http://127.0.0.1:3000/api/v1").rstrip("/")
_STORM_TIMEOUT = float(os.getenv("STORM_API_TIMEOUT_SEC", "15"))

# Data directory relative to this file (../data/)
_DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
# Public web dir (../web/public/)
_WEB_PUBLIC_DIR = pathlib.Path(__file__).parent.parent / "web" / "public"


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL manjka")
    return url


def _udari_database_url() -> str | None:
    url = (
        os.getenv("UDARI_DATABASE_URL", "").strip()
        or os.getenv("STORM_DATABASE_URL", "").strip()
    )
    if not url:
        return None
    return url.replace("postgresql+psycopg://", "postgresql://")


def _date_range(
    from_: date | None,
    to_: date | None,
    day: date | None,
    days: int | None,
) -> tuple[date, date]:
    """Resolves (start, end) from the various accepted parameter combos."""
    if day is not None:
        return day, day
    if from_ is not None and to_ is not None:
        return from_, to_
    if days is not None:
        end = date.today()
        return end - timedelta(days=days - 1), end
    raise HTTPException(status_code=422, detail="Podaj day, days ali from+to")


def _date_bounds(day: date | None, days: int | None) -> tuple[date, date]:
    return _date_range(None, None, day, days)


app = FastAPI(title="Strele občine (javno)", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Lazy-loaded ObcinaIndex za GPS lookup (Shapely PiP)
_obcina_index = None
_region_index = None

def _get_region_index():
    global _region_index
    if _region_index is None:
        path = _DATA_DIR / "SR.geojson"
        if not path.exists():
            raise RuntimeError("GeoJSON za regije (Slovenija) ni najden")
        _region_index = load_regions(path)
    return _region_index

def _get_obcina_index():
    global _obcina_index
    if _obcina_index is None:
        for name in ("OB.geojson", "OB-lite.geojson"):
            path = _DATA_DIR / name
            if path.exists():
                _obcina_index = load_obcine(path)
                break
        if _obcina_index is None:
            raise RuntimeError("GeoJSON za občine ni najden")
    return _obcina_index


def _find_obcina(ob_mid: int):
    for ob in _get_obcina_index().obcine:
        if ob.ob_mid == ob_mid:
            return ob
    raise HTTPException(status_code=404, detail="Občina ne obstaja")


def _parse_ob_mids(ob_mid: int | None, ob_mids: str | None) -> list[int]:
    ids: list[int] = []
    if ob_mids:
        for part in ob_mids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                val = int(part)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Neveljaven ob_mids") from exc
            if val > 0 and val not in ids:
                ids.append(val)
    elif ob_mid is not None:
        ids = [ob_mid]
    else:
        raise HTTPException(status_code=422, detail="Podaj ob_mid ali ob_mids")
    if not ids:
        raise HTTPException(status_code=422, detail="Manjka identifikator občine")
    if len(ids) > 10:
        raise HTTPException(status_code=422, detail="Največ 10 občin")
    return ids


def _find_obcine(ob_mids: list[int]) -> list:
    obs = []
    for mid in ob_mids:
        obs.append(_find_obcina(mid))
    return obs


def _widget_obcina_label(obs: list, title: str | None) -> str:
    if title and title.strip():
        return title.strip()[:80]
    if len(obs) == 1:
        return obs[0].name
    return ", ".join(ob.name for ob in obs)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _storm_get(path: str, params: dict) -> dict | list:
    url = f"{_STORM_API}{path}"
    try:
        resp = requests.get(url, params=params, timeout=_STORM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"StormAPI: {exc}") from exc


def _storm_get_safe(path: str, params: dict) -> dict | list:
    """Kot _storm_get, a ob napaki vrne StormUnavailable (za widget fallback)."""
    url = f"{_STORM_API}{path}"
    try:
        resp = requests.get(url, params=params, timeout=_STORM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise StormUnavailable(str(exc)) from exc


def _storm_post(path: str, payload: dict) -> dict | list:
    url = f"{_STORM_API}{path}"
    try:
        resp = requests.post(url, json=payload, timeout=_STORM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"StormAPI: {exc}") from exc


def _fetch_obcina_live_stats(muni_code: str, *, now: datetime | None = None) -> StormObcinaLiveStats:
    """Rolling 24 h + današnji dan od lokalne polnoči (StormAPI udari_24h)."""
    now = now or _utc_now()
    start = now - timedelta(hours=24)
    payload = _storm_get_safe(
        "/strele/aggregates/series",
        {
            "bucket": "hour",
            "group_by": "municipality",
            "municipality_codes": muni_code,
            "time_from_utc": _iso_utc(start),
            "time_to_utc": _iso_utc(now),
        },
    )
    if not isinstance(payload, dict):
        raise StormUnavailable("Neveljaven StormAPI odgovor")
    return parse_storm_hourly_payload(payload, now_utc=now)


def _fetch_obcina_live_stats_multi(
    muni_codes: list[str],
    *,
    now: datetime | None = None,
) -> StormObcinaLiveStats:
    if len(muni_codes) == 1:
        return _fetch_obcina_live_stats(muni_codes[0], now=now)
    now = now or _utc_now()
    merged: list[dict] | None = None
    total = 0
    today_sum = 0
    for code in muni_codes:
        part = _fetch_obcina_live_stats(code, now=now)
        total += part.total_24h
        today_sum += part.today_from_midnight
        if merged is None:
            merged = [dict(h) for h in part.hourly]
        else:
            for i, h in enumerate(part.hourly):
                merged[i]["stevilo"] += h["stevilo"]
    last_hour = merged[-1]["stevilo"] if merged else 0
    return StormObcinaLiveStats(
        total_24h=total,
        last_hour=last_hour,
        hourly=merged or [],
        today_from_midnight=today_sum,
    )


def _fetch_hourly_24h(muni_code: str) -> tuple[int, int, list[dict]]:
    """Vrne (skupaj_24h, urni_podatki) za občino."""
    stats = _fetch_obcina_live_stats(muni_code)
    return stats.total_24h, stats.last_hour, stats.hourly


def _fetch_hourly_24h_multi(muni_codes: list[str]) -> tuple[int, int, list[dict]]:
    stats = _fetch_obcina_live_stats_multi(muni_codes)
    return stats.total_24h, stats.last_hour, stats.hourly


def _fetch_strikes_24h(ob) -> list[dict]:
    """Udari zadnjih 24 h znotraj občine (bbox + point-in-polygon)."""
    minx, miny, maxx, maxy = ob.geometry.bounds
    now = _utc_now()
    start = now - timedelta(hours=24)
    rows = _storm_get(
        "/strele",
        {
            "min_lat": miny,
            "max_lat": maxy,
            "min_lon": minx,
            "max_lon": maxx,
            "time_from_utc": _iso_utc(start),
            "time_to_utc": _iso_utc(now),
        },
    )
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        lat = float(row.get("lat", 0))
        lon = float(row.get("lon", 0))
        if not ob.prepared.contains(Point(lon, lat)):
            continue
        ts = row.get("ts_utc")
        out.append({"lat": lat, "lon": lon, "ts_utc": str(ts) if ts else None})
        if len(out) >= 500:
            break
    return out


def _fetch_strikes_24h_multi(obs: list) -> list[dict]:
    if len(obs) == 1:
        return _fetch_strikes_24h(obs[0])
    minx = min(o.geometry.bounds[0] for o in obs)
    miny = min(o.geometry.bounds[1] for o in obs)
    maxx = max(o.geometry.bounds[2] for o in obs)
    maxy = max(o.geometry.bounds[3] for o in obs)
    now = _utc_now()
    start = now - timedelta(hours=24)
    rows = _storm_get(
        "/strele",
        {
            "min_lat": miny,
            "max_lat": maxy,
            "min_lon": minx,
            "max_lon": maxx,
            "time_from_utc": _iso_utc(start),
            "time_to_utc": _iso_utc(now),
        },
    )
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        lat = float(row.get("lat", 0))
        lon = float(row.get("lon", 0))
        pt = Point(lon, lat)
        if not any(o.prepared.contains(pt) for o in obs):
            continue
        ts = row.get("ts_utc")
        out.append({"lat": lat, "lon": lon, "ts_utc": str(ts) if ts else None})
        if len(out) >= 500:
            break
    return out


def _last_strike_date_from_daily(daily: list[dict]) -> date | None:
    last: date | None = None
    for row in daily:
        stevilo = row.get("stevilo") or 0
        datum = row.get("datum")
        if stevilo < 1 or not datum:
            continue
        d = date.fromisoformat(str(datum)[:10])
        if last is None or d > last:
            last = d
    return last


def _parse_strike_ts(ts_raw) -> datetime | None:
    if not ts_raw:
        return None
    if isinstance(ts_raw, str):
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    else:
        ts = ts_raw
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _fetch_last_strike_time_from_udari_db(obs: list) -> str | None:
    """Natančen čas zadnjega udara iz strele.udari (minute)."""
    url = _udari_database_url()
    if not url:
        return None
    lookback_days = int(os.getenv("WIDGET_LAST_STRIKE_DAYS", "365"))
    minx = min(o.geometry.bounds[0] for o in obs)
    miny = min(o.geometry.bounds[1] for o in obs)
    maxx = max(o.geometry.bounds[2] for o in obs)
    maxy = max(o.geometry.bounds[3] for o in obs)
    now = _utc_now()
    start = now - timedelta(days=lookback_days)
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lat, lon, ts_utc
                    FROM strele.udari
                    WHERE ts_utc >= %s AND ts_utc <= %s
                      AND lat BETWEEN %s AND %s
                      AND lon BETWEEN %s AND %s
                    ORDER BY ts_utc DESC
                    LIMIT 500
                    """,
                    (start, now, miny, maxy, minx, maxx),
                )
                rows = cur.fetchall()
    except Exception:
        return None
    latest: datetime | None = None
    for lat, lon, ts_raw in rows:
        pt = Point(lon, lat)
        if not any(o.prepared.contains(pt) for o in obs):
            continue
        ts = _parse_strike_ts(ts_raw)
        if ts and (latest is None or ts > latest):
            latest = ts
    return _iso_utc(latest) if latest else None


def _fetch_last_strike_time_from_24h_multi(obs: list) -> str | None:
    """Najnovejši udar iz zadnjih 24 h (natančen čas)."""
    minx = min(o.geometry.bounds[0] for o in obs)
    miny = min(o.geometry.bounds[1] for o in obs)
    maxx = max(o.geometry.bounds[2] for o in obs)
    maxy = max(o.geometry.bounds[3] for o in obs)
    now = _utc_now()
    start = now - timedelta(hours=24)
    try:
        rows = _storm_get(
            "/strele",
            {
                "min_lat": miny,
                "max_lat": maxy,
                "min_lon": minx,
                "max_lon": maxx,
                "time_from_utc": _iso_utc(start),
                "time_to_utc": _iso_utc(now),
            },
        )
    except HTTPException:
        return None
    if not isinstance(rows, list):
        return None
    latest: datetime | None = None
    for row in rows:
        lat = float(row.get("lat", 0))
        lon = float(row.get("lon", 0))
        pt = Point(lon, lat)
        if not any(o.prepared.contains(pt) for o in obs):
            continue
        ts = _parse_strike_ts(row.get("ts_utc"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return _iso_utc(latest) if latest else None


def _fetch_last_strike_time_from_hourly_aggregate(
    muni_codes: list[str], daily: list[dict]
) -> str | None:
    """Približen čas zadnjega udara iz urne agregacije na zadnji dan s strelami."""
    last_day = _last_strike_date_from_daily(daily)
    if not last_day or not muni_codes:
        return None
    start_local = datetime.combine(last_day, datetime.min.time(), tzinfo=_LJ_TZ)
    end_local = start_local + timedelta(days=1)
    try:
        payload = _storm_get(
            "/strele/aggregates/series",
            {
                "bucket": "hour",
                "group_by": "municipality",
                "municipality_codes": ",".join(muni_codes),
                "time_from_utc": _iso_utc(start_local.astimezone(timezone.utc)),
                "time_to_utc": _iso_utc(end_local.astimezone(timezone.utc)),
            },
        )
    except HTTPException:
        return None
    latest: datetime | None = None
    for group in payload.get("groups") or []:
        for pt in group.get("points") or []:
            if int(pt.get("count") or 0) < 1:
                continue
            ts = _parse_strike_ts(pt.get("t"))
            if ts and (latest is None or ts > latest):
                latest = ts
    return _iso_utc(latest) if latest else None


def _fetch_last_strike_time_multi(
    obs: list, muni_codes: list[str], daily: list[dict]
) -> str | None:
    ts = _fetch_last_strike_time_from_udari_db(obs)
    if ts:
        return ts
    ts = _fetch_last_strike_time_from_24h_multi(obs)
    if ts:
        return ts
    return _fetch_last_strike_time_from_hourly_aggregate(muni_codes, daily)


def _fetch_daily_calm(ob_mids: list[int] | int, days: int = 30) -> tuple[list[dict], int, dict | None]:
    if isinstance(ob_mids, int):
        mids = [ob_mids]
    else:
        mids = ob_mids
    end = date.today()
    start = end - timedelta(days=days - 1)
    sql = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS datum
        )
        SELECT ds.datum, COALESCE(SUM(s.stevilo)::int, 0) AS stevilo
        FROM date_series ds
        LEFT JOIN obcine o ON o.ob_mid = ANY(%s)
        LEFT JOIN strele_obcina_dnevno s ON s.obcina_id = o.id AND s.datum = ds.datum
        GROUP BY ds.datum
        ORDER BY ds.datum
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, mids))
            rows = cur.fetchall()
    daily = [{"datum": str(r[0]), "stevilo": r[1]} for r in rows]
    total = sum(d["stevilo"] for d in daily)
    peak = max(daily, key=lambda d: d["stevilo"]) if daily else None
    peak_out = {"datum": peak["datum"], "stevilo": peak["stevilo"]} if peak and peak["stevilo"] > 0 else None
    return daily, total, peak_out


def _si_bounds() -> list[list[float]]:
    min_lon, min_lat, max_lon, max_lat = _get_region_index().bounds
    return [[min_lat, min_lon], [max_lat, max_lon]]


def _si_storm_bbox_params() -> dict[str, float]:
    min_lon, min_lat, max_lon, max_lat = _get_region_index().bounds
    return {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
    }


def _fetch_daily_calm_si(days: int = 30) -> tuple[list[dict], int, dict | None]:
    end = date.today()
    start = end - timedelta(days=days - 1)
    sql = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS datum
        )
        SELECT ds.datum, COALESCE(s.stevilo, 0)::int AS stevilo
        FROM date_series ds
        LEFT JOIN strele_si_dnevno s ON s.datum = ds.datum
        ORDER BY ds.datum
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            rows = cur.fetchall()
    daily = [{"datum": str(r[0]), "stevilo": r[1]} for r in rows]
    total = sum(d["stevilo"] for d in daily)
    peak = max(daily, key=lambda d: d["stevilo"]) if daily else None
    peak_out = {"datum": peak["datum"], "stevilo": peak["stevilo"]} if peak and peak["stevilo"] > 0 else None
    return daily, total, peak_out


def _fetch_raw_strikes_window_si(
    start: datetime,
    end: datetime,
    *,
    limit: int = 50_000,
) -> list[tuple[float, float, datetime]]:
    """Surovi udare v oknu (bbox predfilter); brez omejitve za prikaz na zemljevidu."""
    idx = _get_region_index()
    min_lon, min_lat, max_lon, max_lat = idx.bounds
    parsed: list[tuple[float, float, datetime]] = []

    url = _udari_database_url()
    if url:
        try:
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT lat, lon, ts_utc
                        FROM strele.udari_24h
                        WHERE ts_utc >= %s AND ts_utc <= %s
                          AND lat BETWEEN %s AND %s
                          AND lon BETWEEN %s AND %s
                        ORDER BY ts_utc ASC
                        LIMIT %s
                        """,
                        (start, end, min_lat, max_lat, min_lon, max_lon, limit),
                    )
                    rows = cur.fetchall()
            for lat_raw, lon_raw, ts_raw in rows:
                ts = _parse_strike_ts(ts_raw)
                if ts is None:
                    continue
                parsed.append((float(lat_raw), float(lon_raw), ts))
            if parsed:
                return parsed
        except Exception:
            pass

    rows = _storm_get(
        "/strele",
        {
            **_si_storm_bbox_params(),
            "time_from_utc": _iso_utc(start),
            "time_to_utc": _iso_utc(end),
        },
    )
    if isinstance(rows, list):
        for row in rows:
            ts = _parse_strike_ts(row.get("ts_utc"))
            if ts is None:
                continue
            parsed.append((float(row.get("lat", 0)), float(row.get("lon", 0)), ts))
    return parsed


def _si_rolling_24h_pip_tuples() -> list[tuple[float, float, datetime]]:
    """PiP udari v rolling 24 h oknu — skupen vir za total_24h in zemljevid."""
    now = _utc_now()
    start = now - timedelta(hours=24)
    idx = _get_region_index()
    raw = _fetch_raw_strikes_window_si(start, now)
    inside = filter_pip_strikes(raw, idx)
    return dedup_pip_strikes(inside)


def _fetch_hourly_24h_si() -> tuple[int, int, list[dict]]:
    """24h urni profil in total — PiP znotraj meje Slovenije (ne bbox)."""
    inside = _si_rolling_24h_pip_tuples()
    return bucket_hourly_rolling_24h(inside, now_utc=_utc_now(), tz_name="Europe/Ljubljana")


def _fetch_strikes_24h_si() -> tuple[list[dict], dict]:
    """Vsi PiP udari v rolling 24 h (enak nabor kot total_24h)."""
    inside = _si_rolling_24h_pip_tuples()
    return pip_strikes_to_map_records(
        inside,
        iso_utc=_iso_utc,
        max_strikes=SI_WIDGET_MAP_MAX_STRIKES,
    )


def _fetch_last_strike_time_si(daily: list[dict]) -> str | None:
    idx = _get_region_index()
    min_lon, min_lat, max_lon, max_lat = idx.bounds
    url = _udari_database_url()
    if url:
        lookback_days = int(os.getenv("WIDGET_LAST_STRIKE_DAYS", "365"))
        now = _utc_now()
        start = now - timedelta(days=lookback_days)
        try:
            with psycopg.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT lat, lon, ts_utc
                        FROM strele.udari
                        WHERE ts_utc >= %s AND ts_utc <= %s
                          AND lat BETWEEN %s AND %s
                          AND lon BETWEEN %s AND %s
                        ORDER BY ts_utc DESC
                        LIMIT 500
                        """,
                        (start, now, min_lat, max_lat, min_lon, max_lon),
                    )
                    rows = cur.fetchall()
        except Exception:
            rows = []
        latest: datetime | None = None
        for lat, lon, ts_raw in rows:
            if not idx.contains(lon, lat):
                continue
            ts = _parse_strike_ts(ts_raw)
            if ts and (latest is None or ts > latest):
                latest = ts
        if latest:
            return _iso_utc(latest)

    latest24: datetime | None = None
    strikes_24h, _ = _fetch_strikes_24h_si()
    for strike in strikes_24h:
        ts = _parse_strike_ts(strike.get("ts_utc"))
        if ts and (latest24 is None or ts > latest24):
            latest24 = ts
    if latest24:
        return _iso_utc(latest24)

    last_date = _last_strike_date_from_daily(daily)
    if last_date:
        return last_date.isoformat()
    return None


def _obcina_bounds(ob) -> list[list[float]]:
    minx, miny, maxx, maxy = ob.geometry.bounds
    return [[miny, minx], [maxy, maxx]]


def _obcina_bounds_multi(obs: list) -> list[list[float]]:
    if len(obs) == 1:
        return _obcina_bounds(obs[0])
    minx = min(o.geometry.bounds[0] for o in obs)
    miny = min(o.geometry.bounds[1] for o in obs)
    maxx = max(o.geometry.bounds[2] for o in obs)
    maxy = max(o.geometry.bounds[3] for o in obs)
    return [[miny, minx], [maxy, maxx]]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "obcine-public"}


@app.get("/api/obcine-top")
def api_obcine_top(
    day: date | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    start, end = _date_bounds(day, days)
    sql = """
        SELECT o.ime_sl AS obcina, SUM(s.stevilo)::int AS stevilo
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum BETWEEN %s AND %s
        GROUP BY o.id, o.ime_sl
        ORDER BY stevilo DESC
        LIMIT %s
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, limit))
            rows = cur.fetchall()
    return [{"obcina": r[0], "stevilo": r[1]} for r in rows]


@app.get("/api/obcine-gostota")
def api_obcine_gostota(
    day: date | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
) -> list[dict]:
    start, end = _date_bounds(day, days)
    sql = """
        SELECT
            o.ime_sl AS obcina,
            SUM(s.stevilo)::float / NULLIF(o.pov_km2, 0) AS gostota,
            SUM(s.stevilo)::int AS stevilo
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum BETWEEN %s AND %s
        GROUP BY o.id, o.ime_sl, o.pov_km2
        ORDER BY gostota DESC NULLS LAST
        LIMIT %s
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, limit))
            rows = cur.fetchall()
    return [{"obcina": r[0], "gostota": float(r[1] or 0), "stevilo": r[2]} for r in rows]


@app.get("/api/obcine-map")
def api_obcine_map(
    from_: date | None = Query(None, alias="from"),
    to_: date | None = Query(None, alias="to"),
    day: date | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
) -> list[dict]:
    """Vse občine z OB_ID za choropleth – vrne stevilo za vsako občino."""
    start, end = _date_range(from_, to_, day, days)
    sql = """
        SELECT
            o.ob_mid,
            o.ime_sl AS obcina,
            COALESCE(o.pov_km2, 0) AS pov_km2,
            COALESCE(SUM(s.stevilo)::int, 0) AS stevilo,
            COALESCE(
                COUNT(DISTINCT s.datum) FILTER (WHERE s.stevilo > 0)::int,
                0
            ) AS dni_z_nevihto
        FROM obcine o
        LEFT JOIN strele_obcina_dnevno s
            ON s.obcina_id = o.id AND s.datum BETWEEN %s AND %s
        GROUP BY o.ob_mid, o.ime_sl, o.pov_km2
        ORDER BY o.ob_mid
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            rows = cur.fetchall()
    return [
        {
            "ob_id": r[0],
            "obcina": r[1],
            "pov_km2": float(r[2] or 0),
            "stevilo": r[3],
            "dni_z_nevihto": r[4],
        }
        for r in rows
    ]


@app.get("/api/grid-map", response_model=None)
def api_grid_map(
    request: Request,
    from_: date | None = Query(None, alias="from"),
    to_: date | None = Query(None, alias="to"),
    day: date | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    today: bool = Query(False, description="Današnji lokalni dan (cache grid-map-today.json)"),
    min_lon: float | None = Query(None, ge=12.0, le=17.5),
    min_lat: float | None = Query(None, ge=44.0, le=48.0),
    max_lon: float | None = Query(None, ge=12.0, le=17.5),
    max_lat: float | None = Query(None, ge=44.0, le=48.0),
) -> dict | Response:
    """Mreža 1 × 1 km — GeoJSON celic (gostota iz dnevne agregatne tabele ali cache)."""
    today_lj = local_today()

    if None not in (min_lon, min_lat, max_lon, max_lat):
        if min_lon >= max_lon or min_lat >= max_lat:
            raise HTTPException(status_code=422, detail="Neveljaven bbox")
        raise HTTPException(status_code=422, detail="Viewport filter za mrežo ni podprt.")

    # Današnji dan — samo predpripravljen cache.
    if today or (day is not None and day == today_lj) or (
        from_ is not None
        and to_ is not None
        and from_ == to_ == today_lj
        and day is None
        and days is None
    ):
        return _grid_cache_response(request, _grid_today_cache_path())

    if from_ is None and to_ is None and day is None and days in _GRID_CACHE_DAYS:
        end = today_lj
        start = end - timedelta(days=int(days) - 1)
    else:
        start, end = _date_range(from_, to_, day, days)

    # Cached rolling periods (7/14/30/90), brez viewporta.
    if (
        days in _GRID_CACHE_DAYS
        and day is None
        and from_ is None
        and to_ is None
        and not today
        and start == (end - timedelta(days=int(days) - 1))
        and end == today_lj
    ):
        return _grid_cache_response(request, _grid_cache_path(int(days)))

    udari_url = _udari_database_url()
    if not udari_url:
        raise HTTPException(status_code=503, detail="Vir udarov (UDARI_DATABASE_URL) ni na voljo")

    try:
        with psycopg.connect(udari_url) as conn:
            out = fetch_grid_map_from_daily(conn, start=start, end=end)
        out.update(
            {
                "cached": False,
                "generated_at": datetime.now(tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "from": start.isoformat(),
                "to": end.isoformat(),
                "cache_version": _GRID_CACHE_VERSION,
            }
        )
        return out
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Napaka pri branju mreže: {exc}") from exc


@app.get("/api/grid-cell-daily")
def api_grid_cell_daily(
    lat: float = Query(..., ge=44.0, le=48.0),
    lon: float = Query(..., ge=12.0, le=17.5),
    from_: date = Query(..., alias="from"),
    to_: date = Query(..., alias="to"),
) -> dict:
    """Izbrana celica 1 × 1 km + dnevni potek strel v radijih 5, 10 in 15 km."""
    if from_ > to_:
        raise HTTPException(status_code=422, detail="Neveljavno obdobje")
    period_days = (to_ - from_).days + 1
    if period_days > _GRID_CELL_DAILY_MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Obdobje je predolgo (največ {_GRID_CELL_DAILY_MAX_DAYS} dni)",
        )
    udari_url = _udari_database_url()
    if not udari_url:
        raise HTTPException(status_code=503, detail="Vir udarov (UDARI_DATABASE_URL) ni na voljo")
    try:
        with psycopg.connect(udari_url) as conn:
            out = fetch_grid_cell_daily(
                conn,
                lat=lat,
                lon=lon,
                start=from_,
                end=to_,
                data_dir=_DATA_DIR,
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Napaka pri branju celice: {exc}") from exc
    if out is None:
        raise HTTPException(status_code=404, detail="Celica ni znotraj Slovenije")
    return out


@app.get("/api/obcina-daily")
def api_obcina_daily(
    ob_mid: int = Query(..., description="OB_MID identifikator občine"),
    from_: date | None = Query(None, alias="from"),
    to_: date | None = Query(None, alias="to"),
    days: int | None = Query(None, ge=1, le=365),
) -> list[dict]:
    """Dnevni potek strel za posamezno občino (za dnevni graf)."""
    if from_ is not None and to_ is not None:
        start, end = from_, to_
    elif days is not None:
        end = date.today()
        start = end - timedelta(days=days - 1)
    else:
        raise HTTPException(status_code=422, detail="Podaj days ali from+to")
    sql = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS datum
        )
        SELECT
            ds.datum,
            COALESCE(SUM(s.stevilo)::int, 0) AS stevilo
        FROM date_series ds
        LEFT JOIN obcine o ON o.ob_mid = %s
        LEFT JOIN strele_obcina_dnevno s ON s.obcina_id = o.id AND s.datum = ds.datum
        GROUP BY ds.datum
        ORDER BY ds.datum
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, ob_mid))
            rows = cur.fetchall()
    return [{"datum": str(r[0]), "stevilo": r[1]} for r in rows]


@app.get("/api/obcina-by-coords")
def api_obcina_by_coords(
    lat: float = Query(..., ge=44.0, le=48.0),
    lon: float = Query(..., ge=12.0, le=17.5),
) -> dict:
    """Vrne ob_mid in ime občine za podane GPS koordinate."""
    try:
        idx = _get_obcina_index()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    obcina_id = idx.lookup(lon, lat)
    if obcina_id is None:
        raise HTTPException(status_code=404, detail="Koordinate niso znotraj nobene občine")
    for ob in idx.obcine:
        if ob.id == obcina_id:
            return {"ob_mid": ob.ob_mid, "name": ob.name}
    raise HTTPException(status_code=404, detail="Občina ne obstaja")


@app.get("/api/obcina-geometry")
def api_obcina_geometry(
    ob_mid: int = Query(..., description="OB_MID identifikator občine"),
) -> dict:
    """GeoJSON geometrija občine za prikaz na zemljevidu."""
    ob = _find_obcina(ob_mid)
    return {
        "type": "Feature",
        "properties": {"ob_mid": ob.ob_mid, "name": ob.name},
        "geometry": mapping(ob.geometry),
    }


@app.get("/api/si-daily")
def api_si_daily(
    from_: date | None = Query(None, alias="from"),
    to_: date | None = Query(None, alias="to"),
    days: int | None = Query(None, ge=1, le=365),
) -> list[dict]:
    """Dnevni potek strel za celotno Slovenijo."""
    if from_ is not None and to_ is not None:
        start, end = from_, to_
    elif days is not None:
        end = date.today()
        start = end - timedelta(days=days - 1)
    else:
        raise HTTPException(status_code=422, detail="Podaj days ali from+to")
    sql = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS datum
        )
        SELECT ds.datum, COALESCE(s.stevilo, 0)::int AS stevilo
        FROM date_series ds
        LEFT JOIN strele_si_dnevno s ON s.datum = ds.datum
        ORDER BY ds.datum
    """
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            rows = cur.fetchall()
    return [{"datum": str(r[0]), "stevilo": r[1]} for r in rows]


@app.get("/api/si-geometry")
def api_si_geometry() -> dict:
    """Zunanja obroba Slovenije (brez notranjih regijskih meja)."""
    idx = _get_region_index()
    outline = unary_union([region.geometry for region in idx.regions])
    return {
        "type": "Feature",
        "properties": {"name": "SLOVENIJA"},
        "geometry": mapping(outline),
    }


@app.get("/api/si-widget")
def api_si_widget(
    request: Request,
    calm_days: int = Query(30, ge=7, le=90, alias="days"),
) -> dict:
    """Podatki za embed widget celotne Slovenije."""
    if not legacy_widget_open():
        raise HTTPException(status_code=403, detail="Dostop zavrnjen. Uporabite veljaven widget ključ.")
    return _api_si_widget_data(calm_days)


def _api_si_widget_data(calm_days: int) -> dict:
    daily, period_total, peak = _fetch_daily_calm_si(calm_days)
    last_strike_time = _fetch_last_strike_time_si(daily)
    bounds = _si_bounds()

    try:
        inside = _si_rolling_24h_pip_tuples()
        now = _utc_now()
        total_24h, last_hour, hourly = bucket_hourly_rolling_24h(
            inside, now_utc=now, tz_name="Europe/Ljubljana"
        )
        strikes, map_meta = pip_strikes_to_map_records(
            inside,
            iso_utc=_iso_utc,
            max_strikes=SI_WIDGET_MAP_MAX_STRIKES,
        )
    except HTTPException:
        total_24h, last_hour, hourly = 0, 0, []
        strikes, map_meta = [], {"map_complete": True, "map_total_pip": 0}

    base = {
        "scope": "slovenija",
        "obcina": "SLOVENIJA",
        "period_days": calm_days,
        "period_total": period_total,
        "last_strike_time": last_strike_time,
        "bounds": bounds,
        "updated_at": _iso_utc(_utc_now()),
    }

    if total_24h > 0:
        return {
            **base,
            "mode": "storm",
            "total_24h": total_24h,
            "last_hour": last_hour,
            "hourly": hourly,
            "strikes": strikes,
            "strike_count": len(strikes),
            "map_complete": map_meta.get("map_complete", True),
            "map_total_pip": map_meta.get("map_total_pip", len(strikes)),
            "map_message": map_meta.get("map_message"),
            "peak": peak,
            "daily": daily,
        }

    return {
        **base,
        "mode": "calm",
        "peak": peak,
        "daily": daily,
        "total_24h": 0,
        "strikes": [],
    }


@app.get("/api/si-widget/internal")
def api_si_widget_internal(
    request: Request,
    calm_days: int = Query(30, ge=7, le=90, alias="days"),
) -> dict:
    require_internal_key(request)
    return _api_si_widget_data(calm_days)


@app.get("/api/obcina-widget")
def api_obcina_widget(
    request: Request,
    ob_mid: int | None = Query(None, description="OB_MID identifikator občine"),
    ob_mids: str | None = Query(None, description="Več OB_MID, ločeno z vejico (največ 10)"),
    title: str | None = Query(None, max_length=80, description="Ime widgeta pri več občinah"),
    calm_days: int = Query(30, ge=7, le=90, alias="days"),
) -> dict:
    """Podatki za embed widget občine — legacy javni dostop (zaprt, razen če OBCINA_WIDGET_LEGACY_OPEN=1)."""
    if not legacy_widget_open():
        raise HTTPException(status_code=403, detail="Dostop zavrnjen. Uporabite veljaven widget ključ.")
    return _api_obcina_widget_data(
        ob_mid=ob_mid,
        ob_mids=ob_mids,
        title=title,
        calm_days=calm_days,
    )


def _api_obcina_widget_data(
    *,
    ob_mid: int | None = None,
    ob_mids: str | None = None,
    title: str | None = None,
    calm_days: int = 30,
) -> dict:
    mids = _parse_ob_mids(ob_mid, ob_mids)
    obs = _find_obcine(mids)
    muni_codes = [str(ob.id) for ob in obs]
    label = _widget_obcina_label(obs, title)
    bounds = _obcina_bounds_multi(obs)

    daily, _archive_period_total, peak = _fetch_daily_calm(mids, calm_days)
    today_lj = local_today()
    archive_today = daily_value_for_date(daily, today_lj)
    last_strike_time = _fetch_last_strike_time_multi(obs, muni_codes, daily)

    data_source = "archive"
    total_24h = 0
    last_hour = 0
    hourly: list[dict] = []
    today_live: int | None = None

    try:
        live = _fetch_obcina_live_stats_multi(muni_codes)
        data_source = "live"
        total_24h = live.total_24h
        last_hour = live.last_hour
        hourly = live.hourly
        today_live = live.today_from_midnight
    except StormUnavailable:
        data_source = "archive_fallback"
        total_24h = archive_today
        today_live = archive_today

    daily, period_total, peak = apply_live_daily_sync(
        daily,
        data_source=data_source,
        today_live=today_live,
        today=today_lj,
    )

    storm_active = total_24h > 0

    base = {
        "ob_mid": obs[0].ob_mid,
        "ob_mids": [ob.ob_mid for ob in obs],
        "obcina": label,
        "title": title.strip()[:80] if title and title.strip() else None,
        "period_days": calm_days,
        "period_total": period_total,
        "last_strike_time": last_strike_time,
        "bounds": bounds,
        "updated_at": _iso_utc(_utc_now()),
        "data_source": data_source,
    }

    if storm_active:
        try:
            strikes = _fetch_strikes_24h_multi(obs)
        except HTTPException:
            strikes = []
        return {
            **base,
            "mode": "storm",
            "muni_code": muni_codes[0],
            "muni_codes": muni_codes,
            "total_24h": total_24h,
            "last_hour": last_hour,
            "hourly": hourly,
            "strikes": strikes,
            "strike_count": len(strikes),
            "peak": peak,
            "daily": daily,
        }

    return {
        **base,
        "mode": "calm",
        "muni_code": muni_codes[0],
        "muni_codes": muni_codes,
        "peak": peak,
        "daily": daily,
        "total_24h": 0,
        "strikes": [],
    }


@app.get("/api/obcina-widget/internal")
def api_obcina_widget_internal(
    request: Request,
    ob_mid: int | None = Query(None),
    ob_mids: str | None = Query(None),
    title: str | None = Query(None, max_length=80),
    calm_days: int = Query(30, ge=7, le=90, alias="days"),
) -> dict:
    require_internal_key(request)
    return _api_obcina_widget_data(
        ob_mid=ob_mid,
        ob_mids=ob_mids,
        title=title,
        calm_days=calm_days,
    )


@app.get("/api/obcina-widget/preview")
def api_obcina_widget_preview(
    request: Request,
    token: str = Query(..., min_length=10),
) -> dict:
    if request.query_params.get("ob_mid") or request.query_params.get("scope"):
        raise HTTPException(status_code=400, detail="Dodatni parametri niso dovoljeni.")
    session_id = request.cookies.get(PREVIEW_SESSION_COOKIE)
    config = validate_preview_token(token, session_id)
    if config["scope"]:
        data = _api_si_widget_data(30)
    else:
        data = _api_obcina_widget_data(ob_mid=config["ob_mid"], calm_days=30)
    return {
        **data,
        "preview": True,
        "theme": config["theme"],
        "size": config["size"],
        "scope": config["scope"],
        "ob_mid": config["ob_mid"],
    }


@app.get("/public/assets/{filename:path}")
def serve_public_asset(filename: str) -> FileResponse:
    allowed = {
        "strelko-logo-mark.png",
        "strelko-logo.png",
        "meteoinfo-logo.png",
    }
    safe = filename.strip("/")
    if safe not in allowed:
        raise HTTPException(status_code=404, detail="Datoteka ni dostopna")
    path = _WEB_PUBLIC_DIR / "assets" / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Datoteka ne obstaja")
    return FileResponse(str(path), media_type="image/png")


@app.get("/public/obcina-widget.html")
def serve_obcina_widget() -> FileResponse:
    path = _WEB_PUBLIC_DIR / "obcina-widget.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="obcina-widget.html ne obstaja")
    return FileResponse(
        str(path),
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/public/obcina-preview.html")
def serve_obcina_preview() -> FileResponse:
    path = _WEB_PUBLIC_DIR / "obcina-preview.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="obcina-preview.html ne obstaja")
    frame_ancestors = os.getenv("STRELKO_FRAME_ANCESTORS", "https://strelko.meteoinfo.si").strip()
    return FileResponse(
        str(path),
        media_type="text/html",
        headers={"Content-Security-Policy": f"frame-ancestors {frame_ancestors}"},
    )


@app.get("/public/data/{filename:path}")
def serve_data_file(filename: str) -> FileResponse:
    """Servira statične podatkovne datoteke (GeoJSON ipd.) brez avtentikacije."""
    safe = filename.strip("/")
    # Allow only known data files for safety
    allowed = {"OB-lite.geojson", "OB.geojson", "SR.geojson", "meje_drzav.geojson", "meje_drzav_brez_si.geojson"}
    if safe not in allowed:
        raise HTTPException(status_code=404, detail="Datoteka ni dostopna")
    path = _DATA_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Datoteka ne obstaja")
    return FileResponse(
        str(path),
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/public/map-embed.html")
def serve_map_embed() -> FileResponse:
    path = _WEB_PUBLIC_DIR / "map-embed.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="map-embed.html ne obstaja")
    return FileResponse(
        str(path),
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


def main() -> None:
    port = int(os.getenv("OBCINE_PUBLIC_PORT", os.getenv("SERVER_PORT", "8083")))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
