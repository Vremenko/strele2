"""Začetni uvoz zadnjih ~24 h iz Meteoinfo API v arhiv."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from strele_archive.config import get_settings
from strele_archive.db import Database
from strele_archive.ingest import api_bbox, fetch_strikes, run_once
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = get_settings()
    db = Database(settings.database_url)
    regions = load_regions(settings.regions_geojson)
    obcine = load_obcine(settings.obcine_geojson)
    db.upsert_regije([(r.id, r.name, r.sr_mid) for r in regions.regions])
    db.upsert_obcine([(o.id, o.name, o.ob_mid, o.pov_km2) for o in obcine.obcine])

    bbox = api_bbox(settings, regions)
    logger.info("Območje: statistične regije Slovenije, API bbox=%s", bbox)
    logger.info("Nalagam zadnjih ~24 h udarcev iz %s ...", settings.api_base_url)

    strikes = fetch_strikes(settings, regions)
    if not strikes:
        logger.warning("API ni vrnil udarcev.")
        return

    tz = ZoneInfo(settings.timezone)
    days: Counter[date] = Counter()
    for strike in strikes:
        lat = float(strike["lat"])
        lon = float(strike["lon"])
        if regions.lookup(lon, lat) is None:
            continue
        ts = datetime.fromisoformat(str(strike["ts_utc"]).replace("Z", "+00:00"))
        days[ts.astimezone(tz).date()] += 1

    logger.info("API: %s udarcev v bbox, %s znotraj Slovenije", len(strikes), sum(days.values()))
    for d in sorted(days):
        logger.info("  %s: %s udarcev", d.isoformat(), days[d])

    stats = run_once(settings, db, regions, obcine)
    logger.info(
        "V arhiv shranjeno: %s novih, %s že obstoječih, %s izven Slovenije",
        stats["new"],
        stats["skipped"],
        stats["outside_slovenia"],
    )


if __name__ == "__main__":
    main()
