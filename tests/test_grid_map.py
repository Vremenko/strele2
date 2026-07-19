"""Testi za mrežo 1 × 1 km (grid_map)."""

from __future__ import annotations

import json
import os
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


def _allow_grid_podpornik(srv):
    """Obstoječi endpoint testi predpostavljajo dovoljen dostop Podpornik."""
    return patch.object(srv, "require_active_podpornik", return_value=None)


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
        self.assertIn("0 st. / km²", html)
        self.assertIn("radij 10 km", html)
        self.assertNotIn("data-radius-km", html)

    def test_map_embed_has_no_grid_storm_mode_button(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "public" / "map-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('id="mapModeBtnDni"', html)
        gridSection = html.split("loadGridData")[1].split("async function reload")[0]
        self.assertNotIn("storm_days", gridSection)

    def test_map_embed_grid_locked_without_supporter(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "public" / "map-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("isGridLocked", html)
        self.assertIn("isPeriodLocked", html)
        self.assertIn("showGridLockUi", html)
        self.assertIn("switchToObcineFromLockedGrid", html)
        self.assertIn("Ta prikaz je na voljo s paketom Podpornik", html)
        self.assertIn("Z mrežo 1 × 1 km lahko podrobneje analizirate prostorsko razporeditev", html)
        self.assertIn("map-lock-glyph", html)
        self.assertIn("Mreža zahteva paket Podpornik", html)
        self.assertIn("strelkoAuthHeaders", html)
        self.assertIn('next === "grid" && !hasSupporterAccess', html)
        self.assertIn('next === "obcine" && isGridLocked()', html)
        self.assertIn("z-index: 1100", html)
        self.assertIn("strele-map-view-changed", html)
        self.assertIn("gridLoadSeq", html)
        self.assertIn('isPeriodLocked("obcine"', html)

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

            with _allow_grid_podpornik(srv):
              with patch.object(
                  srv,
                  "refresh_today_grid_cache_if_stale",
                  return_value={"refreshed": False, "reason": "fresh", "age_sec": 10, "cache_hit": True},
              ):
                with patch.object(srv, "fetch_grid_map_from_daily", side_effect=AssertionError("daily read called")):
                  with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
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
            self.assertEqual(res.headers.get("X-Grid-Cache"), "hit")
            self.assertIn("max-age=60", res.headers.get("Cache-Control", ""))

    def test_api_grid_map_today_refreshes_stale_cache(self):
        import tempfile
        from starlette.requests import Request

        srv = self._import_server()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            payload = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"strike_count": 1}, "geometry": None}],
                "cached": True,
                "cache_version": _CACHE_VERSION,
                "from": "2026-07-19",
                "to": "2026-07-19",
                "generated_at": "2026-07-19T08:00:00Z",
            }
            (cache_dir / today_cache_basename()).write_text(json.dumps(payload) + "\n", encoding="utf-8")
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]
            refresh = MagicMock(
                return_value={"refreshed": True, "reason": "stale", "age_sec": 0.2, "cache_hit": False}
            )
            with _allow_grid_podpornik(srv):
              with patch.object(srv, "refresh_today_grid_cache_if_stale", refresh):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
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
            refresh.assert_called_once()
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers.get("X-Grid-Cache"), "miss")
            self.assertEqual(res.headers.get("X-Grid-Refresh"), "stale")

    def test_map_embed_grid_today_poll_and_seq(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "public" / "map-embed.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("liveGrid", html)
        self.assertIn("Math.min(REFRESH_SEC, 120)", html)
        self.assertIn("gridData = null", html)
        self.assertIn("gridLoadSeq", html)
        self.assertIn('if (loadSeq !== gridLoadSeq || mapView !== "grid") return', html)
        self.assertIn("Mreža zahteva paket Podpornik", html)
        self.assertIn("cache: \"no-store\"", html)
        # Preklop na mrežo gre skozi reload() → removeGridLayers() → loadGridData().
        self.assertIn("await loadGridData()", html)
        self.assertIn("if (!hasSupporterAccess || mapView !== \"grid\")", html)

    def test_api_grid_map_single_day_uses_daily_table(self):
        from starlette.requests import Request

        srv = self._import_server()
        req = Request({"type": "http", "headers": []})
        fake = {"type": "FeatureCollection", "features": [], "cached": False}
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        with _allow_grid_podpornik(srv):
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
            with _allow_grid_podpornik(srv):
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
            with _allow_grid_podpornik(srv):
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
            with _allow_grid_podpornik(srv):
              with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                with patch.object(
                    srv,
                    "refresh_today_grid_cache_if_stale",
                    return_value={"refreshed": False, "reason": "fresh", "age_sec": 1.0},
                ):
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

    def _req(self):
        from starlette.requests import Request
        return Request({"type": "http", "headers": []})

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
        with _allow_grid_podpornik(srv):
          with patch.object(srv, "fetch_grid_cell_daily", return_value=fake) as fetch_mock:
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    out = srv.api_grid_cell_daily(
                        self._req(),
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
        with _allow_grid_podpornik(srv):
          with patch.object(srv, "fetch_grid_cell_daily", return_value=None):
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    with self.assertRaises(HTTPException) as ctx:
                        srv.api_grid_cell_daily(
                            self._req(),
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
        with _allow_grid_podpornik(srv):
          with patch.object(srv, "fetch_grid_cell_daily", return_value={"cell": {}, "series": []}) as fetch_mock:
            with patch.object(srv.psycopg, "connect", return_value=mock_conn):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    srv.api_grid_cell_daily(
                        self._req(),
                        lat=46.05,
                        lon=14.5,
                        from_=date(2026, 7, 1),
                        to_=date(2026, 9, 28),
                    )
        fetch_mock.assert_called_once()

    def test_api_grid_cell_daily_91_days_rejected(self):
        from fastapi import HTTPException

        srv = self._import_server()
        with _allow_grid_podpornik(srv):
          with self.assertRaises(HTTPException) as ctx:
            srv.api_grid_cell_daily(
                self._req(),
                lat=46.05,
                lon=14.5,
                from_=date(2026, 7, 1),
                to_=date(2026, 9, 29),
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_api_grid_cell_daily_reversed_period(self):
        from fastapi import HTTPException

        srv = self._import_server()
        with _allow_grid_podpornik(srv):
          with self.assertRaises(HTTPException) as ctx:
            srv.api_grid_cell_daily(
                self._req(),
                lat=46.05,
                lon=14.5,
                from_=date(2026, 7, 15),
                to_=date(2026, 7, 1),
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Neveljavno", ctx.exception.detail)



class GridTodayRefreshTest(unittest.TestCase):
    def test_today_cache_age_and_skip_when_fresh(self):
        import tempfile
        import time
        from strele_archive.grid_cache_job import (
            refresh_today_grid_cache_if_stale,
            today_cache_age_sec,
            today_cache_basename,
        )

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
            age = today_cache_age_sec(cache_dir)
            self.assertIsNotNone(age)
            self.assertLess(age, 5)
            with patch("strele_archive.grid_cache_job.psycopg.connect") as connect:
                out = refresh_today_grid_cache_if_stale(
                    database_url="postgresql://example",
                    cache_dir=cache_dir,
                    max_age_sec=120,
                )
            connect.assert_not_called()
            self.assertFalse(out["refreshed"])
            self.assertEqual(out["reason"], "fresh")

    def test_today_refresh_runs_when_missing(self):
        import tempfile
        from strele_archive.grid_cache_job import refresh_today_grid_cache_if_stale

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            mock_conn = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = MagicMock()
            with patch("strele_archive.grid_cache_job.psycopg.connect", return_value=mock_conn):
                with patch("strele_archive.grid_cache_job._try_advisory_lock", return_value=True):
                    with patch("strele_archive.grid_cache_job._advisory_unlock"):
                        with patch("strele_archive.grid_cache_job._max_strike_ts_utc", return_value=None):
                            with patch("strele_archive.grid_cache_job.rebuild_grid_daily_aggregates") as reb:
                                with patch("strele_archive.grid_cache_job.rebuild_today_cache_file") as rebuild_file:
                                    out = refresh_today_grid_cache_if_stale(
                                        database_url="postgresql://example",
                                        cache_dir=cache_dir,
                                        max_age_sec=120,
                                    )
            reb.assert_called_once()
            rebuild_file.assert_called_once()
            self.assertTrue(out["refreshed"])
            self.assertEqual(out["reason"], "missing")

    def test_obcine_and_grid_today_share_local_day_bounds(self):
        """Občine (days=1) in mreža (today=1) morata ciljati isti lokalni dan Europe/Ljubljana."""
        from strele_archive.grid_map import local_today as grid_local_today
        from strele_archive.obcina_widget_daily import local_today as obcina_local_today

        self.assertEqual(grid_local_today(), obcina_local_today())

    def test_concurrent_refresh_single_flight_via_advisory_lock(self):
        """Dva sočasna stale zahtevka: samo en dobi lock in obnovi, drugi uporabi obstoječi cache."""
        import tempfile
        from strele_archive.grid_cache_job import refresh_today_grid_cache_if_stale, today_cache_basename

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            old = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"strike_count": 9}, "geometry": None}],
                "cached": True,
                "cache_version": _CACHE_VERSION,
            }
            (cache_dir / today_cache_basename()).write_text(json.dumps(old) + "\n", encoding="utf-8")
            # Prestari cache (mtime v preteklosti).
            os.utime(cache_dir / today_cache_basename(), (1_700_000_000, 1_700_000_000))

            mock_conn = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = MagicMock()
            lock_calls = {"n": 0}

            def try_lock(_conn):
                lock_calls["n"] += 1
                return lock_calls["n"] == 1

            with patch("strele_archive.grid_cache_job.psycopg.connect", return_value=mock_conn):
                with patch("strele_archive.grid_cache_job._try_advisory_lock", side_effect=try_lock):
                    with patch("strele_archive.grid_cache_job._advisory_unlock"):
                        with patch("strele_archive.grid_cache_job._max_strike_ts_utc", return_value=None):
                            with patch(
                                "strele_archive.grid_cache_job.rebuild_grid_daily_aggregates"
                            ) as reb:
                                with patch(
                                    "strele_archive.grid_cache_job.rebuild_today_cache_file"
                                ) as rebuild_file:
                                    first = refresh_today_grid_cache_if_stale(
                                        database_url="postgresql://example",
                                        cache_dir=cache_dir,
                                        max_age_sec=120,
                                    )
                                    second = refresh_today_grid_cache_if_stale(
                                        database_url="postgresql://example",
                                        cache_dir=cache_dir,
                                        max_age_sec=120,
                                    )
            reb.assert_called_once()
            rebuild_file.assert_called_once()
            self.assertTrue(first["refreshed"])
            self.assertFalse(second["refreshed"])
            self.assertEqual(second["reason"], "lock_busy")
            self.assertTrue(second["cache_hit"])

    def test_failed_refresh_preserves_last_valid_cache(self):
        """Neuspešna obnova pusti zadnji veljavni JSON; API vrne cache + X-Grid-Refresh=refresh_error."""
        import tempfile
        from starlette.requests import Request

        try:
            import strele_archive.obcine_public_server as srv
        except ModuleNotFoundError:
            self.skipTest("obcine_public_server optional deps not installed")

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            payload = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"strike_count": 4}, "geometry": None}],
                "cached": True,
                "cache_version": _CACHE_VERSION,
                "from": "2026-07-19",
                "to": "2026-07-19",
            }
            path = cache_dir / today_cache_basename()
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            srv._GRID_CACHE_DIR = cache_dir  # type: ignore[attr-defined]
            req = Request({"type": "http", "headers": []})
            with _allow_grid_podpornik(srv):
                with patch.object(srv, "_udari_database_url", return_value="postgresql://example"):
                    with patch.object(
                        srv,
                        "refresh_today_grid_cache_if_stale",
                        side_effect=RuntimeError("rebuild failed"),
                    ):
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
            self.assertEqual(res.headers.get("X-Grid-Refresh"), "refresh_error")
            self.assertEqual(res.headers.get("X-Grid-Cache"), "hit")
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_today_cache_write_is_atomic_tmp_then_replace(self):
        """Ciljna datoteka se zamenja šele po popolnem zapisu tmp — delni JSON ni izpostavljen."""
        import tempfile
        from strele_archive.grid_cache_job import rebuild_today_cache_file, today_cache_basename

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            dst = cache_dir / today_cache_basename()
            old = {"type": "FeatureCollection", "features": [], "cached": True, "cache_version": _CACHE_VERSION}
            dst.write_text(json.dumps(old) + "\n", encoding="utf-8")
            old_text = dst.read_text(encoding="utf-8")

            seen: list[str] = []

            original_write = Path.write_text

            def boom_write(path_self, data, *args, **kwargs):  # noqa: ANN001
                if path_self.name.endswith(".tmp"):
                    seen.append("tmp_write")
                    assert dst.read_text(encoding="utf-8") == old_text
                return original_write(path_self, data, *args, **kwargs)

            mock_conn = MagicMock()
            with patch.object(Path, "write_text", boom_write):
                with patch(
                    "strele_archive.grid_cache_job.build_today_cached_feature_collection",
                    return_value={
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "properties": {"strike_count": 1}}],
                        "cached": True,
                        "cache_version": _CACHE_VERSION,
                    },
                ):
                    rebuild_today_cache_file(
                        mock_conn, cache_dir=cache_dir, today_local=date(2026, 7, 19)
                    )
            self.assertIn("tmp_write", seen)
            new = json.loads(dst.read_text(encoding="utf-8"))
            self.assertEqual(new["features"][0]["properties"]["strike_count"], 1)
            self.assertFalse((cache_dir / ".grid-map-today.json.tmp").exists())



class GridPodpornikGateTest(unittest.TestCase):
    def _import_server(self):
        try:
            import strele_archive.obcine_public_server as srv
        except ModuleNotFoundError:
            self.skipTest("obcine_public_server optional deps not installed")
        return srv

    def test_api_grid_map_without_membership_returns_403(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from strele_archive.strelko_auth import GRID_PODPORNIK_FORBIDDEN

        srv = self._import_server()
        req = Request({"type": "http", "headers": []})
        refresh = MagicMock(return_value={"refreshed": True, "reason": "should_not_run"})
        with patch("strele_archive.strelko_auth.strelko_open_access", return_value=False):
            with patch("strele_archive.strelko_auth.extract_bearer_token", return_value=None):
                with patch.object(srv, "refresh_today_grid_cache_if_stale", refresh):
                    with self.assertRaises(HTTPException) as ctx:
                        srv.api_grid_map(
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
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, GRID_PODPORNIK_FORBIDDEN)
        refresh.assert_not_called()

    def test_api_grid_map_active_podpornik_allowed(self):
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
            req = Request({"type": "http", "headers": [(b"authorization", b"Bearer tok")]})
            with patch("strele_archive.strelko_auth.strelko_open_access", return_value=False):
                with patch("strele_archive.strelko_auth.extract_bearer_token", return_value="tok"):
                    with patch(
                        "strele_archive.strelko_auth.fetch_strelko_credits",
                        return_value={"plan_id": "podpornik", "has_subscription": True},
                    ):
                        with patch.object(
                            srv,
                            "refresh_today_grid_cache_if_stale",
                            return_value={"refreshed": False, "reason": "fresh", "age_sec": 1.0},
                        ):
                            with patch.object(
                                srv, "fetch_grid_map_from_daily", side_effect=AssertionError("daily read called")
                            ):
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

    def test_is_podpornik_active_credits_mirrors_frontend(self):
        from strele_archive.strelko_auth import is_podpornik_active_credits

        today = date(2026, 7, 19)
        self.assertFalse(is_podpornik_active_credits(None, today=today))
        self.assertFalse(is_podpornik_active_credits({"plan_id": "ob_skodi"}, today=today))
        self.assertFalse(
            is_podpornik_active_credits(
                {"plan_id": "podpornik", "has_subscription": False, "season_pass_expires_at": "2026-07-01"},
                today=today,
            )
        )
        self.assertTrue(
            is_podpornik_active_credits({"plan_id": "podpornik", "has_subscription": True}, today=today)
        )
        self.assertTrue(
            is_podpornik_active_credits(
                {"plan_id": "podpornik", "has_subscription": False, "season_pass_expires_at": "2026-12-31"},
                today=today,
            )
        )


if __name__ == "__main__":
    unittest.main()
