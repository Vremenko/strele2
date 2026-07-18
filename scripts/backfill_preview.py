#!/usr/bin/env python3
"""Enkratni backfill — pred izvedbo prikaže primerjavo arhiv vs StormAPI."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone

from strele_archive.config import get_settings
from strele_archive.day_reconcile import compute_day_from_stormapi, hourly_comparison, reconcile_day
from strele_archive.db import Database
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions
from strele_archive.timezone_utils import lj_timezone

logger = logging.getLogger(__name__)


def _print_comparison(label: str, comp: dict) -> None:
    print(f"\n=== {label} ({comp['day']}) ===")
    print(f"Arhiv skupaj:    {comp['arhiv_skupaj']:,}")
    print(f"StormAPI skupaj: {comp['stormapi_skupaj']:,}")
    print(f"Razlika:         {comp['razlika_skupaj']:+,}")
    print("\nUra | Arhiv | StormAPI | Razlika")
    print("----|-------|----------|--------")
    for row in comp["po_urah"]:
        if row["arhiv"] or row["stormapi"]:
            print(
                f"{row['ura']:>3} | {row['arhiv']:>5} | {row['stormapi']:>8} | {row['razlika']:>+7}"
            )


def _projected_after(comp: dict) -> dict:
    """Po reconcile bi arhiv ustrezal StormAPI (suhi zagon)."""
    projected = dict(comp)
    projected["arhiv_skupaj"] = comp["stormapi_skupaj"]
    projected["razlika_skupaj"] = 0
    projected["po_urah"] = [
        {**row, "arhiv": row["stormapi"], "razlika": 0} for row in comp["po_urah"]
    ]
    return projected


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill preview / apply za izbrane dni")
    parser.add_argument(
        "days",
        nargs="+",
        help="Datumi YYYY-MM-DD (npr. 2026-07-10 2026-07-11)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Dejansko zapiši uskladitev v bazo (privzeto: samo preview)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Izpis v JSON",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    regions = load_regions(settings.regions_geojson)
    obcine = load_obcine(settings.obcine_geojson)
    db = Database(settings.database_url)

    end_cap = datetime.now(timezone.utc)
    today_lj = end_cap.astimezone(lj_timezone(settings.timezone)).date()
    report: list[dict] = []

    for day_str in args.days:
        day = date.fromisoformat(day_str)
        cap = end_cap if day == today_lj else None
        before = hourly_comparison(
            day,
            settings=settings,
            regions=regions,
            obcine=obcine,
            end_cap_utc=cap,
        )

        _, aggs, fetched, outside = compute_day_from_stormapi(
            day,
            settings=settings,
            regions=regions,
            obcine=obcine,
            end_cap_utc=cap,
        )

        after = _projected_after(before)
        entry = {
            "day": day.isoformat(),
            "pred": before,
            "po_reconcile": after,
            "stormapi_meta": {
                "fetched_raw": fetched,
                "inside_classified": aggs.national_daily,
                "outside": outside,
                "regije": len(aggs.regija_daily),
                "obcine": len(aggs.obcina_daily),
            },
            "applied": False,
        }

        if args.json:
            report.append(entry)
        else:
            _print_comparison(f"PRED — {day.isoformat()}", before)
            _print_comparison(f"PO (projekcija) — {day.isoformat()}", after)
            print(
                f"\nStormAPI surovi: fetched={fetched}, znotraj SI={aggs.national_daily}, "
                f"izven={outside}, regije={len(aggs.regija_daily)}, občine={len(aggs.obcina_daily)}"
            )

        if args.apply:
            print(f"\n>>> Usklajujem {day.isoformat()} ...")
            result = reconcile_day(
                day,
                settings=settings,
                db=db,
                regions=regions,
                obcine=obcine,
                end_cap_utc=cap,
            )
            entry["applied"] = result.applied
            if not args.json:
                print(
                    f"Končano: prev={result.previous_daily} -> new={result.national_total} "
                    f"(fetched={result.fetched})"
                )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    if not args.apply:
        print("\n*** Backfill NI bil izveden (brez --apply). ***")
        print(
            "Opomba: StormAPI /strele bere le strele.udari_24h (~24 h). "
            "Celoten zgodovinski dan (>24 h nazaj) ni vedno obnovljiv iz surovih udarov."
        )


if __name__ == "__main__":
    main()
