"""PiP filtriranje in urno štetje za slovenski widget (scope=slovenija)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from strele_archive.regions import RegionIndex


def filter_pip_strikes(
    rows: list[tuple[float, float, datetime]],
    region_index: RegionIndex,
) -> list[tuple[float, float, datetime]]:
    """Obdrži le udare znotraj meje Slovenije (PiP)."""
    inside: list[tuple[float, float, datetime]] = []
    for lat, lon, ts in rows:
        if region_index.contains(lon, lat):
            inside.append((lat, lon, ts))
    return inside


def bucket_hourly_rolling_24h(
    strikes: list[tuple[float, float, datetime]],
    *,
    now_utc: datetime,
    tz_name: str = "Europe/Ljubljana",
) -> tuple[int, int, list[dict]]:
    """
    Zgradi 24 urne buckete (rolling 24 h v lokalnem času) iz PiP filtriranih udarov.
    Vrne (total_24h, last_hour_count, hourly_list).
    """
    tz = ZoneInfo(tz_name)
    by_hour: dict[str, int] = {}
    for _lat, _lon, ts in strikes:
        local = ts.astimezone(tz)
        key = local.strftime("%Y-%m-%dT%H:00:00")
        by_hour[key] = by_hour.get(key, 0) + 1

    hourly: list[dict] = []
    cursor = now_utc.astimezone(tz).replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    for _ in range(24):
        key = cursor.strftime("%Y-%m-%dT%H:00:00")
        hourly.append({
            "ura": cursor.hour,
            "label": f"{cursor.hour:02d}:00",
            "stevilo": by_hour.get(key, 0),
            "t": key,
        })
        cursor += timedelta(hours=1)

    total = sum(h["stevilo"] for h in hourly)
    last_hour = hourly[-1]["stevilo"] if hourly else 0
    return total, last_hour, hourly
