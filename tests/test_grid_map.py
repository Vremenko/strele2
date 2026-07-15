"""Testi za mrežo 1 × 1 km (grid_map)."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from strele_archive.grid_cache_job import CACHE_DAYS, rebuild_cache_files, run_job
from strele_archive.grid_map import (
    _CACHE_VERSION,
    _DAILY_TABLE,
    _GRID_AGG_SQL,
    GRID_CELL_DAILY_RADIUS_KM,
    _RADIUS_DAILY_SQL,
    build_cached_feature_collection,
    build_feature_collection,
    build_today_cached_feature_collection,
    calendar_bounds_utc,
    fetch_grid_cell_daily,
    fetch_grid_map,
    fetch_grid_map_from_daily,
    make_cell_id,
    parse_cell_id,
    rebuild_grid_daily_aggregates,
    resolve_grid_cell,
    today_cache_basename,
)


class GridMapHelpersTest(unittest.TestCase):
    def test_make_cell_id(self):
        self.assertEqual(make_cell_id(478000, 66000), "3794:478000:66000")

    def test_parse_cell_id(self):
        self.assertEqual(parse_cell_id("3794:478000:66000"), (3794, 478000, 66000))
        self.assertIsNone(parse_cell_id("bad"))

    def test_today_cache_basename(self):
        self.assertEqual(today_cache_basename(), "grid-map-today.json")

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

    def test_build_feature_collection_density_only(self):
        geom = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[14.7, 45.7], [14.71, 45.7], [14.71, 45.71], [14.7, 45.71], [14.7, 45.7]]],
            }
        )
        rows = [(478000, 66000, 23, geom)]
        out = build_feature_collection(rows, period_from=date(2026, 6, 14), period_to=date(2026, 7, 13))
        feat = out["features"][0]
        self.assertEqual(feat["properties"]["strike_count"], 23)
        self.assertNotIn("storm_days", feat["properties"])
        self.assertNotIn("center_lat", feat["properties"])

    def test_sql_contains_postgis_primitives(self):
        self.assertIn("ST_SnapToGrid", _GRID_AGG_SQL)
        self.assertIn("ST_Transform", _GRID_AGG_SQL)


class GridMapFetchTest(unittest.TestCase):
    def test_fetch_grid_map_from_daily_executes_daily_query(self):
        geom = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[14.8, 46.1], [14.81, 46.1], [14.81, 46.11], [14.8, 46.11], [14.8, 46.1]]],
            }
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1000, 2000, 5, geom)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        out = fetch_grid_map_from_daily(mock_conn, start=date(2026, 7, 1), end=date(2026, 7, 7))
        sql = mock_cursor.execute.call_args[0][0]
        self.assertIn(_DAILY_TABLE, sql)
        self.assertNotIn("udari", sql)
        self.assertEqual(out["features"][0]["properties"]["strike_count"], 5)

    def test_fetch_grid_map_executes_raw_query(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        fake_row = (
            1000,
            2000,
            5,
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
        self.assertEqual(len(out["features"]), 1)


class GridMapDailyUpsertTest(unittest.TestCase):
    def test_rebuild_daily_aggregates_no_storm_table(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        rebuild_grid_daily_aggregates(mock_conn, day_from=date(2026, 7, 1), day_to=date(2026, 7, 2))
        self.assertEqual(mock_cursor.execute.call_count, 2)
        all_sql = "\n\n".join(call[0][0] for call in mock_cursor.execute.call_args_list)
        self.assertIn("INSERT INTO lightning_grid_1km_daily", all_sql)
        self.assertNotIn("lightning_grid_1km_storm_daily", all_sql)
        self.assertNotIn("ST_DWithin", all_sql)


class GridMapCachedBuildTest(unittest.TestCase):
    def _fake_row(self):
        return (
            1000,
            2000,
            5,
            json.dumps(
                {
                    "type": "Polygon",
                    "coordinates": [[[14.8, 46.1], [14.81, 46.1], [14.81, 46.11], [14.8, 46.11], [14.8, 46.1]]],
                }
            ),
        )

    def test_cached_feature_collection_density_metadata(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [self._fake_row()]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        out = build_cached_feature_collection(
            mock_conn,
            start=date(2026, 7, 1),
            end=date(2026, 7, 7),
            days=7,
        )
        self.assertTrue(out.get("cached"))
        self.assertEqual(out.get("cache_version"), _CACHE_VERSION)
        self.assertNotIn("storm_radius_km", out)
        self.assertNotIn("storm_days", out["features"][0]["properties"])

    def test_today_cached_feature_collection(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [self._fake_row()]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        out = build_today_cached_feature_collection(mock_conn, today_local=date(2026, 7, 15))
        self.assertEqual(out.get("days"), 1)
        self.assertEqual(out.get("from"), "2026-07-15")
        self.assertEqual(out.get("to"), "2026-07-15")


class GridCacheJobTest(unittest.TestCase):
    def test_rebuild_cache_files_writes_today_and_periods(self):
        import tempfile

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            rebuild_cache_files(mock_conn, cache_dir=cache_dir, today_local=date(2026, 7, 15))
            self.assertTrue((cache_dir / today_cache_basename()).exists())
            for d in CACHE_DAYS:
                self.assertTrue((cache_dir / f"grid-map-{d}.json").exists())
                payload = json.loads((cache_dir / f"grid-map-{d}.json").read_text(encoding="utf-8"))
                self.assertEqual(payload.get("cache_version"), _CACHE_VERSION)
                self.assertNotIn("storm_radius_km", payload)

    def test_job_skips_when_no_new_strikes_same_day(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            meta = cache_dir / "grid-cache-meta.json"
            meta.write_text(
                json.dumps({"last_max_ts_utc": "2026-07-15T10:00:00Z", "last_local_day": "2026-07-15"}),
                encoding="utf-8",
            )
            mock_conn = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
            mock_cursor.fetchone.side_effect = [
                (True,),  # advisory lock
                (datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),),  # max ts
                (True,),  # unlock
            ]

            with patch("strele_archive.grid_cache_job._cache_dir", return_value=cache_dir):
                with patch("strele_archive.grid_cache_job._now_local_day", return_value=date(2026, 7, 15)):
                    with patch("strele_archive.grid_cache_job.psycopg.connect", return_value=mock_conn):
                        with patch("strele_archive.grid_cache_job.rebuild_cache_files") as rebuild_mock:
                            code = run_job(database_url="postgresql://example")
            self.assertEqual(code, 0)
            rebuild_mock.assert_not_called()


class GridMapEndpointTest(unittest.TestCase):
    def _import_server(self):
        try:
            import strele_archive.obcine_public_server as srv
        except ModuleNotFoundError:
            self.skipTest("obcine_public_server optional deps not installed")
        return srv

    def test_map_embed_has_grid_cell_daily_and_zero_tooltip(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "public" / "map-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("gridZeroTooltipHtml", html)
        self.assertIn("/api/grid-cell-daily", html)
        self.assertIn("0 strel / km²", html)
        self.assertIn("Celica 1 × 1 km — radij 10 km", html)
        self.assertNotIn("data-radius-km", html)

    def test_map_embed_has_no_grid_storm_mode_button(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "public" / "map-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('id="mapModeBtnDni"', html)
        gridSection = html.split("loadGridData")[1].split("async function reload")[0]
        self.assertNotIn("storm_days", gridSection)

    def test_api_grid_map_today_cache_path(self):
        import tempfile
        from starlette.requests import Request

        srv = self._import_server()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            payload = {
                "type": "FeatureCollection",
                "features": [],
                "cached": True,
                "cache_version": _CACHE_VERSION,
                "from": "2026-07-15",
                "to": "2026-07-15",
            }
            (cache_dir / today_cache_basename()).write_text(json.dumps(payload) + "\n", encoding="utf-8")
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]

            with patch.object(srv, "fetch_grid_map_from_daily", side_effect=AssertionError("daily read called")):
                req = Request({"type": "http", "headers": []})
                res = srv.api_grid_map(
                    req,
                    from_=None,
                    to_=None,
                    day=None,
                    days=None,
                    today=True,
                    min_lon=None,
                    min_lat=None,
                    max_lon=None,
                    max_lat=None,
                )
            self.assertEqual(res.status_code, 200)

    def test_api_grid_map_single_day_uses_daily_table(self):
        from starlette.requests import Request

        srv = self._import_server()
        req = Request({"type": "http", "headers": []})
        fake = {"type": "FeatureCollection", "features": [], "cached": False}
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        with patch.object(srv, "fetch_grid_map_from_daily", return_value=fake) as daily_mock:
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    out = srv.api_grid_map(
                        req,
                        from_=None,
                        to_=None,
                        day=date(2026, 6, 1),
                        days=None,
                        today=False,
                        min_lon=None,
                        min_lat=None,
                        max_lon=None,
                        max_lat=None,
                    )
        daily_mock.assert_called_once()
        self.assertEqual(out["cached"], False)

    def test_api_grid_map_rolling_cache_no_raw_aggregation(self):
        import tempfile
        from starlette.requests import Request

        srv = self._import_server()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            (cache_dir / "grid-map-7.json").write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [],
                        "cached": True,
                        "cache_version": _CACHE_VERSION,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]
            with patch.object(srv, "fetch_grid_map_from_daily", side_effect=AssertionError("raw called")):
                req = Request({"type": "http", "headers": []})
                res = srv.api_grid_map(
                    req,
                    from_=None,
                    to_=None,
                    day=None,
                    days=7,
                    today=False,
                    min_lon=None,
                    min_lat=None,
                    max_lon=None,
                    max_lat=None,
                )
            self.assertEqual(res.status_code, 200)

    def test_api_grid_map_rejects_old_cache_version(self):
        import tempfile
        from starlette.requests import Request
        from fastapi import HTTPException

        srv = self._import_server()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            (cache_dir / "grid-map-7.json").write_text(
                json.dumps({"type": "FeatureCollection", "features": [], "cached": True, "cache_version": 3})
                + "\n",
                encoding="utf-8",
            )
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]
            req = Request({"type": "http", "headers": []})
            with self.assertRaises(HTTPException) as ctx:
                srv.api_grid_map(
                    req,
                    from_=None,
                    to_=None,
                    day=None,
                    days=7,
                    today=False,
                    min_lon=None,
                    min_lat=None,
                    max_lon=None,
                    max_lat=None,
                )
            self.assertEqual(ctx.exception.status_code, 503)

    def test_api_grid_map_etag_304(self):
        import tempfile
        from starlette.requests import Request

        srv = self._import_server()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            p = cache_dir / today_cache_basename()
            p.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [],
                        "cached": True,
                        "cache_version": _CACHE_VERSION,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]
            etag = srv._grid_cache_etag(p)
            req = Request({"type": "http", "headers": [(b"if-none-match", etag.encode("latin-1"))]})
            res = srv.api_grid_map(
                req,
                from_=None,
                to_=None,
                day=None,
                days=None,
                today=True,
                min_lon=None,
                min_lat=None,
                max_lon=None,
                max_lat=None,
            )
            self.assertEqual(res.status_code, 304)


class GridCellDailyTest(unittest.TestCase):
    def test_grid_cell_daily_radius_fixed(self):
        self.assertEqual(GRID_CELL_DAILY_RADIUS_KM, 10)

    def test_radius_daily_sql_uses_local_bbox(self):
        self.assertIn("ST_Buffer", _RADIUS_DAILY_SQL)
        self.assertIn("ST_Envelope", _RADIUS_DAILY_SQL)
        self.assertNotIn("bounds AS", _RADIUS_DAILY_SQL)

    def test_resolve_grid_cell_outside_slovenia(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1000, 2000, "{}", 46.0, 14.5, False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        out = resolve_grid_cell(mock_conn, lat=36.0, lon=14.5)
        self.assertIsNone(out)
        sql = mock_cursor.execute.call_args[0][0]
        self.assertIn("SnapToGrid", sql)

    def test_fetch_grid_cell_daily_shape(self):
        geom = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[14.8, 46.1], [14.81, 46.1], [14.81, 46.11], [14.8, 46.11], [14.8, 46.1]]],
            }
        )
        resolve_row = (478000, 66000, geom, 46.05, 14.5, True)
        daily_rows = [(date(2026, 7, 9), 0), (date(2026, 7, 10), 3)]

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = resolve_row
        mock_cursor.fetchall.return_value = daily_rows
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("strele_archive.grid_map._slovenia_wkt", return_value="POLYGON((0 0,1 0,1 1,0 1,0 0))"):
            out = fetch_grid_cell_daily(
                mock_conn,
                lat=46.05,
                lon=14.5,
                start=date(2026, 7, 9),
                end=date(2026, 7, 10),
            )

        self.assertIsNotNone(out)
        self.assertEqual(out["cell"]["cell_id"], make_cell_id(478000, 66000))
        self.assertEqual(out["from"], "2026-07-09")
        self.assertEqual(out["to"], "2026-07-10")
        self.assertEqual(len(out["series"]), 1)
        self.assertEqual(out["series"][0]["radius_km"], 10)
        self.assertEqual(out["series"][0]["total"], 3)
        self.assertEqual(out["series"][0]["daily"][1]["stevilo"], 3)


class GridCellDailyEndpointTest(unittest.TestCase):
    def _import_server(self):
        try:
            import strele_archive.obcine_public_server as srv
        except ModuleNotFoundError:
            self.skipTest("obcine_public_server optional deps not installed")
        return srv

    def test_api_grid_cell_daily_success(self):
        srv = self._import_server()
        fake = {
            "cell": {
                "cell_id": "3794:478000:66000",
                "center_lat": 46.05,
                "center_lon": 14.5,
                "geometry": {"type": "Polygon", "coordinates": []},
            },
            "from": "2026-07-09",
            "to": "2026-07-15",
            "series": [{"radius_km": 10, "total": 0, "daily": []}],
        }
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        with patch.object(srv, "fetch_grid_cell_daily", return_value=fake) as fetch_mock:
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    out = srv.api_grid_cell_daily(
                        lat=46.05,
                        lon=14.5,
                        from_=date(2026, 7, 9),
                        to_=date(2026, 7, 15),
                    )
        fetch_mock.assert_called_once()
        self.assertEqual(out["cell"]["cell_id"], "3794:478000:66000")

    def test_api_grid_cell_daily_not_in_slovenia(self):
        from fastapi import HTTPException

        srv = self._import_server()
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        with patch.object(srv, "fetch_grid_cell_daily", return_value=None):
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    with self.assertRaises(HTTPException) as ctx:
                        srv.api_grid_cell_daily(
                            lat=36.0,
                            lon=14.5,
                            from_=date(2026, 7, 9),
                            to_=date(2026, 7, 15),
                        )
        self.assertEqual(ctx.exception.status_code, 404)


    def test_api_grid_cell_daily_90_days_ok(self):
        srv = self._import_server()
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        with patch.object(srv, "fetch_grid_cell_daily", return_value={"cell": {}, "series": []}) as fetch_mock:
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    srv.api_grid_cell_daily(
                        lat=46.05,
                        lon=14.5,
                        from_=date(2026, 7, 1),
                        to_=date(2026, 9, 28),
                    )
        fetch_mock.assert_called_once()

    def test_api_grid_cell_daily_91_days_rejected(self):
        from fastapi import HTTPException

        srv = self._import_server()
        with self.assertRaises(HTTPException) as ctx:
            srv.api_grid_cell_daily(
                lat=46.05,
                lon=14.5,
                from_=date(2026, 7, 1),
                to_=date(2026, 9, 29),
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_api_grid_cell_daily_reversed_period(self):
        from fastapi import HTTPException

        srv = self._import_server()
        with self.assertRaises(HTTPException) as ctx:
            srv.api_grid_cell_daily(
                lat=46.05,
                lon=14.5,
                from_=date(2026, 7, 15),
                to_=date(2026, 7, 1),
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Neveljavno", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
