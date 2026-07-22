"""Uskladitev arhivskih agregatov z avtoritativnimi surovimi udarji iz StormAPI."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from strele_archive.config import Settings, get_settings
from strele_archive.db import Database
from strele_archive.obcine import ObcinaIndex, load_obcine
from strele_archive.regions import RegionIndex, load_regions
from strele_archive.udari_client import fetch_udari_calendar_day, udari_database_url
from strele_archive.storm_client import fetch_strikes_window
from strele_archive.strike_processing import (
    ClassifiedStrike,
    DayAggregates,
    aggregate_for_day,
    classify_strikes,
    hourly_series,
)
from strele_archive.timezone_utils import lj_day_bounds_utc, lj_timezone

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).resolve().parents[1] / ".reconcile-state.json"


@dataclass
class ReconcileResult:
    day: date
    fetched: int
    inside: int
    outside: int
    national_total: int
    applied: bool
    previous_daily: int | None = None


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def compute_day_from_stormapi(
    day: date,
    *,
    settings: Settings | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    end_cap_utc: datetime | None = None,
) -> tuple[list[ClassifiedStrike], DayAggregates, int, int]:
    """Pridobi in agregira dan iz udari_24h (GET /strele) — le za tekoči/nezgodovinski dan."""
    settings = settings or get_settings()
    regions = regions or load_regions(settings.regions_geojson)
    obcine = obcine or load_obcine(settings.obcine_geojson)
    tz_name = settings.timezone

    time_from, time_to = lj_day_bounds_utc(day, tz_name=tz_name, end_cap_utc=end_cap_utc)
    raw = fetch_strikes_window(settings, regions, time_from, time_to)
    classified, outside = classify_strikes(raw, regions, obcine, tz_name)
    aggs = aggregate_for_day(classified, day)
    return classified, aggs, len(raw), outside


def compute_day_from_udari(
    day: date,
    *,
    settings: Settings | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
) -> tuple[list[ClassifiedStrike], DayAggregates, int, int] | None:
    """Cel koledarski dan iz strele.udari. Vrne None, če vir ni na voljo."""
    settings = settings or get_settings()
    regions = regions or load_regions(settings.regions_geojson)
    obcine = obcine or load_obcine(settings.obcine_geojson)
    if not udari_database_url():
        return None

    raw = fetch_udari_calendar_day(day, regions, tz_name=settings.timezone)
    classified, outside = classify_strikes(raw, regions, obcine, settings.timezone)
    aggs = aggregate_for_day(classified, day)
    return classified, aggs, len(raw), outside


def compute_day(
    day: date,
    *,
    settings: Settings | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    end_cap_utc: datetime | None = None,
    prefer_udari: bool = False,
) -> tuple[list[ClassifiedStrike], DayAggregates, int, int, str]:
    """
    Avtoritativni izračun dne. Vir:
    - prefer_udari / zaključeni dnevi: strele.udari
    - tekoči dan: udari_24h prek StormAPI
    """
    settings = settings or get_settings()
    tz = lj_timezone(settings.timezone)
    today = datetime.now(tz).date()

    if prefer_udari or day < today:
        udari = compute_day_from_udari(day, settings=settings, regions=regions, obcine=obcine)
        if udari is not None:
            classified, aggs, fetched, outside = udari
            return classified, aggs, fetched, outside, "udari"

    classified, aggs, fetched, outside = compute_day_from_stormapi(
        day,
        settings=settings,
        regions=regions,
        obcine=obcine,
        end_cap_utc=end_cap_utc,
    )
    return classified, aggs, fetched, outside, "udari_24h"


def reconcile_day(
    day: date,
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    end_cap_utc: datetime | None = None,
    dry_run: bool = False,
) -> ReconcileResult:
    """
    Idempotentna uskladitev agregatov za koledarski dan.
    Agregati se zamenjajo (ne inkrementirajo); dedup ključi se uskladijo.
    """
    settings = settings or get_settings()
    db = db or Database(settings.database_url)
    tz_name = settings.timezone

    previous_daily = db.get_daily_count(day)
    classified, aggs, fetched, outside, source = compute_day(
        day,
        settings=settings,
        regions=regions,
        obcine=obcine,
        end_cap_utc=end_cap_utc,
        prefer_udari=day < datetime.now(lj_timezone(settings.timezone)).date(),
    )

    # Ne zmanjšaj arhiva, če vir (npr. udari_24h) ni popoln
    if (
        not dry_run
        and previous_daily is not None
        and aggs.national_daily < previous_daily
        and source == "udari_24h"
    ):
        logger.warning(
            "Reconcile %s preskočen: vir=%s total=%s < arhiv=%s",
            day.isoformat(),
            source,
            aggs.national_daily,
            previous_daily,
        )
        return ReconcileResult(
            day=day,
            fetched=fetched,
            inside=aggs.national_daily,
            outside=outside,
            national_total=aggs.national_daily,
            applied=False,
            previous_daily=previous_daily,
        )

    result = ReconcileResult(
        day=day,
        fetched=fetched,
        inside=aggs.national_daily,
        outside=outside,
        national_total=aggs.national_daily,
        applied=False,
        previous_daily=previous_daily,
    )

    if dry_run:
        return result

    db.replace_day_aggregates(day, aggs, classified, tz_name=tz_name)
    result.applied = True
    logger.info(
        "Reconcile %s: fetched=%s inside=%s outside=%s prev_daily=%s new_daily=%s",
        day.isoformat(),
        fetched,
        aggs.national_daily,
        outside,
        previous_daily,
        aggs.national_daily,
    )
    return result


def reconcile_today(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    dry_run: bool = False,
) -> ReconcileResult:
    """Uskladi tekoči koledarski dan od lokalne polnoči do zdaj."""
    settings = settings or get_settings()
    tz = lj_timezone(settings.timezone)
    now_local = datetime.now(tz)
    today = now_local.date()
    return reconcile_day(
        today,
        settings=settings,
        db=db,
        regions=regions,
        obcine=obcine,
        end_cap_utc=datetime.now(timezone.utc),
        dry_run=dry_run,
    )


def should_defer_finalize(
    result: ReconcileResult,
    now_local: datetime,
    *,
    retry_until_hour: int = 12,
) -> bool:
    """
    True = še ne zakleni dneva; poskusi znova ob naslednjem reconcile.

    Odloži, če je total 0 ali očitno nižji od že zbranega arhiva (vir še ni poln).
    Po retry_until_hour (lokalno) sprejmi tudi 0 / nižjo vrednost.
    """
    if now_local.hour >= retry_until_hour:
        return False
    total = int(result.national_total or 0)
    prev = int(result.previous_daily or 0)
    if total == 0:
        return True
    if prev > 0 and total < prev:
        return True
    return False


def maybe_finalize_previous_day(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    dry_run: bool = False,
) -> ReconcileResult | None:
    """
    Zaključek prejšnjega koledarskega dne iz strele.udari (NE iz udari_24h).

    udari_24h ob 00:05 ne vsebuje več celotnega prejšnjega dne — zato je
    zanesljiv vir zgodovinska tabela udari ali arhiv, zgrajen med dnem.

    Če je rezultat 0 ali sumljivo nizek glede na arhiv, ne zaklene dneva
    in poskusi znova (do finalize_retry_until_hour).
    """
    settings = settings or get_settings()
    tz = lj_timezone(settings.timezone)
    now_local = datetime.now(tz)
    yesterday = now_local.date() - timedelta(days=1)

    state = _load_state()
    if state.get("finalized_day") == yesterday.isoformat():
        return None

    if now_local.hour == 0 and now_local.minute < 15:
        return None

    if not udari_database_url():
        logger.warning(
            "Zaključek %s preskočen: UDARI_DATABASE_URL ni nastavljen (udari_24h ni zanesljiv)",
            yesterday.isoformat(),
        )
        return None

    # Najprej samo preveri — ne piši 0/nižje vrednosti, če še čakamo na poln vir.
    probe = reconcile_day(
        yesterday,
        settings=settings,
        db=db,
        regions=regions,
        obcine=obcine,
        dry_run=True,
    )
    if should_defer_finalize(
        probe,
        now_local,
        retry_until_hour=settings.finalize_retry_until_hour,
    ):
        logger.warning(
            "Zaključek %s odložen: total=%s prev_arhiv=%s (ponovni poskus do %s:00)",
            yesterday.isoformat(),
            probe.national_total,
            probe.previous_daily,
            settings.finalize_retry_until_hour,
        )
        return probe

    result = reconcile_day(
        yesterday,
        settings=settings,
        db=db,
        regions=regions,
        obcine=obcine,
        dry_run=dry_run,
    )
    if not dry_run and result.applied:
        state["finalized_day"] = yesterday.isoformat()
        _save_state(state)
    return result


def maybe_pre_finalize_today(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    dry_run: bool = False,
) -> ReconcileResult | None:
    """
    Pred polnočjo (privzeto 23:50 LJ) uskladi tekoči dan, dokler so podatki
    še v udari_24h. To je varna alternativa zaključku ob 00:05.
    """
    settings = settings or get_settings()
    tz = lj_timezone(settings.timezone)
    now_local = datetime.now(tz)
    today = now_local.date()

    if (now_local.hour, now_local.minute) < (
        settings.finalize_local_hour,
        settings.finalize_local_minute,
    ):
        return None

    state = _load_state()
    if state.get("pre_finalized_day") == today.isoformat():
        return None

    result = reconcile_day(
        today,
        settings=settings,
        db=db,
        regions=regions,
        obcine=obcine,
        end_cap_utc=datetime.now(timezone.utc),
        dry_run=dry_run,
    )
    if not dry_run and result.applied:
        state["pre_finalized_day"] = today.isoformat()
        _save_state(state)
    return result


def should_reconcile_today(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
) -> bool:
    """Reconcile le, če arhiv zaostaja za PiP izračun iz vira."""
    settings = settings or get_settings()
    db = db or Database(settings.database_url)
    tz = lj_timezone(settings.timezone)
    today = datetime.now(tz).date()
    previous = db.get_daily_count(today) or 0
    _, aggs, _, _, _ = compute_day(
        today,
        settings=settings,
        regions=regions,
        obcine=obcine,
        end_cap_utc=datetime.now(timezone.utc),
    )
    return aggs.national_daily - previous >= settings.reconcile_min_gap


def hourly_comparison(
    day: date,
    *,
    settings: Settings | None = None,
    regions: RegionIndex | None = None,
    obcine: ObcinaIndex | None = None,
    end_cap_utc: datetime | None = None,
) -> dict:
    """Primerjava urnih agregatov: arhiv (DB) vs StormAPI (izračun)."""
    settings = settings or get_settings()
    db = Database(settings.database_url)
    archive_hourly = {r["ura"]: r["stevilo"] for r in db.query_hourly(day)}
    _, aggs, _, _, _ = compute_day(
        day,
        settings=settings,
        regions=regions,
        obcine=obcine,
        end_cap_utc=end_cap_utc,
    )
    storm_hourly = {r["ura"]: r["stevilo"] for r in hourly_series(aggs)}
    rows = []
    for h in range(24):
        arch = archive_hourly.get(h, 0)
        storm = storm_hourly.get(h, 0)
        rows.append({"ura": h, "arhiv": arch, "stormapi": storm, "razlika": storm - arch})
    return {
        "day": day.isoformat(),
        "arhiv_skupaj": sum(archive_hourly.values()),
        "stormapi_skupaj": sum(storm_hourly.values()),
        "razlika_skupaj": sum(storm_hourly.values()) - sum(archive_hourly.values()),
        "po_urah": rows,
    }
