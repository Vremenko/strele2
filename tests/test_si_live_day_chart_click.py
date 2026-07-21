"""Testi živega SI urnega profila in logike izbire dneva na grafu."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from strele_archive.obcina_widget_daily import local_today
from strele_archive.si_live_day import live_si_hourly_for_day


def should_clear_day_after_hourly(daily_count: int | None, hourly_total: int) -> bool:
    """Zrcali logiko selectDay v embed.html / index.html."""
    if daily_count == 0:
        return True
    return hourly_total == 0 and not (daily_count is not None and daily_count > 0)


class DailyChartLastBarSelectionTest(unittest.TestCase):
    def test_keep_selection_when_daily_has_strikes_but_hourly_zero(self):
        """Regresija: klik na današnji živi stolpec ne sme počistiti izbire."""
        self.assertFalse(should_clear_day_after_hourly(500, 0))
        self.assertFalse(should_clear_day_after_hourly(1, 0))

    def test_clear_when_both_zero(self):
        self.assertTrue(should_clear_day_after_hourly(0, 0))

    def test_keep_when_hourly_has_data(self):
        self.assertFalse(should_clear_day_after_hourly(10, 10))
        # daily==0 se v selectDay obravnava prej; po urnem klicu ne pridemo sem z 0.
        self.assertTrue(should_clear_day_after_hourly(0, 0))

    def test_live_hourly_only_for_today(self):
        today = local_today()
        yest = date.fromordinal(today.toordinal() - 1)
        self.assertIsNone(live_si_hourly_for_day(yest))

    def test_live_hourly_replaces_not_adds(self):
        """Živi urni profil nadomesti arhiv (ne prišteje)."""
        today = date(2026, 7, 21)
        now = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
        fake = [(46.0, 14.5, now)] * 3
        with patch("strele_archive.si_live_day.local_today", return_value=today), \
             patch("strele_archive.si_live_day.udari_database_url", return_value="postgresql://x"), \
             patch(
                 "strele_archive.si_live_day.live_today_si_pip_tuples",
                 return_value=fake,
             ):
            hourly = live_si_hourly_for_day(today, now_utc=now)
        self.assertIsNotNone(hourly)
        assert hourly is not None
        total = sum(r["stevilo"] for r in hourly)
        self.assertEqual(total, 3)
        self.assertEqual(len(hourly), 24)


class GetSiHourlyLiveTest(unittest.TestCase):
    def test_get_si_hourly_prefers_live_for_today(self):
        from strele_archive import data_source

        today = date(2026, 7, 21)
        live_rows = [{"ura": h, "stevilo": 1 if h == 12 else 0} for h in range(24)]
        with patch("strele_archive.si_live_day.live_si_hourly_for_day", return_value=live_rows):
            data, source = data_source.get_si_hourly(today)
        self.assertEqual(source, "live")
        self.assertEqual(sum(r["stevilo"] for r in data), 1)


if __name__ == "__main__":
    unittest.main()
