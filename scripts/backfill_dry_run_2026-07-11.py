#!/usr/bin/env python3
"""Dry-run backfill za 11. 7. 2026 — PiP Slovenija, brez pisanja v bazo."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from strele_archive.config import get_settings
from strele_archive.day_reconcile import compute_day, hourly_comparison
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions
from strele_archive.timezone_utils import lj_timezone


def main() -> None:
    day = date(2026, 7, 11)
    settings = get_settings()
    regions = load_regions(settings.regions_geojson)
    obcine = load_obcine(settings.obcine_geojson)
    end_cap = datetime.now(timezone.utc)
    today_lj = end_cap.astimezone(lj_timezone(settings.timezone)).date()
    cap = end_cap if day == today_lj else None

    before = hourly_comparison(
        day, settings=settings, regions=regions, obcine=obcine, end_cap_utc=cap
    )
    _, aggs, fetched, outside, source = compute_day(
        day,
        settings=settings,
        regions=regions,
        obcine=obcine,
        end_cap_utc=cap,
    )

    print(f"=== Dry-run backfill {day.isoformat()} (PiP Slovenija) ===")
    print(f"Vir:              {source}")
    print(f"Arhiv (PRED):     {before['arhiv_skupaj']:,}")
    print(f"PiP izračun:      {aggs.national_daily:,}")
    print(f"Razlika:          {aggs.national_daily - before['arhiv_skupaj']:+,}")
    print(f"Surovi (bbox):    {fetched:,}")
    print(f"Izven SI (PiP):   {outside:,}")
    print()
    print("Ura | Arhiv | PiP   | Δ")
    print("----|-------|-------|------")
    arch_by_hour = {r["ura"]: r["arhiv"] for r in before["po_urah"]}
    for h in range(24):
        arch = arch_by_hour.get(h, 0)
        pip = aggs.national_hourly.get(h, 0)
        if arch or pip:
            print(f"{h:>3} | {arch:>5} | {pip:>5} | {pip - arch:>+5}")
    print()
    print("*** Backfill NI bil izveden. ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
