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


def dedup_pip_strikes(
    strikes: list[tuple[float, float, datetime]],
) -> list[tuple[float, float, datetime]]:
    """Odstrani podvojene udare (lat, lon, ts_utc)."""
    seen: set[tuple[float, float, datetime]] = set()
    unique: list[tuple[float, float, datetime]] = []
    for lat, lon, ts in strikes:
        key = (round(lat, 6), round(lon, 6), ts)
        if key in seen:
            continue
        seen.add(key)
        unique.append((lat, lon, ts))
    return unique


def pip_strikes_to_map_records(
    strikes: list[tuple[float, float, datetime]],
    *,
    iso_utc,
    max_strikes: int = 50_000,
) -> tuple[list[dict], dict]:
    """
    Pretvori PiP udare v zapise za zemljevid (deduplikacija, ASC po času).
    Vrne (records, meta) z map_complete / map_message ob varovalu.
    """
    unique = dedup_pip_strikes(strikes)
    records: list[dict] = []
    for lat, lon, ts in sorted(unique, key=lambda row: row[2]):
        records.append({"lat": lat, "lon": lon, "ts_utc": iso_utc(ts)})

    total = len(records)
    meta: dict = {"map_complete": True, "map_total_pip": total}
    if total > max_strikes:
        meta = {
            "map_complete": False,
            "map_total_pip": total,
            "map_message": (
                f"Prikaz ni popoln: prikazanih {max_strikes:,} od {total:,} udarov "
                f"v zadnjih 24 urah."
            ).replace(",", "."),
        }
        records = records[:max_strikes]
    return records, meta
