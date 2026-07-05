"""Enkratni poll (brez zanke) — za testiranje."""

from __future__ import annotations

import logging

from strele_archive.config import get_settings
from strele_archive.db import Database
from strele_archive.ingest import run_once
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions


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
    stats = run_once(settings, db, regions, obcine)
    logging.info("Končano: %s", stats)


if __name__ == "__main__":
    main()
