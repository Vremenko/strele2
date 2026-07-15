"""Agregacija udarov strel v mrežo 1 × 1 km (EPSG:3794) za zemljevid statistike."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from strele_archive.regions import RegionIndex, load_regions

_LJ_TZ = ZoneInfo("Europe/Ljubljana")
_GRID_SIZE_M = 1000
_GRID_CRS = 3794

# Daily aggregates table lives in the same PostGIS DB as raw strikes.
_DAILY_TABLE = "lightning_grid_1km_daily"

# Cache format version: 4 = density-only (no storm_days / storm radius).
_CACHE_VERSION = 4

_TODAY_CACHE_BASENAME = "grid-map-today.json"

_GRID_AGG_SQL = """
WITH slo AS (
    SELECT ST_SetSRID(ST_GeomFromText(%(slo_wkt)s), 4326) AS geom
),
bounds AS (
    SELECT
        ST_XMin(geom) AS min_lon,
        ST_YMin(geom) AS min_lat,
        ST_XMax(geom) AS max_lon,
        ST_YMax(geom) AS max_lat,
        geom
    FROM slo
),
strikes AS (
    SELECT u.geom, (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS strike_day
    FROM strele.udari u
    CROSS JOIN slo
    CROSS JOIN bounds b
    WHERE u.ts_utc >= %(t0)s
      AND u.ts_utc < %(t1)s
      AND u.geom && ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
      AND ST_Intersects(u.geom, slo.geom)
    %(extra_union)s
),
cells AS (
    SELECT
        ST_MakeEnvelope(
            ST_X(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s)),
            ST_Y(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s)),
            ST_X(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s))
                + %(grid_size)s,
            ST_Y(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s))
                + %(grid_size)s,
            %(grid_crs)s
        ) AS cell_geom,
        strike_day
    FROM strikes
),
agg AS (
    SELECT
        cell_geom,
        COUNT(*)::int AS strike_count,
        ROUND(ST_XMin(cell_geom))::bigint AS grid_x,
        ROUND(ST_YMin(cell_geom))::bigint AS grid_y
    FROM cells
    GROUP BY cell_geom
)
SELECT
    grid_x,
    grid_y,
    strike_count,
    ST_AsGeoJSON(ST_Transform(cell_geom, 4326)) AS geometry_json
FROM agg
%(viewport_filter)s
ORDER BY grid_y, grid_x
"""

_EXTRA_UNION_TODAY = """
    UNION ALL
    SELECT u.geom, (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS strike_day
    FROM strele.udari_24h u
    CROSS JOIN slo
    CROSS JOIN bounds b
    WHERE u.geom && ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
      AND ST_Intersects(u.geom, slo.geom)
      AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date >= %(today)s
      AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date < %(tomorrow)s
      AND NOT EXISTS (
        SELECT 1
        FROM strele.udari x
        WHERE x.ts_utc = u.ts_utc
          AND ST_Equals(x.geom, u.geom)
      )
"""

_VIEWPORT_FILTER = """
WHERE ST_Intersects(
    cell_geom,
    ST_Transform(
        ST_MakeEnvelope(%(vp_min_lon)s, %(vp_min_lat)s, %(vp_max_lon)s, %(vp_max_lat)s, 4326),
        %(grid_crs)s
    )
)
"""

GRID_CELL_DAILY_RADIUS_KM: int = 10

_CELL_RESOLVE_SQL = """
WITH slo AS (
    SELECT ST_SetSRID(ST_GeomFromText(%(slo_wkt)s), 4326) AS geom
),
snapped AS (
    SELECT
        (ST_X(ST_SnapToGrid(
            ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(grid_crs)s),
            %(grid_size)s, %(grid_size)s
        )))::bigint AS grid_x,
        (ST_Y(ST_SnapToGrid(
            ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(grid_crs)s),
            %(grid_size)s, %(grid_size)s
        )))::bigint AS grid_y
),
cell AS (
    SELECT
        s.grid_x,
        s.grid_y,
        ST_MakeEnvelope(
            s.grid_x, s.grid_y,
            s.grid_x + %(grid_size)s, s.grid_y + %(grid_size)s,
            %(grid_crs)s
        ) AS cell_geom
    FROM snapped s
)
SELECT
    c.grid_x,
    c.grid_y,
    ST_AsGeoJSON(ST_Transform(c.cell_geom, 4326))::text AS geometry_json,
    ST_Y(ST_Transform(ST_Centroid(c.cell_geom), 4326)) AS center_lat,
    ST_X(ST_Transform(ST_Centroid(c.cell_geom), 4326)) AS center_lon,
    EXISTS (
        SELECT 1
        FROM slo
        WHERE ST_Intersects(ST_Transform(c.cell_geom, 4326), slo.geom)
    ) AS in_slovenia
FROM cell c
"""

_RADIUS_DAILY_SQL = """
WITH slo AS (
    SELECT ST_SetSRID(ST_GeomFromText(%(slo_wkt)s), 4326) AS geom
),
center AS (
    SELECT ST_SetSRID(ST_MakePoint(%(center_lon)s, %(center_lat)s), 4326) AS geom
),
date_series AS (
    SELECT generate_series(%(start)s::date, %(end)s::date, interval '1 day')::date AS datum
),
strikes AS (
    SELECT (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS strike_day
    FROM strele.udari u
    CROSS JOIN slo
    CROSS JOIN center c
    WHERE u.ts_utc >= %(t0)s
      AND u.ts_utc < %(t1)s
      AND u.geom && ST_Envelope(ST_Buffer(c.geom::geography, %(radius_m)s::double precision)::geometry)
      AND ST_Intersects(u.geom, slo.geom)
      AND ST_DWithin(u.geom::geography, c.geom::geography, %(radius_m)s)
    %(extra_union_today)s
),
daily AS (
    SELECT strike_day AS datum, COUNT(*)::int AS stevilo
    FROM strikes
    GROUP BY strike_day
)
SELECT ds.datum, COALESCE(d.stevilo, 0)::int AS stevilo
FROM date_series ds
LEFT JOIN daily d ON d.datum = ds.datum
ORDER BY ds.datum
"""

_EXTRA_UNION_TODAY_RADIUS = """
    UNION ALL
    SELECT (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS strike_day
    FROM strele.udari_24h u
    CROSS JOIN slo
    CROSS JOIN center c
    WHERE u.geom && ST_Envelope(ST_Buffer(c.geom::geography, %(radius_m)s::double precision)::geometry)
      AND ST_Intersects(u.geom, slo.geom)
      AND ST_DWithin(u.geom::geography, c.geom::geography, %(radius_m)s)
      AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date >= %(today)s
      AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date < %(tomorrow)s
      AND NOT EXISTS (
        SELECT 1
        FROM strele.udari x
        WHERE x.ts_utc = u.ts_utc
          AND ST_Equals(x.geom, u.geom)
      )
"""

_DAILY_READ_SQL = f"""
SELECT
    j.grid_x,
    j.grid_y,
    j.strike_count,
    ST_AsGeoJSON(
        ST_Transform(
            ST_MakeEnvelope(j.grid_x, j.grid_y, j.grid_x + %s, j.grid_y + %s, %s),
            4326
        )
    ) AS geometry_json
FROM (
    SELECT grid_x, grid_y, SUM(strike_count)::int AS strike_count
    FROM {_DAILY_TABLE}
    WHERE day_local BETWEEN %s AND %s
    GROUP BY grid_x, grid_y
    HAVING SUM(strike_count) > 0
) j
ORDER BY j.grid_y, j.grid_x
"""


@lru_cache(maxsize=1)
def _slovenia_wkt(data_dir: str) -> str:
    path = Path(data_dir) / "SR.geojson"
    data = json.loads(path.read_text(encoding="utf-8"))
    geoms = [shape(feature["geometry"]) for feature in data["features"]]
    return unary_union(geoms).wkt


@lru_cache(maxsize=1)
def _region_index(data_dir: str) -> RegionIndex:
    return load_regions(Path(data_dir) / "SR.geojson")


def calendar_bounds_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """Lokalni koledarski dnevi [start, end] vključno → UTC polovi [t0, t1)."""
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=_LJ_TZ).astimezone(timezone.utc)
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=_LJ_TZ).astimezone(
        timezone.utc
    )
    return t0, t1


def local_today() -> date:
    return datetime.now(tz=_LJ_TZ).date()


def today_cache_basename() -> str:
    return _TODAY_CACHE_BASENAME


def make_cell_id(grid_x: int, grid_y: int) -> str:
    return f"{_GRID_CRS}:{grid_x}:{grid_y}"


def parse_cell_id(cell_id: str) -> tuple[int, int, int] | None:
    parts = cell_id.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def build_feature_collection(
    rows: list[tuple],
    *,
    period_from: date,
    period_to: date,
) -> dict:
    features = []
    for grid_x, grid_y, strike_count, geometry_json in rows:
        geometry = json.loads(geometry_json) if isinstance(geometry_json, str) else geometry_json
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "cell_id": make_cell_id(int(grid_x), int(grid_y)),
                    "strike_count": int(strike_count),
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "period": {
            "from": period_from.isoformat(),
            "to": period_to.isoformat(),
        },
        "features": features,
    }


def _read_daily_rows(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            _DAILY_READ_SQL,
            (
                _GRID_SIZE_M,
                _GRID_SIZE_M,
                _GRID_CRS,
                start,
                end,
            ),
        )
        return cur.fetchall()


def fetch_grid_map_from_daily(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
) -> dict:
    """Zgradi GeoJSON iz dnevne agregatne tabele (brez surovih strel)."""
    rows = _read_daily_rows(conn, start=start, end=end)
    return build_feature_collection(rows, period_from=start, period_to=end)


def fetch_grid_map(
    *,
    start: date,
    end: date,
    data_dir: Path,
    database_url: str,
    viewport: tuple[float, float, float, float] | None = None,
) -> dict:
    """
    Agregira udare v mrežo 1 × 1 km iz surovih strel.

    Uporablja se samo v jobu za rebuild dnevne tabele — ne v API.
    """
    slo_wkt = _slovenia_wkt(str(data_dir))
    t0, t1 = calendar_bounds_utc(start, end)
    today = date.today()

    extra_union = ""
    params: dict = {
        "slo_wkt": slo_wkt,
        "t0": t0,
        "t1": t1,
        "grid_crs": _GRID_CRS,
        "grid_size": _GRID_SIZE_M,
        "extra_union": "",
        "viewport_filter": "",
    }

    if end >= today:
        extra_union = _EXTRA_UNION_TODAY
        params["today"] = today
        params["tomorrow"] = today + timedelta(days=1)

    if viewport is not None:
        min_lon, min_lat, max_lon, max_lat = viewport
        params["viewport_filter"] = _VIEWPORT_FILTER
        params["vp_min_lon"] = min_lon
        params["vp_min_lat"] = min_lat
        params["vp_max_lon"] = max_lon
        params["vp_max_lat"] = max_lat

    sql = _GRID_AGG_SQL.replace("%(extra_union)s", extra_union).replace(
        "%(viewport_filter)s", params["viewport_filter"]
    )

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return build_feature_collection(rows, period_from=start, period_to=end)


def rebuild_grid_daily_aggregates(
    conn: psycopg.Connection,
    *,
    day_from: date,
    day_to: date,
) -> None:
    """Rebuild daily strike_count per 1×1 km cell for local days [day_from, day_to] inclusive."""
    t0, t1 = calendar_bounds_utc(day_from, day_to)
    today = date.today()
    slo_wkt = _slovenia_wkt(str(Path(__file__).resolve().parents[1] / "data"))

    params: dict = {
        "slo_wkt": slo_wkt,
        "t0": t0,
        "t1": t1,
        "grid_crs": _GRID_CRS,
        "grid_size": _GRID_SIZE_M,
        "today": today,
        "tomorrow": today + timedelta(days=1),
    }

    extra_today = ""
    if day_to >= today:
        extra_today = """
        UNION ALL
        SELECT u.geom, (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS day_local
        FROM strele.udari_24h u
        CROSS JOIN slo
        CROSS JOIN bounds b
        WHERE u.geom && ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
          AND ST_Intersects(u.geom, slo.geom)
          AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date >= %(today)s
          AND (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date < %(tomorrow)s
          AND NOT EXISTS (
            SELECT 1
            FROM strele.udari x
            WHERE x.ts_utc = u.ts_utc
              AND ST_Equals(x.geom, u.geom)
          )
        """

    delete_daily_sql = f"DELETE FROM {_DAILY_TABLE} WHERE day_local BETWEEN %s AND %s"

    insert_sql = f"""
WITH slo AS (
    SELECT ST_SetSRID(ST_GeomFromText(%(slo_wkt)s), 4326) AS geom
),
bounds AS (
    SELECT
        ST_XMin(geom) AS min_lon,
        ST_YMin(geom) AS min_lat,
        ST_XMax(geom) AS max_lon,
        ST_YMax(geom) AS max_lat,
        geom
    FROM slo
),
strikes AS (
    SELECT
      u.geom,
      (u.ts_utc AT TIME ZONE 'Europe/Ljubljana')::date AS day_local
    FROM strele.udari u
    CROSS JOIN slo
    CROSS JOIN bounds b
    WHERE u.ts_utc >= %(t0)s
      AND u.ts_utc < %(t1)s
      AND u.geom && ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
      AND ST_Intersects(u.geom, slo.geom)
    {extra_today}
),
cells AS (
    SELECT
        (ST_X(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s)))::bigint AS grid_x,
        (ST_Y(ST_SnapToGrid(ST_Transform(geom, %(grid_crs)s), %(grid_size)s, %(grid_size)s)))::bigint AS grid_y,
        day_local
    FROM strikes
),
agg AS (
    SELECT day_local, grid_x, grid_y, COUNT(*)::int AS strike_count
    FROM cells
    GROUP BY day_local, grid_x, grid_y
)
INSERT INTO {_DAILY_TABLE} (day_local, grid_x, grid_y, strike_count, updated_at)
SELECT day_local, grid_x, grid_y, strike_count, now()
FROM agg
"""
    with conn.cursor() as cur:
        cur.execute(delete_daily_sql, (day_from, day_to))
        cur.execute(insert_sql, params)


def _cache_metadata(
    *,
    start: date,
    end: date,
    days: int | None = None,
) -> dict:
    meta = {
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "cached": True,
        "cache_version": int(_CACHE_VERSION),
    }
    if days is not None:
        meta["days"] = int(days)
    return meta


def build_cached_feature_collection(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
    days: int,
) -> dict:
    """Build cached GeoJSON (density only) from daily table for [start, end] local days."""
    rows = _read_daily_rows(conn, start=start, end=end)
    out = build_feature_collection(rows, period_from=start, period_to=end)
    out.update(_cache_metadata(start=start, end=end, days=days))
    return out


def build_today_cached_feature_collection(
    conn: psycopg.Connection,
    *,
    today_local: date,
) -> dict:
    """Cache za današnji lokalni dan — samo celice s strike_count > 0."""
    rows = _read_daily_rows(conn, start=today_local, end=today_local)
    out = build_feature_collection(rows, period_from=today_local, period_to=today_local)
    out.update(_cache_metadata(start=today_local, end=today_local, days=1))
    return out


def region_bounds(data_dir: Path) -> tuple[float, float, float, float]:
    """min_lon, min_lat, max_lon, max_lat."""
    return _region_index(str(data_dir)).bounds


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def resolve_grid_cell(
    conn: psycopg.Connection,
    *,
    lat: float,
    lon: float,
    data_dir: Path | None = None,
) -> dict | None:
    """Poravna točko na celico 1 × 1 km; vrne None, če celica ni v Sloveniji."""
    slo_wkt = _slovenia_wkt(str(data_dir or _default_data_dir()))
    with conn.cursor() as cur:
        cur.execute(
            _CELL_RESOLVE_SQL,
            {
                "slo_wkt": slo_wkt,
                "lat": lat,
                "lon": lon,
                "grid_crs": _GRID_CRS,
                "grid_size": _GRID_SIZE_M,
            },
        )
        row = cur.fetchone()
    if not row or not row[5]:
        return None
    grid_x, grid_y, geometry_json, center_lat, center_lon, _in_slo = row
    geometry = json.loads(geometry_json) if isinstance(geometry_json, str) else geometry_json
    return {
        "cell_id": make_cell_id(int(grid_x), int(grid_y)),
        "center_lat": float(center_lat),
        "center_lon": float(center_lon),
        "geometry": geometry,
    }


def _fetch_radius_daily(
    conn: psycopg.Connection,
    *,
    center_lat: float,
    center_lon: float,
    start: date,
    end: date,
    radius_km: int,
    slo_wkt: str,
) -> list[dict]:
    t0, t1 = calendar_bounds_utc(start, end)
    today = date.today()
    extra_union = ""
    params: dict = {
        "slo_wkt": slo_wkt,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "start": start,
        "end": end,
        "t0": t0,
        "t1": t1,
        "radius_m": radius_km * 1000,
        "extra_union_today": "",
    }
    if end >= today:
        extra_union = _EXTRA_UNION_TODAY_RADIUS
        params["extra_union_today"] = extra_union
        params["today"] = today
        params["tomorrow"] = today + timedelta(days=1)

    sql = _RADIUS_DAILY_SQL.replace("%(extra_union_today)s", extra_union)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    daily = [{"datum": str(r[0]), "stevilo": int(r[1])} for r in rows]
    total = sum(d["stevilo"] for d in daily)
    return {"radius_km": radius_km, "total": total, "daily": daily}


def fetch_grid_cell_daily(
    conn: psycopg.Connection,
    *,
    lat: float,
    lon: float,
    start: date,
    end: date,
    data_dir: Path | None = None,
) -> dict | None:
    """Celica + dnevni niz za fiksni radij 10 km okoli središča celice."""
    cell = resolve_grid_cell(conn, lat=lat, lon=lon, data_dir=data_dir)
    if cell is None:
        return None
    slo_wkt = _slovenia_wkt(str(data_dir or _default_data_dir()))
    series = [
        _fetch_radius_daily(
            conn,
            center_lat=cell["center_lat"],
            center_lon=cell["center_lon"],
            start=start,
            end=end,
            radius_km=GRID_CELL_DAILY_RADIUS_KM,
            slo_wkt=slo_wkt,
        )
    ]
    return {
        "cell": cell,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "series": series,
    }
