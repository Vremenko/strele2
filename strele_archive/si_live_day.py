"""Živi SI urni profil za tekoči koledarski dan (udari_24h + PiP, Europe/Ljubljana)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

import psycopg

from zoneinfo import ZoneInfo

from strele_archive.config import get_settings
from strele_archive.obcina_widget_daily import local_today
from strele_archive.regions import load_regions
from strele_archive.si_widget_counts import dedup_pip_strikes, filter_pip_strikes
from strele_archive.timezone_utils import lj_day_bounds_utc
from strele_archive.udari_client import udari_database_url

_region_index = None


def _regions():
    global _region_index
    if _region_index is None:
        _region_index = load_regions(get_settings().regions_geojson)
    return _region_index


def _parse_ts(ts_raw) -> datetime | None:
    if ts_raw is None:
        return None
    if isinstance(ts_raw, datetime):
        ts = ts_raw
    else:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _fetch_udari_24h_window(
    start_utc: datetime,
    end_inclusive: datetime,
) -> list[tuple[float, float, datetime]]:
    url = udari_database_url()
    if not url:
        return []
    idx = _regions()
    min_lon, min_lat, max_lon, max_lat = idx.bounds
    parsed: list[tuple[float, float, datetime]] = []
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
                LIMIT 50000
                """,
                (start_utc, end_inclusive, min_lat, max_lat, min_lon, max_lon),
            )
            for lat_raw, lon_raw, ts_raw in cur.fetchall():
                ts = _parse_ts(ts_raw)
                if ts is None:
                    continue
                parsed.append((float(lat_raw), float(lon_raw), ts))
    return parsed


def live_si_hourly_for_day(
    day: date,
    *,
    now_utc: datetime | None = None,
) -> list[dict] | None:
    """
    Urni profil za lokalni danes iz živih udarov.
    Vrne None, če dan ni danes ali vir ni na voljo (ostane arhiv).
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if day != local_today(now):
        return None
    if not udari_database_url():
        return None

    start_utc, end_utc = lj_day_bounds_utc(day, end_cap_utc=now)
    end_inclusive = end_utc - timedelta(microseconds=1) if end_utc > start_utc else end_utc
    raw = _fetch_udari_24h_window(start_utc, end_inclusive)
    inside = dedup_pip_strikes(filter_pip_strikes(raw, _regions()))
    tz_name = get_settings().timezone
    tz = ZoneInfo(tz_name)
    by_hour: Counter[int] = Counter()
    for _lat, _lon, ts in inside:
        local = ts.astimezone(tz)
        if local.date() != day:
            continue
        by_hour[local.hour] += 1
    return [{"ura": h, "stevilo": int(by_hour.get(h, 0))} for h in range(24)]
