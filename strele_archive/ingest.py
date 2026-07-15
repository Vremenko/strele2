"""Inkrementalni ingest udarov iz StormAPI + periodična uskladitev (varovalo)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from strele_archive.config import Settings, get_settings
from strele_archive.day_reconcile import (
    maybe_finalize_previous_day,
    maybe_pre_finalize_today,
    reconcile_today,
    should_reconcile_today,
)
from strele_archive.db import Database
from strele_archive.hourly_reconcile import rebuild_urno_from_dedup
from strele_archive.obcine import ObcinaIndex, load_obcine
from strele_archive.regions import RegionIndex, load_regions
from strele_archive.storm_client import api_bbox, fetch_strikes_window

logger = logging.getLogger(__name__)


def fetch_strikes(
    settings: Settings,
    regions: RegionIndex,
    *,
    time_from_utc: datetime | None = None,
    time_to_utc: datetime | None = None,
) -> list[dict]:
    """
    Pridobi udare v časovnem oknu s paginacijo in časovnimi rezinami.
    Privzeto: zadnjih dedup_retention_hours + 1 ur (prekrivanje za varnost).
    """
    now = datetime.now(timezone.utc)
    if time_to_utc is None:
        time_to_utc = now
    if time_from_utc is None:
        hours = settings.dedup_retention_hours + 1
        time_from_utc = now - timedelta(hours=hours)
    return fetch_strikes_window(settings, regions, time_from_utc, time_to_utc)


def run_once(
    settings: Settings,
    db: Database,
    regions: RegionIndex,
    obcine: ObcinaIndex,
) -> dict:
    tz = ZoneInfo(settings.timezone)
    strikes = fetch_strikes(settings, regions)
    prepared: list[tuple[float, float, datetime, object, int, int, int | None]] = []
    outside = 0
    no_obcina = 0

    for strike in strikes:
        lat = float(strike["lat"])
        lon = float(strike["lon"])
        regija_id = regions.lookup(lon, lat)
        if regija_id is None:
            outside += 1
            continue
        obcina_id = obcine.lookup(lon, lat)
        if obcina_id is None:
            no_obcina += 1
        ts_utc = datetime.fromisoformat(str(strike["ts_utc"]).replace("Z", "+00:00"))
        local = ts_utc.astimezone(tz)
        prepared.append(
            (lat, lon, ts_utc, local.date(), local.hour, regija_id, obcina_id)
        )

    new_count = db.process_new_strikes(prepared)
    deleted = db.cleanup_dedup(settings.dedup_retention_hours)

    return {
        "fetched": len(strikes),
        "inside_slovenia": len(prepared),
        "outside_slovenia": outside,
        "no_obcina": no_obcina,
        "new": new_count,
        "skipped": len(prepared) - new_count,
        "dedup_deleted": deleted,
    }


def run_reconcile_pass(
    settings: Settings,
    db: Database,
    regions: RegionIndex,
    obcine: ObcinaIndex,
    *,
    force: bool = False,
) -> None:
    """
    Varovalo: reconcile tekočega dne le ob zaznanem zaostanku (ali force).
    Zaključek prejšnjega dne iz udari; predpolnočni snapshot tekočega iz udari_24h.
    """
    try:
        if force or should_reconcile_today(
            settings=settings, db=db, regions=regions, obcine=obcine
        ):
            result = reconcile_today(settings=settings, db=db, regions=regions, obcine=obcine)
            logger.info(
                "Reconcile danes (%s): fetched=%s total=%s prev=%s applied=%s",
                result.day.isoformat(),
                result.fetched,
                result.national_total,
                result.previous_daily,
                result.applied,
            )
    except Exception:
        logger.exception("Reconcile tekočega dne ni uspel")

    try:
        pre = maybe_pre_finalize_today(
            settings=settings, db=db, regions=regions, obcine=obcine
        )
        if pre:
            logger.info(
                "Predpolnočni snapshot %s: total=%s",
                pre.day.isoformat(),
                pre.national_total,
            )
    except Exception:
        logger.exception("Predpolnočni snapshot ni uspel")

    try:
        finalized = maybe_finalize_previous_day(
            settings=settings, db=db, regions=regions, obcine=obcine
        )
        if finalized:
            logger.info(
                "Zaključek iz udari %s: total=%s applied=%s",
                finalized.day.isoformat(),
                finalized.national_total,
                finalized.applied,
            )
    except Exception:
        logger.exception("Zaključna uskladitev prejšnjega dne ni uspela")

    try:
        tz = ZoneInfo(settings.timezone)
        today = datetime.now(tz).date()
        fixed = rebuild_urno_from_dedup(today)
        if fixed:
            logger.info("Urn/dnevno uskladitev iz dedup: %s ur", fixed)
    except Exception:
        logger.exception("hourly_reconcile ni uspel")


def run_loop(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    db = Database(settings.database_url)
    regions = load_regions(settings.regions_geojson)
    obcine = load_obcine(settings.obcine_geojson)
    bbox = api_bbox(settings, regions)

    db.upsert_regije([(r.id, r.name, r.sr_mid) for r in regions.regions])
    db.upsert_obcine([(o.id, o.name, o.ob_mid, o.pov_km2) for o in obcine.obcine])
    logger.info(
        "Ingest zagnan (poll=%ss, reconcile=%ss, min_gap=%s, regije=%s, api=%s)",
        settings.poll_interval_sec,
        settings.reconcile_interval_sec,
        settings.reconcile_min_gap,
        len(regions.regions),
        settings.api_base_url,
    )

    last_reconcile_at = 0.0
    while True:
        try:
            stats = run_once(settings, db, regions, obcine)
            logger.info(
                "Poll: fetched=%s inside=%s new=%s skipped=%s",
                stats["fetched"],
                stats["inside_slovenia"],
                stats["new"],
                stats["skipped"],
            )
            now_mono = time.monotonic()
            if now_mono - last_reconcile_at >= settings.reconcile_interval_sec:
                run_reconcile_pass(settings, db, regions, obcine)
                last_reconcile_at = now_mono
        except Exception:
            logger.exception("Poll ni uspel")
        time.sleep(settings.poll_interval_sec)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_loop()


if __name__ == "__main__":
    main()
