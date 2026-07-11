"""Testi uskladitve dnevnega grafa občinskega widgeta z live StormAPI podatki."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from strele_archive.obcina_widget_daily import (
    StormUnavailable,
    apply_live_daily_sync,
    local_today,
    merge_live_today_into_daily,
    parse_storm_hourly_payload,
    recalc_period_total,
    today_count_from_hourly_buckets,
)
from strele_archive.obcine_public_server import (
    _fetch_obcina_live_stats_multi,
    api_obcina_widget,
)


class ObcinaWidgetDailyTest(unittest.TestCase):
    def test_live_today_replaces_stale_archive(self):
        today = date(2026, 7, 11)
        daily = [
            {"datum": "2026-07-10", "stevilo": 5},
            {"datum": "2026-07-11", "stevilo": 64},
        ]
        merged, total, peak = apply_live_daily_sync(
            daily,
            data_source="live",
            today_live=114,
            today=today,
        )
        self.assertEqual(merged[-1]["stevilo"], 114)
        self.assertEqual(total, 119)
        self.assertEqual(peak, {"datum": "2026-07-11", "stevilo": 114})

    def test_rolling_24h_includes_yesterday_before_midnight(self):
        now = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
        payload = {
            "total": 15,
            "groups": [{
                "points": [
                    {"t": "2026-07-10T21:00:00Z", "count": 5},  # 23:00 Lj 10.7.
                    {"t": "2026-07-10T22:00:00Z", "count": 3},  # 00:00 Lj 11.7.
                    {"t": "2026-07-11T18:00:00Z", "count": 7},  # 20:00 Lj 11.7.
                ],
            }],
        }
        stats = parse_storm_hourly_payload(payload, now_utc=now)
        self.assertEqual(stats.total_24h, 15)
        self.assertEqual(stats.today_from_midnight, 10)
        self.assertNotEqual(stats.total_24h, stats.today_from_midnight)

    def test_today_count_from_hourly_buckets(self):
        by_hour = {
            "2026-07-10T23:00:00": 5,
            "2026-07-11T00:00:00": 3,
            "2026-07-11T20:00:00": 7,
        }
        self.assertEqual(
            today_count_from_hourly_buckets(by_hour, today=date(2026, 7, 11)),
            10,
        )

    def test_recalc_period_total_matches_daily_sum(self):
        daily = merge_live_today_into_daily(
            [
                {"datum": "2026-06-12", "stevilo": 0},
                {"datum": "2026-07-10", "stevilo": 93},
                {"datum": "2026-07-11", "stevilo": 64},
            ],
            date(2026, 7, 11),
            114,
        )
        self.assertEqual(recalc_period_total(daily), 207)

    def test_archive_fallback_does_not_zero_values(self):
        today = date(2026, 7, 11)
        daily = [
            {"datum": "2026-07-10", "stevilo": 0},
            {"datum": "2026-07-11", "stevilo": 99},
        ]
        merged, total, _peak = apply_live_daily_sync(
            daily,
            data_source="archive_fallback",
            today_live=99,
            today=today,
        )
        self.assertEqual(merged[-1]["stevilo"], 99)
        self.assertEqual(total, 99)


class ObcinaWidgetApiFallbackTest(unittest.TestCase):
    def test_api_fallback_on_storm_unavailable(self):
        archive_daily = [
            {"datum": "2026-07-10", "stevilo": 0},
            {"datum": "2026-07-11", "stevilo": 99},
        ]

        class FakeOb:
            ob_mid = 11027962
            id = 85
            name = "Novo mesto"
            geometry = None

        fake_ob = FakeOb()

        with patch("strele_archive.obcine_public_server._parse_ob_mids", return_value=[11027962]), \
             patch("strele_archive.obcine_public_server._find_obcine", return_value=[fake_ob]), \
             patch("strele_archive.obcine_public_server._widget_obcina_label", return_value="Novo mesto"), \
             patch("strele_archive.obcine_public_server._obcina_bounds_multi", return_value=[]), \
             patch("strele_archive.obcine_public_server._fetch_daily_calm", return_value=(archive_daily, 192, None)), \
             patch("strele_archive.obcine_public_server._fetch_last_strike_time_multi", return_value="2026-07-11T20:36:00Z"), \
             patch("strele_archive.obcine_public_server._fetch_obcina_live_stats_multi", side_effect=StormUnavailable("429")), \
             patch("strele_archive.obcine_public_server._fetch_strikes_24h_multi", return_value=[]), \
             patch("strele_archive.obcine_public_server.local_today", return_value=date(2026, 7, 11)):
            out = api_obcina_widget(ob_mid=11027962, title=None, calm_days=30)

        self.assertEqual(out["data_source"], "archive_fallback")
        self.assertEqual(out["total_24h"], 99)
        self.assertEqual(out["mode"], "storm")
        today_row = [r for r in out["daily"] if r["datum"] == "2026-07-11"][0]
        self.assertEqual(today_row["stevilo"], 99)
        self.assertEqual(out["period_total"], recalc_period_total(out["daily"]))

    def test_api_live_syncs_today_and_period_total(self):
        archive_daily = [
            {"datum": "2026-07-10", "stevilo": 93},
            {"datum": "2026-07-11", "stevilo": 64},
        ]
        live_stats = type("S", (), {
            "total_24h": 118,
            "last_hour": 2,
            "hourly": [],
            "today_from_midnight": 114,
        })()

        class FakeOb:
            ob_mid = 11027962
            id = 85
            name = "Novo mesto"
            geometry = None

        with patch("strele_archive.obcine_public_server._parse_ob_mids", return_value=[11027962]), \
             patch("strele_archive.obcine_public_server._find_obcine", return_value=[FakeOb()]), \
             patch("strele_archive.obcine_public_server._widget_obcina_label", return_value="Novo mesto"), \
             patch("strele_archive.obcine_public_server._obcina_bounds_multi", return_value=[]), \
             patch("strele_archive.obcine_public_server._fetch_daily_calm", return_value=(archive_daily, 157, None)), \
             patch("strele_archive.obcine_public_server._fetch_last_strike_time_multi", return_value="2026-07-11T20:36:00Z"), \
             patch("strele_archive.obcine_public_server._fetch_obcina_live_stats_multi", return_value=live_stats), \
             patch("strele_archive.obcine_public_server._fetch_strikes_24h_multi", return_value=[]), \
             patch("strele_archive.obcine_public_server.local_today", return_value=date(2026, 7, 11)):
            out = api_obcina_widget(ob_mid=11027962, title=None, calm_days=30)

        self.assertEqual(out["data_source"], "live")
        self.assertEqual(out["total_24h"], 118)
        today_row = [r for r in out["daily"] if r["datum"] == "2026-07-11"][0]
        self.assertEqual(today_row["stevilo"], 114)
        self.assertNotEqual(out["total_24h"], today_row["stevilo"])
        self.assertEqual(out["period_total"], 93 + 114)


if __name__ == "__main__":
    unittest.main()
