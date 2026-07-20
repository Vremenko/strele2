"""Testi zaokroževanja ocenjenega časa udara (Europe/Ljubljana, 5 min)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from strele_archive.estimated_strike_time import (
    format_estimated_strike_datetime,
    format_estimated_strike_time,
    round_estimated_strike_datetime,
)

LJ = ZoneInfo("Europe/Ljubljana")


def _utc(y: int, m: int, d: int, h: int, mi: int, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


class EstimatedStrikeTimeRoundTest(unittest.TestCase):
    def test_round_down(self):
        # 12.16 → 12.15 (CEST = UTC+2)
        local = round_estimated_strike_datetime(_utc(2026, 7, 19, 10, 16, 0))
        self.assertEqual((local.hour, local.minute), (12, 15))
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 16, 0)), "12.15")

    def test_round_down_21(self):
        # 12.21 → 12.20
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 21, 0)), "12.20")

    def test_round_up(self):
        # 12.24 → 12.25
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 24, 0)), "12.25")

    def test_round_up_to_next_hour(self):
        # 12.58 → 13.00
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 58, 0)), "13.00")

    def test_on_five_minute_boundary(self):
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 15, 0)), "12.15")
        self.assertEqual(format_estimated_strike_time(_utc(2026, 7, 19, 10, 20, 0)), "12.20")

    def test_midnight_crossover(self):
        # 23:58 CEST → 00.00 naslednji dan
        ts = _utc(2026, 7, 19, 21, 58, 0)
        local = round_estimated_strike_datetime(ts)
        self.assertEqual(local.tzinfo, LJ)
        self.assertEqual((local.year, local.month, local.day), (2026, 7, 20))
        self.assertEqual((local.hour, local.minute), (0, 0))
        self.assertEqual(format_estimated_strike_time(ts), "00.00")
        self.assertEqual(format_estimated_strike_datetime(ts), "20. 7. 2026, 00.00")

    def test_year_crossover(self):
        # 23:58 CET → 00.00 1. 1. 2026
        ts = _utc(2025, 12, 31, 22, 58, 0)
        self.assertEqual(format_estimated_strike_datetime(ts), "1. 1. 2026, 00.00")

    def test_winter_timezone(self):
        # CET UTC+1: 11:16 UTC → 12.16 → 12.15
        local = round_estimated_strike_datetime(_utc(2026, 1, 15, 11, 16, 0))
        self.assertEqual((local.hour, local.minute), (12, 15))

    def test_naive_utc_treated_as_utc(self):
        naive = datetime(2026, 7, 19, 10, 16, 0)
        self.assertEqual(format_estimated_strike_time(naive), "12.15")


if __name__ == "__main__":
    unittest.main()
