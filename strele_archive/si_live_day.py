"""Živi SI urni profil za tekoči koledarski dan (isti vir kot občinski zemljevid)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from strele_archive.live_today_obcine import (
    live_hourly_from_pip,
    live_today_si_pip_tuples,
)
from strele_archive.obcina_widget_daily import local_today
from strele_archive.udari_client import udari_database_url


def live_si_hourly_for_day(
    day: date,
    *,
    now_utc: datetime | None = None,
) -> list[dict] | None:
    """
    Urni profil za lokalni danes iz živih udarov.
    Vrne None, če dan ni danes ali vir ni na voljo (ostane arhiv).
    Uporabi isti PiP nabor kot občinski zemljevid (brez ločenega SQL).
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if day != local_today(now):
        return None
    if not udari_database_url():
        return None
    pip = live_today_si_pip_tuples(today=day, now_utc=now)
    return live_hourly_from_pip(pip, day)
