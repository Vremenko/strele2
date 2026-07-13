"""Testi za mrežo 1 × 1 km (grid_map)."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from strele_archive.grid_map import (
    _GRID_AGG_SQL,
    build_feature_collection,
    calendar_bounds_utc,
    fetch_grid_map,
    make_cell_id,
    parse_cell_id,
)


class GridMapHelpersTest(unittest.TestCase):
    def test_make_cell_id(self):
        self.assertEqual(make_cell_id(478000, 66000), "3794:478000:66000")

    def test_parse_cell_id(self):
        self.assertEqual(parse_cell_id("3794:478000:66000"), (3794, 478000, 66000))
        self.assertIsNone(parse_cell_id("bad"))

    def test_calendar_bounds_utc(self):
        t0, t1 = calendar_bounds_utc(date(2026, 7, 13), date(2026, 7, 13))
        lj = ZoneInfo("Europe/Ljubljana")
        self.assertEqual(
            t0,
            datetime(2026, 7, 13, 0, 0, tzinfo=lj).astimezone(timezone.utc),
        )
        self.assertEqual(
            t1,
            datetime(2026, 7, 14, 0, 0, tzinfo=lj).astimezone(timezone.utc),
        )

    def test_build_feature_collection_shape(self):
        geom = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[14.7, 45.7], [14.71, 45.7], [14.71, 45.71], [14.7, 45.71], [14.7, 45.7]]],
            }
        )
        rows = [(478000, 66000, 23, 4, 45.7415, 14.7265, geom)]
        out = build_feature_collection(rows, period_from=date(2026, 6, 14), period_to=date(2026, 7, 13))
        self.assertEqual(out["type"], "FeatureCollection")
        self.assertEqual(out["period"]["from"], "2026-06-14")
        self.assertEqual(out["period"]["to"], "2026-07-13")
        feat = out["features"][0]
        self.assertEqual(feat["properties"]["cell_id"], "3794:478000:66000")
        self.assertEqual(feat["properties"]["strike_count"], 23)
        self.assertEqual(feat["properties"]["storm_days"], 4)
        self.assertEqual(feat["properties"]["center_lat"], 45.7415)
        self.assertEqual(feat["properties"]["center_lon"], 14.7265)

    def test_sql_contains_postgis_primitives(self):
        self.assertIn("ST_SnapToGrid", _GRID_AGG_SQL)
        self.assertIn("ST_Transform", _GRID_AGG_SQL)
        self.assertIn("%(grid_crs)s", _GRID_AGG_SQL)
        self.assertIn("%(grid_size)s", _GRID_AGG_SQL)


class GridMapFetchTest(unittest.TestCase):
    def test_fetch_grid_map_executes_single_query(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        fake_row = (
            1000,
            2000,
            5,
            2,
            46.1,
            14.8,
            json.dumps({"type": "Polygon", "coordinates": [[[14.8, 46.1], [14.81, 46.1], [14.81, 46.11], [14.8, 46.11], [14.8, 46.1]]]}),
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [fake_row]
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("strele_archive.grid_map.psycopg.connect", return_value=mock_conn):
            out = fetch_grid_map(
                start=date(2026, 7, 1),
                end=date(2026, 7, 7),
                data_dir=data_dir,
                database_url="postgresql://example",
            )

        mock_cursor.execute.assert_called_once()
        self.assertEqual(len(out["features"]), 1)
        self.assertEqual(out["features"][0]["properties"]["strike_count"], 5)


class GridMapEndpointTest(unittest.TestCase):
    def test_api_grid_map_registered(self):
        from strele_archive.obcine_public_server import app

        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn("/api/grid-map", paths)


if __name__ == "__main__":
    unittest.main()
