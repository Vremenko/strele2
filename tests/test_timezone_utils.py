"""Testi časovnih oken Europe/Ljubljana (poletni/zimski čas)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from strele_archive.timezone_utils import lj_day_bounds_utc


class LjDayBoundsTest(unittest.TestCase):
    def test_summer_day_july_2026(self):
        day = date(2026, 7, 11)
        start, end = lj_day_bounds_utc(day)
        # 11. 7. 2026 00:00 LJ (CEST, UTC+2) = 21. 7. 2025 22:00 UTC — wait, July is CEST
        # Europe/Ljubljana in July: UTC+2
        self.assertEqual(start, datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc))

    def test_winter_day_january_2026(self):
        day = date(2026, 1, 15)
        start, end = lj_day_bounds_utc(day)
        # CET UTC+1
        self.assertEqual(start, datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc))

    def test_end_cap_for_today(self):
        day = date(2026, 7, 11)
        cap = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        start, end = lj_day_bounds_utc(day, end_cap_utc=cap)
        self.assertEqual(start, datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(end, cap)


if __name__ == "__main__":
    unittest.main()
