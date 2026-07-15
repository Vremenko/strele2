"""Pridobivanje surovih udarov iz zgodovinske tabele strele.udari."""

from __future__ import annotations

import os
from datetime import date, datetime

import psycopg

from strele_archive.regions import RegionIndex
from strele_archive.timezone_utils import lj_day_bounds_utc


def udari_database_url() -> str | None:
    url = (
        os.getenv("UDARI_DATABASE_URL", "").strip()
        or os.getenv("STORM_DATABASE_URL", "").strip()
    )
    if not url:
        return None
    return url.replace("postgresql+psycopg://", "postgresql://")


def fetch_udari_window(
    time_from_utc: datetime,
    time_to_utc: datetime,
    regions: RegionIndex,
    *,
    database_url: str | None = None,
) -> list[dict]:
    """Surovi udari iz strele.udari za časovno okno (bbox predfilter)."""
    url = database_url or udari_database_url()
    if not url:
        return []

    min_lon, min_lat, max_lon, max_lat = regions.bounds
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT lat, lon, ts_utc
                FROM strele.udari
                WHERE ts_utc >= %s AND ts_utc < %s
                  AND lat BETWEEN %s AND %s
                  AND lon BETWEEN %s AND %s
                ORDER BY ts_utc ASC
                """,
                (time_from_utc, time_to_utc, min_lat, max_lat, min_lon, max_lon),
            )
            rows = cur.fetchall()

    return [
        {"lat": float(r[0]), "lon": float(r[1]), "ts_utc": r[2].isoformat()}
        for r in rows
    ]


def fetch_udari_calendar_day(
    day: date,
    regions: RegionIndex,
    *,
    tz_name: str = "Europe/Ljubljana",
    database_url: str | None = None,
) -> list[dict]:
    """Cel koledarski dan iz strele.udari (zanesljiv vir za zaključek dne)."""
    time_from, time_to = lj_day_bounds_utc(day, tz_name=tz_name)
    return fetch_udari_window(time_from, time_to, regions, database_url=database_url)
