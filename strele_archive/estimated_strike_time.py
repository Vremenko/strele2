"""Ocenjeni čas udara: Europe/Ljubljana, nato zaokrožitev na 5 min (samo prikaz)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LJUBLJANA_TZ = ZoneInfo("Europe/Ljubljana")

ESTIMATED_STRIKE_TIME_NOTE = (
    "Časi posameznih udarov so ocenjeni na podlagi razpoložljivih podatkov "
    "in zaokroženi na 5 minut."
)


def _to_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def round_estimated_strike_datetime(ts: datetime) -> datetime:
    """
    Pretvori v Europe/Ljubljana, zaokroži na najbližjih 5 minut.
    Vrne lokalni datetime (sekunde = 0). Ne spreminja shranjenih podatkov.
    """
    local = _to_aware_utc(ts).astimezone(LJUBLJANA_TZ)
    total_min = (
        local.hour * 60
        + local.minute
        + local.second / 60
        + local.microsecond / 60_000_000
    )
    rounded = int(round(total_min / 5) * 5)
    day = local.date()
    if rounded >= 24 * 60:
        rounded -= 24 * 60
        day = day + timedelta(days=1)
    elif rounded < 0:
        rounded += 24 * 60
        day = day - timedelta(days=1)
    hour, minute = divmod(rounded, 60)
    return datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=LJUBLJANA_TZ)


def format_estimated_strike_time(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    local = round_estimated_strike_datetime(ts)
    return f"{local.hour:02d}.{local.minute:02d}"


def format_estimated_strike_datetime(ts: datetime | None) -> str:
    """Prikaz kot v widgetu: ``19. 7. 2026, 12.15``."""
    if ts is None:
        return "—"
    local = round_estimated_strike_datetime(ts)
    return f"{local.day}. {local.month}. {local.year}, {local.hour:02d}.{local.minute:02d}"
