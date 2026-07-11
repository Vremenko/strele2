"""Testi PiP štetja za slovenski widget."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from strele_archive.si_widget_counts import bucket_hourly_rolling_24h, filter_pip_strikes


class FakeRegionIndex:
    """Simulira Slovenijo kot kvadrat 46–46.5°N, 14–15°E."""

    def contains(self, lon: float, lat: float) -> bool:
        return 14.0 <= lon <= 15.0 and 46.0 <= lat <= 46.5


class SiWidgetPipTest(unittest.TestCase):
    def test_excludes_bbox_outside_slovenia(self):
        idx = FakeRegionIndex()
        now = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
        rows = [
            (46.2, 14.5, now),          # znotraj SI
            (46.2, 13.5, now),          # v bboxu, vendar zahodno (Hrvaška/jadran)
            (46.2, 15.8, now),          # v bboxu, vendar vzhodno
            (45.8, 14.5, now),          # južno izven SI
        ]
        inside = filter_pip_strikes(rows, idx)
        self.assertEqual(len(inside), 1)
        self.assertEqual(inside[0][1], 14.5)

    def test_hourly_total_matches_pip_count(self):
        idx = FakeRegionIndex()
        now = datetime(2026, 7, 11, 20, 30, tzinfo=timezone.utc)
        rows = [
            (46.1, 14.2, now - timedelta(hours=1)),
            (46.1, 14.3, now - timedelta(hours=1)),
            (46.1, 13.0, now - timedelta(hours=1)),  # izven SI
        ]
        inside = filter_pip_strikes(rows, idx)
        total, _last, hourly = bucket_hourly_rolling_24h(inside, now_utc=now)
        self.assertEqual(total, 2)
        self.assertEqual(sum(h["stevilo"] for h in hourly), 2)


if __name__ == "__main__":
    unittest.main()
