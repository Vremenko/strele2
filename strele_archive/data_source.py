"""Zdruzen vir: lokalna baza (primarno), API samo za ozadjski arhiv."""

from __future__ import annotations

import logging
from datetime import date

from strele_archive.hourly_reconcile import query_hourly_from_db, reconcile_hourly_for_day

logger = logging.getLogger(__name__)


def _local():
    from strele_archive import export as local

    return local


def get_si_daily(days: int) -> tuple[list[dict], str]:
    return _local().export_si_daily(days), "local"


def get_si_hourly(day: date | None = None, *, days: int | None = None) -> tuple[list[dict], str]:
    try:
        if days is not None:
            return _local().export_si_hourly_period(days), "local"
        if day is not None:
            reconciled = reconcile_hourly_for_day(day)
            if reconciled is not None:
                return reconciled, "local"
            return query_hourly_from_db(day), "local"
    except Exception:
        logger.warning("Lokalni urni podatki nedosegljivi", exc_info=True)
    return [{"ura": h, "stevilo": 0} for h in range(24)], "local"


def get_regije(day: date | None = None, *, days: int | None = None) -> tuple[list[dict], str]:
    try:
        if days is not None:
            return _local().export_regije_period(days), "local"
        if day is not None:
            return _local().export_regije_daily(day), "local"
    except Exception:
        logger.warning("Lokalne regije nedosegljive", exc_info=True)
    return [], "local"


def get_obcine_top(
    day: date | None = None,
    *,
    days: int | None = None,
    limit: int = 10,
) -> tuple[list[dict], str]:
    if days is not None:
        return _local().export_obcine_top_period(days, limit), "local"
    if day is not None:
        return _local().export_obcine_top(day, limit), "local"
    return [], "local"


def get_obcine_gostota_top(
    day: date | None = None,
    *,
    days: int | None = None,
    limit: int = 10,
) -> tuple[list[dict], str]:
    if days is not None:
        return _local().export_obcine_gostota_period(days, limit), "local"
    if day is not None:
        return _local().export_obcine_gostota_top(day, limit), "local"
    return [], "local"


def get_obcine_map(day: date) -> tuple[list[dict], str]:
    return _local().export_obcine_map(day), "local"


def get_latest_date() -> tuple[date | None, str]:
    try:
        return _local().export_latest_date(), "local"
    except Exception:
        return None, "local"


def get_archive_info() -> dict:
    try:
        return _local().export_archive_info()
    except Exception:
        return {}
