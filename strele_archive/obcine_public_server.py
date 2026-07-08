"""Javni proxy za občinske grafe — brez Strelko auth (začasno odprt dostop)."""

from __future__ import annotations

import os
import pathlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from shapely.geometry import Point, mapping

from strele_archive.obcine import load_obcine

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


def _storm_post(path: str, payload: dict) -> dict | list:
    url = f"{_STORM_API}{path}"
    try:
        resp = requests.post(url, json=payload, timeout=_STORM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"StormAPI: {exc}") from exc


def _fetch_hourly_24h(muni_code: str) -> tuple[int, int, list[dict]]:
    """Vrne (skupaj_24h, urni_podatki) za občino."""
    now = _utc_now()
    start = now - timedelta(hours=24)
    payload = _storm_get(
        "/strele/aggregates/series",
        {
            "bucket": "hour",
            "group_by": "municipality",
            "municipality_codes": muni_code,
            "time_from_utc": _iso_utc(start),
            "time_to_utc": _iso_utc(now),
        },
    )
    groups = payload.get("groups") or []
    points = groups[0].get("points", []) if groups else []
    by_hour: dict[str, int] = {}
    for pt in points:
        ts = str(pt.get("t", ""))
        if not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        local = dt.astimezone(_LJ_TZ)
        key = local.strftime("%Y-%m-%dT%H:00:00")
        by_hour[key] = by_hour.get(key, 0) + int(pt.get("count") or 0)

    hourly: list[dict] = []
    cursor = now.astimezone(_LJ_TZ).replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    for _ in range(24):
        key = cursor.strftime("%Y-%m-%dT%H:00:00")
        hourly.append({
            "ura": cursor.hour,
            "label": f"{cursor.hour:02d}:00",
            "stevilo": by_hour.get(key, 0),
            "t": key,
        })
        cursor += timedelta(hours=1)

    total = int(payload.get("total") or sum(h["stevilo"] for h in hourly))
    last_hour = hourly[-1]["stevilo"] if hourly else 0
    return total, last_hour, hourly


def _fetch_hourly_24h_multi(muni_codes: list[str]) -> tuple[int, int, list[dict]]:
    if len(muni_codes) == 1:
        return _fetch_hourly_24h(muni_codes[0])
    merged: list[dict] | None = None
    total = 0
    for code in muni_codes:
        part_total, _part_last, hourly = _fetch_hourly_24h(code)
        total += part_total
        if merged is None:
            merged = [dict(h) for h in hourly]
        else:
            for i, h in enumerate(hourly):
                merged[i]["stevilo"] += h["stevilo"]
    last_hour = merged[-1]["stevilo"] if merged else 0
    return total, last_hour, merged or []


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


@app.get("/api/obcina-widget")
def api_obcina_widget(
    ob_mid: int | None = Query(None, description="OB_MID identifikator občine"),
    ob_mids: str | None = Query(None, description="Več OB_MID, ločeno z vejico (največ 10)"),
    title: str | None = Query(None, max_length=80, description="Ime widgeta pri več občinah"),
    calm_days: int = Query(30, ge=7, le=90, alias="days"),
) -> dict:
    """Podatki za embed widget občine: urni profil + udari 24 h ob nevihti, sicer dnevna statistika."""
    mids = _parse_ob_mids(ob_mid, ob_mids)
    obs = _find_obcine(mids)
    muni_codes = [str(ob.id) for ob in obs]
    label = _widget_obcina_label(obs, title)
    bounds = _obcina_bounds_multi(obs)

    daily, period_total, peak = _fetch_daily_calm(mids, calm_days)
    last_strike_time = _fetch_last_strike_time_multi(obs, muni_codes, daily)

    try:
        total_24h, last_hour, hourly = _fetch_hourly_24h_multi(muni_codes)
    except HTTPException:
        total_24h, last_hour, hourly = 0, 0, []

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
    }

    if total_24h > 0:
        strikes = _fetch_strikes_24h_multi(obs)
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
    return FileResponse(str(path), media_type="text/html")


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
