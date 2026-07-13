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
        COUNT(DISTINCT strike_day)::int AS storm_days,
        ROUND(ST_XMin(cell_geom))::bigint AS grid_x,
        ROUND(ST_YMin(cell_geom))::bigint AS grid_y,
        ROUND(ST_Y(ST_Transform(ST_Centroid(cell_geom), 4326))::numeric, 6) AS center_lat,
        ROUND(ST_X(ST_Transform(ST_Centroid(cell_geom), 4326))::numeric, 6) AS center_lon
    FROM cells
    GROUP BY cell_geom
)
SELECT
    grid_x,
    grid_y,
    strike_count,
    storm_days,
    center_lat,
    center_lon,
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
    for grid_x, grid_y, strike_count, storm_days, center_lat, center_lon, geometry_json in rows:
        geometry = json.loads(geometry_json) if isinstance(geometry_json, str) else geometry_json
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "cell_id": make_cell_id(int(grid_x), int(grid_y)),
                    "strike_count": int(strike_count),
                    "storm_days": int(storm_days),
                    "center_lat": float(center_lat),
                    "center_lon": float(center_lon),
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


def fetch_grid_map(
    *,
    start: date,
    end: date,
    data_dir: Path,
    database_url: str,
    viewport: tuple[float, float, float, float] | None = None,
) -> dict:
    """
    Agregira udare v mrežo 1 × 1 km.

    viewport: (min_lon, min_lat, max_lon, max_lat) — opcijsko zoži na vidno območje.
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


def region_bounds(data_dir: Path) -> tuple[float, float, float, float]:
    """min_lon, min_lat, max_lon, max_lat."""
    return _region_index(str(data_dir)).bounds
