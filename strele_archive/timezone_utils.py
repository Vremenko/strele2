"""Časovna okna v Europe/Ljubljana (poletni/zimski čas)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def lj_timezone(tz_name: str = "Europe/Ljubljana") -> ZoneInfo:
    return ZoneInfo(tz_name)


def lj_day_bounds_utc(
    day: date,
    *,
    tz_name: str = "Europe/Ljubljana",
    end_cap_utc: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    Vrne [start, end) koledarskega dne v lokalnem času, pretvorjenega v UTC.

    Za tekoči dan lahko podaš end_cap_utc (npr. zdaj), da se okno ne razteza v prihodnost.
    """
    tz = lj_timezone(tz_name)
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    if end_cap_utc is not None:
        cap = end_cap_utc if end_cap_utc.tzinfo else end_cap_utc.replace(tzinfo=timezone.utc)
        if cap < end_utc:
            end_utc = cap
    return start_utc, end_utc


def local_parts(ts_utc: datetime, tz_name: str) -> tuple[date, int]:
    """Lokalni datum in ura (0–23) za UTC časovni žig."""
    local = ts_utc.astimezone(lj_timezone(tz_name))
    return local.date(), local.hour
