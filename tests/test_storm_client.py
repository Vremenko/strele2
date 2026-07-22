"""Testi paginacije in časovnih rez StormAPI odjemalca."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from strele_archive.config import Settings
from strele_archive.regions import Region, RegionIndex
from shapely.geometry import Polygon

from strele_archive import storm_client


def _fake_settings() -> Settings:
    return Settings(
        database_url="postgresql://x",
        api_base_url="http://stormapi.test",
        strele_api_key="test-strele-key",
        poll_interval_sec=300,
        regions_geojson=__import__("pathlib").Path("/tmp"),
        obcine_geojson=__import__("pathlib").Path("/tmp"),
        min_lat=float("nan"),
        max_lat=float("nan"),
        min_lon=float("nan"),
        max_lon=float("nan"),
        timezone="Europe/Ljubljana",
        dedup_retention_hours=26,
        bbox_padding_deg=0.02,
        reconcile_interval_sec=900,
        reconcile_min_gap=50,
        finalize_local_hour=23,
        finalize_local_minute=50,
        finalize_retry_until_hour=12,
    )


def _fake_regions() -> RegionIndex:
    poly = Polygon([(13, 45), (14, 45), (14, 47), (13, 47)])
    region = Region(id=1, name="Test", sr_mid=1, geometry=poly, prepared=MagicMock())
    return RegionIndex([region])


class StormClientPaginationTest(unittest.TestCase):
    @patch("strele_archive.storm_client.requests.get")
    def test_interval_fetches_multiple_pages(self, mock_get: MagicMock) -> None:
        settings = _fake_settings()
        bbox = {"min_lat": 45.0, "max_lat": 47.0, "min_lon": 13.0, "max_lon": 16.0}
        t0 = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 7, 11, 11, 0, tzinfo=timezone.utc)

        page1 = [
            {"lat": 46.0, "lon": 14.0, "ts_utc": "2026-07-11T10:00:00Z"},
            {"lat": 46.1, "lon": 14.1, "ts_utc": "2026-07-11T10:01:00Z"},
        ]
        page2 = [
            {"lat": 46.2, "lon": 14.2, "ts_utc": "2026-07-11T10:02:00Z"},
        ]

        def side_effect(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if params.get("offset", 0) == 0:
                resp.json.return_value = page1
            else:
                resp.json.return_value = page2
            return resp

        mock_get.side_effect = side_effect

        strikes, paginated = storm_client.fetch_strikes_page(
            settings, bbox, time_from_utc=t0, time_to_utc=t1, limit=2
        )
        self.assertEqual(len(strikes), 2)
        self.assertTrue(paginated)
        self.assertTrue(mock_get.called)
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs.get("headers", {}).get("X-Strele-Key"), "test-strele-key")

        mock_interval = MagicMock()
        with patch("strele_archive.storm_client.fetch_strikes_page") as mock_page:
            mock_page.side_effect = [
                (page1, True),
                (page2, True),
            ]
            strikes = storm_client.fetch_strikes_interval(
                settings, bbox, t0, t1, page_size=2
            )
        self.assertEqual(len(strikes), 3)

    @patch("strele_archive.storm_client.fetch_strikes_interval")
    def test_window_deduplicates_overlapping_chunks(self, mock_interval: MagicMock) -> None:
        settings = _fake_settings()
        regions = _fake_regions()
        dup = {"lat": 46.0, "lon": 14.0, "ts_utc": "2026-07-11T10:30:00Z"}
        extra = {"lat": 46.1, "lon": 14.1, "ts_utc": "2026-07-11T10:45:00Z"}
        mock_interval.side_effect = [[dup], [dup, extra], [extra]]

        t0 = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 7, 11, 11, 0, tzinfo=timezone.utc)
        strikes = storm_client.fetch_strikes_window(
            settings, regions, t0, t1, chunk_minutes=30, overlap_seconds=300
        )
        self.assertEqual(len(strikes), 2)


if __name__ == "__main__":
    unittest.main()
