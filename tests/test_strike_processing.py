"""Testi agregacije in idempotentnosti reconcile logike."""

from __future__ import annotations

import unittest
from collections import Counter
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from strele_archive.strike_processing import (
    ClassifiedStrike,
    DayAggregates,
    aggregate_for_day,
    hourly_series,
)
from strele_archive.day_reconcile import reconcile_day


def _strike(
    lat: float,
    lon: float,
    hour: int,
    regija: int = 1,
    obcina: int | None = 10,
) -> ClassifiedStrike:
    day = date(2026, 7, 11)
    ts = datetime(2026, 7, 11, hour, 30, tzinfo=timezone.utc)
    return ClassifiedStrike(
        lat=lat,
        lon=lon,
        ts_utc=ts,
        local_date=day,
        local_hour=hour,
        regija_id=regija,
        obcina_id=obcina,
    )


class AggregateTest(unittest.TestCase):
    def test_national_regional_obcina_totals(self):
        day = date(2026, 7, 11)
        strikes = [
            _strike(46.0, 14.0, 12, regija=1, obcina=10),
            _strike(46.1, 14.1, 12, regija=1, obcina=10),
            _strike(46.2, 14.2, 13, regija=2, obcina=20),
            _strike(46.3, 14.3, 13, regija=2, obcina=None),
        ]
        aggs = aggregate_for_day(strikes, day)
        self.assertEqual(aggs.national_daily, 4)
        self.assertEqual(aggs.national_hourly[12], 2)
        self.assertEqual(aggs.national_hourly[13], 2)
        self.assertEqual(aggs.regija_daily[1], 2)
        self.assertEqual(aggs.regija_daily[2], 2)
        self.assertEqual(aggs.regija_hourly[(1, 12)], 2)
        self.assertEqual(aggs.obcina_daily[10], 2)
        self.assertEqual(aggs.obcina_daily[20], 1)
        self.assertNotIn(99, aggs.obcina_daily)

    def test_heavy_hour_thousands(self):
        day = date(2026, 7, 11)
        strikes = [
            ClassifiedStrike(
                lat=46.0 + i * 0.0001,
                lon=14.0,
                ts_utc=datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc),
                local_date=day,
                local_hour=20,
                regija_id=(i % 12) + 1,
                obcina_id=(i % 50) + 1,
            )
            for i in range(4500)
        ]
        aggs = aggregate_for_day(strikes, day)
        self.assertEqual(aggs.national_daily, 4500)
        self.assertEqual(aggs.national_hourly[20], 4500)
        self.assertEqual(sum(aggs.regija_daily.values()), 4500)
        self.assertEqual(sum(aggs.obcina_daily.values()), 4500)
        series = hourly_series(aggs)
        self.assertEqual(series[20]["stevilo"], 4500)


class ReconcileIdempotentTest(unittest.TestCase):
    @patch("strele_archive.day_reconcile.compute_day")
    def test_repeated_reconcile_same_totals(self, mock_compute: MagicMock) -> None:
        day = date(2026, 7, 11)
        strikes = [_strike(46.0, 14.0, 14) for _ in range(100)]
        aggs = aggregate_for_day(strikes, day)
        mock_compute.return_value = (strikes, aggs, 100, 0, "udari_24h")

        db = MagicMock()
        db.get_daily_count.return_value = 50

        r1 = reconcile_day(day, db=db, dry_run=False)
        r2 = reconcile_day(day, db=db, dry_run=False)

        self.assertEqual(r1.national_total, 100)
        self.assertEqual(r2.national_total, 100)
        self.assertEqual(db.replace_day_aggregates.call_count, 2)
        for call in db.replace_day_aggregates.call_args_list:
            self.assertEqual(call.args[1].national_daily, 100)


if __name__ == "__main__":
    unittest.main()
