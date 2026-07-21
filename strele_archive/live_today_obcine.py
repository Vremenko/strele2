"""Današnji živi števci po občinah — isti vir kot občinski zemljevid.

En SQL + PiP na lokalni datum Europe/Ljubljana; top / gostota / regije se
izpeljejo iz števcev po ob_mid (brez ponovnega pregleda udarov).
Kratek TTL predpomni rezultat med endpointi (tudi med procesi).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from strele_archive.config import get_settings
from strele_archive.obcina_widget_daily import local_today
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions
from strele_archive.si_widget_counts import dedup_pip_strikes, filter_pip_strikes
from strele_archive.timezone_utils import lj_day_bounds_utc
from strele_archive.udari_client import udari_database_url

_TTL_SEC = float(os.getenv("STRELE_LIVE_TODAY_TTL_SEC", "20"))
_CACHE_DIR = Path(os.getenv("STRELE_LIVE_TODAY_CACHE_DIR", "/tmp/strele-live-today-cache"))

_region_index = None
_obcina_index = None
_lock = threading.Lock()
# day_iso -> (expires_monotonic, pip, counts_by_ob_mid, by_region_name)
_mem: dict[
    str,
    tuple[
        float,
        list[tuple[float, float, datetime]],
        dict[int, int],
        dict[str, int],
    ],
] = {}
_compute_count = 0


def live_today_compute_count() -> int:
    return _compute_count


def clear_live_today_cache_for_tests() -> None:
    global _compute_count
    with _lock:
        _mem.clear()
        _compute_count = 0
    try:
        if _CACHE_DIR.is_dir():
            for p in _CACHE_DIR.glob("live-today-*.json"):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _regions():
    global _region_index
    if _region_index is None:
        _region_index = load_regions(get_settings().regions_geojson)
    return _region_index


def _obcine():
    global _obcina_index
    if _obcina_index is None:
        _obcina_index = load_obcine(get_settings().obcine_geojson)
    return _obcina_index


def _parse_ts(ts_raw) -> datetime | None:
    if ts_raw is None:
        return None
    if isinstance(ts_raw, datetime):
        ts = ts_raw
    else:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _fetch_udari_window(
    start_utc: datetime,
    end_inclusive: datetime,
) -> list[tuple[float, float, datetime]]:
    url = udari_database_url()
    if not url:
        return []
    idx = _regions()
    min_lon, min_lat, max_lon, max_lat = idx.bounds
    parsed: list[tuple[float, float, datetime]] = []
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT lat, lon, ts_utc
                FROM strele.udari_24h
                WHERE ts_utc >= %s AND ts_utc <= %s
                  AND lat BETWEEN %s AND %s
                  AND lon BETWEEN %s AND %s
                ORDER BY ts_utc ASC
                LIMIT 50000
                """,
                (start_utc, end_inclusive, min_lat, max_lat, min_lon, max_lon),
            )
            for lat_raw, lon_raw, ts_raw in cur.fetchall():
                ts = _parse_ts(ts_raw)
                if ts is None:
                    continue
                parsed.append((float(lat_raw), float(lon_raw), ts))
    return parsed


def _cache_path(day: date) -> Path:
    return _CACHE_DIR / f"live-today-{day.isoformat()}.json"


def _read_file_cache(
    day: date,
) -> tuple[
    list[tuple[float, float, datetime]],
    dict[int, int],
    dict[str, int],
] | None:
    path = _cache_path(day)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > _TTL_SEC:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        pip: list[tuple[float, float, datetime]] = []
        for lat, lon, ts in data.get("pip") or []:
            parsed = _parse_ts(ts)
            if parsed is None:
                continue
            pip.append((float(lat), float(lon), parsed))
        counts = {int(k): int(v) for k, v in (data.get("counts") or {}).items()}
        by_region = {str(k): int(v) for k, v in (data.get("by_region") or {}).items()}
        return pip, counts, by_region
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_file_cache(
    day: date,
    pip: list[tuple[float, float, datetime]],
    counts: dict[int, int],
    by_region: dict[str, int],
) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(day)
        tmp = path.with_suffix(".tmp")
        payload = {
            "day": day.isoformat(),
            "pip": [[lat, lon, ts.isoformat()] for lat, lon, ts in pip],
            "counts": {str(k): v for k, v in counts.items()},
            "by_region": by_region,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _compute_pip_and_counts(
    day: date,
    *,
    now_utc: datetime,
) -> tuple[list[tuple[float, float, datetime]], dict[int, int], dict[str, int]]:
    global _compute_count
    start_utc, end_utc = lj_day_bounds_utc(day, end_cap_utc=now_utc)
    end_inclusive = end_utc - timedelta(microseconds=1) if end_utc > start_utc else end_utc
    raw = _fetch_udari_window(start_utc, end_inclusive)
    regions = _regions()
    pip = dedup_pip_strikes(filter_pip_strikes(raw, regions))
    obcine = _obcine()
    id_to_mid = {o.id: o.ob_mid for o in obcine.obcine}
    id_to_region = {r.id: r.name for r in regions.regions}
    counts: dict[int, int] = {}
    by_region: Counter[str] = Counter()
    for lat, lon, _ts in pip:
        rid = regions.lookup(lon, lat)
        if rid is not None:
            name = id_to_region.get(rid)
            if name is not None:
                by_region[name] += 1
        ob_id = obcine.lookup(lon, lat)
        if ob_id is None:
            continue
        mid = id_to_mid.get(ob_id)
        if mid is None:
            continue
        counts[mid] = counts.get(mid, 0) + 1
    for r in regions.regions:
        by_region.setdefault(r.name, 0)
    _compute_count += 1
    return pip, counts, dict(by_region)

def _get_cached_bundle(
    day: date,
    *,
    now_utc: datetime | None = None,
) -> tuple[
    list[tuple[float, float, datetime]],
    dict[int, int],
    dict[str, int],
] | None:
    """Vrne (pip, counts_by_ob_mid, by_region_name) za lokalni danes."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if day != local_today(now):
        return None
    if not udari_database_url():
        return None

    key = day.isoformat()
    mono = time.monotonic()
    with _lock:
        hit = _mem.get(key)
        if hit is not None and hit[0] > mono:
            return hit[1], hit[2], hit[3]

    file_hit = _read_file_cache(day)
    if file_hit is not None:
        with _lock:
            _mem[key] = (
                time.monotonic() + _TTL_SEC,
                file_hit[0],
                file_hit[1],
                file_hit[2],
            )
        return file_hit

    with _lock:
        hit = _mem.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1], hit[2], hit[3]
        pip, counts, by_region = _compute_pip_and_counts(day, now_utc=now)
        _mem[key] = (time.monotonic() + _TTL_SEC, pip, counts, by_region)
    _write_file_cache(day, pip, counts, by_region)
    return pip, counts, by_region


def live_today_si_pip_tuples(
    *,
    today: date | None = None,
    now_utc: datetime | None = None,
) -> list[tuple[float, float, datetime]]:
    """Današnji PiP udari v SI — enotni vir (kot občinski zemljevid)."""
    day = today or local_today(now_utc)
    bundle = _get_cached_bundle(day, now_utc=now_utc)
    return [] if bundle is None else bundle[0]


def live_today_obcina_counts_by_ob_mid(
    *,
    today: date | None = None,
    now_utc: datetime | None = None,
) -> dict[int, int]:
    """Današnji udari → števci po ob_mid (isti rezultat kot zemljevid)."""
    day = today or local_today(now_utc)
    bundle = _get_cached_bundle(day, now_utc=now_utc)
    return {} if bundle is None else dict(bundle[1])


def live_hourly_from_pip(
    pip: list[tuple[float, float, datetime]],
    day: date,
) -> list[dict]:
    tz = ZoneInfo(get_settings().timezone)
    by_hour: Counter[int] = Counter()
    for _lat, _lon, ts in pip:
        local = ts.astimezone(tz)
        if local.date() != day:
            continue
        by_hour[local.hour] += 1
    return [{"ura": h, "stevilo": int(by_hour.get(h, 0))} for h in range(24)]


def top_obcine_from_counts(counts: dict[int, int], limit: int = 10) -> list[dict]:
    rows = [
        {"obcina": o.name, "stevilo": int(counts.get(o.ob_mid, 0) or 0)}
        for o in _obcine().obcine
        if int(counts.get(o.ob_mid, 0) or 0) > 0
    ]
    rows.sort(key=lambda r: (-r["stevilo"], r["obcina"]))
    return rows[: max(1, int(limit))]


def gostota_obcine_from_counts(counts: dict[int, int], limit: int = 10) -> list[dict]:
    rows: list[dict] = []
    for o in _obcine().obcine:
        stevilo = int(counts.get(o.ob_mid, 0) or 0)
        if stevilo <= 0:
            continue
        pov = float(o.pov_km2 or 0)
        gostota = (stevilo / pov) if pov > 0 else 0.0
        rows.append({"obcina": o.name, "gostota": float(gostota), "stevilo": stevilo})
    rows.sort(key=lambda r: (-r["gostota"], r["obcina"]))
    return rows[: max(1, int(limit))]


def live_regije_for_today(
    *,
    today: date | None = None,
    now_utc: datetime | None = None,
) -> list[dict] | None:
    """Vsote regij iz istega nabora kot občinski števci (en prehod, brez novega SQL)."""
    day = today or local_today(now_utc)
    bundle = _get_cached_bundle(day, now_utc=now_utc)
    if bundle is None:
        return None
    by_region = bundle[2]
    rows = [{"regija": name, "stevilo": int(n)} for name, n in by_region.items()]
    rows.sort(key=lambda r: (-r["stevilo"], r["regija"]))
    return rows


def merge_live_named_counts(
    archive_rows: list[dict],
    live_by_name: dict[str, int],
    *,
    name_key: str,
) -> list[dict]:
    """Prišteje živo k arhivu, ki že NE vključuje danes (replace semantika)."""
    merged: dict[str, dict] = {}
    for row in archive_rows:
        name = str(row.get(name_key) or "")
        if not name:
            continue
        out = dict(row)
        out["stevilo"] = int(out.get("stevilo") or 0)
        merged[name] = out
    for name, count in live_by_name.items():
        live = int(count or 0)
        if name in merged:
            merged[name]["stevilo"] = int(merged[name].get("stevilo") or 0) + live
        else:
            merged[name] = {name_key: name, "stevilo": live}
    return list(merged.values())
