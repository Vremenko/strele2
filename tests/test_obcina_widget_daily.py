"""Testi uskladitve dnevnega grafa občinskega widgeta z live podatki."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from strele_archive.obcina_widget_daily import (
    StormUnavailable,
    apply_live_daily_sync,
    merge_live_today_into_daily,
    parse_storm_hourly_payload,
    recalc_period_total,
    today_count_from_hourly_buckets,
)
from strele_archive.obcine_public_server import _api_obcina_widget_data


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

    def test_obcine_map_live_replaces_not_adds(self):
        """Live za danes se prišteje samo k arhivu BREZ današnjega dne."""
        from strele_archive.obcina_widget_daily import (
            archive_end_excluding_live_today,
            merge_live_today_into_obcine_map_rows,
        )

        today = date(2026, 7, 21)
        self.assertEqual(
            archive_end_excluding_live_today(today, today, today),
            None,
        )
        self.assertEqual(
            archive_end_excluding_live_today(date(2026, 7, 20), today, today),
            date(2026, 7, 20),
        )
        self.assertEqual(
            archive_end_excluding_live_today(date(2026, 7, 19), date(2026, 7, 20), today),
            date(2026, 7, 20),
        )

        archive_rows = [
            {"ob_id": 1, "obcina": "Idrija", "pov_km2": 10.0, "stevilo": 1, "dni_z_nevihto": 1},
            {"ob_id": 2, "obcina": "Medvode", "pov_km2": 20.0, "stevilo": 0, "dni_z_nevihto": 0},
        ]
        live = {2: 12, 1: 0}
        merged = merge_live_today_into_obcine_map_rows(archive_rows, live)
        by_id = {r["ob_id"]: r for r in merged}
        self.assertEqual(by_id[1]["stevilo"], 1)
        self.assertEqual(by_id[1]["dni_z_nevihto"], 1)
        self.assertEqual(by_id[2]["stevilo"], 12)
        self.assertEqual(by_id[2]["dni_z_nevihto"], 1)

        wrongly_included = [
            {"ob_id": 2, "obcina": "Medvode", "pov_km2": 20.0, "stevilo": 5, "dni_z_nevihto": 1},
        ]
        double = merge_live_today_into_obcine_map_rows(wrongly_included, {2: 12})
        self.assertEqual(double[0]["stevilo"], 17)

    def test_daily_chart_live_replaces_partial_archive(self):
        """Enaka zamenjava za dnevni graf (obcina-daily / si-daily)."""
        today = date(2026, 7, 21)
        daily = [
            {"datum": "2026-07-20", "stevilo": 1},
            {"datum": "2026-07-21", "stevilo": 40},  # delni arhiv
        ]
        merged, total, _peak = apply_live_daily_sync(
            daily,
            data_source="live",
            today_live=169,
            today=today,
        )
        self.assertEqual(merged[-1]["stevilo"], 169)
        self.assertEqual(total, 170)
        self.assertNotEqual(total, 1 + 40 + 169)

    def test_rolling_24h_includes_yesterday_before_midnight(self):
        now = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
        payload = {
            "total": 15,
            "groups": [{
                "points": [
                    {"t": "2026-07-10T21:00:00Z", "count": 5},
                    {"t": "2026-07-10T22:00:00Z", "count": 3},
                    {"t": "2026-07-11T18:00:00Z", "count": 7},
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
    def test_api_fallback_uses_udari_when_storm_unavailable(self):
        """StormAPI 403/nedosegljiv → danes iz udari_24h (ne arhiv 0)."""
        archive_daily = [
            {"datum": "2026-07-10", "stevilo": 0},
            {"datum": "2026-07-11", "stevilo": 40},
        ]
        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        muni_inside = [(46.3, 13.5, now - timedelta(hours=1))] * 5

        class FakeOb:
            ob_mid = 11027962
            id = 85
            name = "Novo mesto"
            geometry = None
            prepared = None

        with patch("strele_archive.obcine_public_server._parse_ob_mids", return_value=[11027962]), \
             patch("strele_archive.obcine_public_server._find_obcine", return_value=[FakeOb()]), \
             patch("strele_archive.obcine_public_server._widget_obcina_label", return_value="Novo mesto"), \
             patch("strele_archive.obcine_public_server._obcina_bounds_multi", return_value=[]), \
             patch("strele_archive.obcine_public_server._fetch_daily_calm", return_value=(archive_daily, 40, None)), \
             patch("strele_archive.obcine_public_server._fetch_last_strike_time_multi", return_value="2026-07-11T20:36:00Z"), \
             patch("strele_archive.obcine_public_server._fetch_obcina_live_stats_multi", side_effect=StormUnavailable("403")), \
             patch("strele_archive.obcine_public_server._live_today_count_for_ob_mids", return_value=169), \
             patch("strele_archive.obcine_public_server._muni_rolling_24h_pip_tuples", return_value=muni_inside), \
             patch("strele_archive.obcine_public_server._utc_now", return_value=now), \
             patch("strele_archive.obcine_public_server.local_today", return_value=date(2026, 7, 11)):
            out = _api_obcina_widget_data(ob_mid=11027962, calm_days=30)

        self.assertEqual(out["data_source"], "live")
        self.assertEqual(out["total_24h"], 5)
        self.assertEqual(out["mode"], "storm")
        today_row = [r for r in out["daily"] if r["datum"] == "2026-07-11"][0]
        self.assertEqual(today_row["stevilo"], 169)
        self.assertEqual(out["period_total"], 169)
        self.assertNotEqual(today_row["stevilo"], 40)

    def test_api_live_uses_udari_today_not_storm_today(self):
        """Tudi ob uspešnem StormAPI je današnji stolpec usklajen z udari (zemljevid)."""
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
             patch("strele_archive.obcine_public_server._live_today_count_for_ob_mids", return_value=169), \
             patch("strele_archive.obcine_public_server.local_today", return_value=date(2026, 7, 11)):
            out = _api_obcina_widget_data(ob_mid=11027962, calm_days=30)

        self.assertEqual(out["data_source"], "live")
        self.assertEqual(out["total_24h"], 118)
        today_row = [r for r in out["daily"] if r["datum"] == "2026-07-11"][0]
        self.assertEqual(today_row["stevilo"], 169)
        self.assertNotEqual(today_row["stevilo"], 114)
        self.assertEqual(out["period_total"], 93 + 169)


if __name__ == "__main__":
    unittest.main()
