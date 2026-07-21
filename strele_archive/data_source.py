"""Zdruzen vir: lokalna baza (primarno), API samo za ozadjski arhiv."""

from __future__ import annotations

import logging
from datetime import date

from strele_archive.hourly_reconcile import query_hourly_from_db, reconcile_hourly_for_day

logger = logging.getLogger(__name__)


def _local():
    from strele_archive import export as local

    return local


def get_si_daily(
    days: int | None = None,
    *,
    from_: date | None = None,
    to_: date | None = None,
) -> tuple[list[dict], str]:
    return _local().export_si_daily(days, from_=from_, to_=to_), "local"


def get_si_hourly(
    day: date | None = None,
    *,
    days: int | None = None,
    from_: date | None = None,
    to_: date | None = None,
) -> tuple[list[dict], str]:
    try:
        if from_ is not None and to_ is not None:
            return _local().export_si_hourly_period(from_=from_, to_=to_), "local"
        if days is not None:
            return _local().export_si_hourly_period(days), "local"
        if day is not None:
            # Tekoči dan: živi urni profil (enako kot si-daily na 8083) — sicer
            # arhiv vrne 0 in klik na današnji stolpec počisti izbiro.
            from strele_archive.si_live_day import live_si_hourly_for_day

            live = live_si_hourly_for_day(day)
            if live is not None:
                return live, "live"
            reconciled = reconcile_hourly_for_day(day)
            if reconciled is not None:
                return reconciled, "local"
            return query_hourly_from_db(day), "local"
    except Exception:
        logger.warning("Lokalni urni podatki nedosegljivi", exc_info=True)
    return [{"ura": h, "stevilo": 0} for h in range(24)], "local"


def get_regije(day: date | None = None, *, days: int | None = None) -> tuple[list[dict], str]:
    try:
        from strele_archive.live_today_obcine import (
            live_regije_for_today,
            merge_live_named_counts,
        )
        from strele_archive.obcina_widget_daily import (
            archive_end_excluding_live_today,
            local_today,
        )

        from datetime import timedelta

        today = local_today()
        # Samo danes → živi števci občin → vsote regij.
        if day is not None and day == today and days is None:
            live = live_regije_for_today(today=today)
            if live is not None:
                return live, "live"
        # Obdobje do danes: arhiv do včeraj + živi danes (replace, ne prištevanje).
        if days is not None:
            end = today
            start = end - timedelta(days=days - 1)
            archive_end = archive_end_excluding_live_today(start, end, today)
            by_name: dict[str, int] = {}
            if archive_end is not None:
                d = start
                while d <= archive_end:
                    for row in _local().export_regije_daily(d):
                        name = str(row.get("regija") or "")
                        if name:
                            by_name[name] = by_name.get(name, 0) + int(
                                row.get("stevilo") or 0
                            )
                    d += timedelta(days=1)
            archive = [{"regija": k, "stevilo": v} for k, v in by_name.items()]
            live_rows = live_regije_for_today(today=today) or []
            live_map = {r["regija"]: int(r["stevilo"]) for r in live_rows}
            merged = merge_live_named_counts(archive, live_map, name_key="regija")
            merged.sort(
                key=lambda r: (-int(r.get("stevilo") or 0), str(r.get("regija") or ""))
            )
            return merged, "live"
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
