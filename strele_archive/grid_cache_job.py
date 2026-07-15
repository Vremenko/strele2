"""Urni job za 1×1 km mrežo: dnevni agregati + disk cache za danes in 7/14/30/90 dni.

Zahteve:
- Idempotentno (lahko teče večkrat; uporablja advisory lock proti hkratnemu zagonu).
- Časovni pas: Europe/Ljubljana (lokalni koledarski dan).
- Če ni novih strel in se lokalni datum ni spremenil → hiter izhod.
- Ob prvi izvedbi novega lokalnega dne → rebuild cache tudi brez novih strel (drseče okno).
- Cache zapis mora biti atomski (tmp + rename).
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from strele_archive.grid_map import (
    build_cached_feature_collection,
    build_today_cached_feature_collection,
    rebuild_grid_daily_aggregates,
    today_cache_basename,
)

_LJ_TZ = ZoneInfo("Europe/Ljubljana")

# Fixed advisory lock key; must be stable across runs.
_ADVISORY_LOCK_KEY = 3794_1000_20260713

CACHE_DAYS = (7, 14, 30, 90)


@dataclass(frozen=True)
class JobMeta:
    last_max_ts_utc: str | None
    last_local_day: str | None


def _cache_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "cache" / "grid-map"


def _meta_path(cache_dir: Path) -> Path:
    return cache_dir / "grid-cache-meta.json"


def _read_meta(cache_dir: Path) -> JobMeta:
    p = _meta_path(cache_dir)
    if not p.exists():
        return JobMeta(None, None)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return JobMeta(
            last_max_ts_utc=str(raw.get("last_max_ts_utc") or "") or None,
            last_local_day=str(raw.get("last_local_day") or "") or None,
        )
    except Exception:
        return JobMeta(None, None)


def _write_meta_atomic(cache_dir: Path, meta: JobMeta) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_dir / ".grid-cache-meta.json.tmp"
    dst = _meta_path(cache_dir)
    tmp.write_text(
        json.dumps(
            {"last_max_ts_utc": meta.last_max_ts_utc, "last_local_day": meta.last_local_day},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(dst)


def _now_local_day() -> date:
    return datetime.now(tz=_LJ_TZ).date()


def _parse_ts_utc(ts: str) -> datetime | None:
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _max_strike_ts_utc(conn: psycopg.Connection) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(ts_utc) AS max_ts
            FROM (
              SELECT MAX(ts_utc) AS ts_utc FROM strele.udari
              UNION ALL
              SELECT MAX(ts_utc) AS ts_utc FROM strele.udari_24h
            ) t
            """
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def _try_advisory_lock(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        row = cur.fetchone()
        return bool(row and row[0])


def _advisory_unlock(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))


def _days_to_range(today_local: date, days: int) -> tuple[date, date]:
    return today_local - timedelta(days=days - 1), today_local


def rebuild_cache_files(
    conn: psycopg.Connection,
    *,
    cache_dir: Path,
    today_local: date,
) -> None:
    """Atomično zapiše grid-map-today + grid-map-7/14/30/90."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, Path]] = []

    today_payload = build_today_cached_feature_collection(conn, today_local=today_local)
    today_tmp = cache_dir / ".grid-map-today.json.tmp"
    today_dst = cache_dir / today_cache_basename()
    today_tmp.write_text(json.dumps(today_payload, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append((today_tmp, today_dst))

    for d in CACHE_DAYS:
        start, end = _days_to_range(today_local, d)
        payload = build_cached_feature_collection(conn, start=start, end=end, days=d)
        tmp = cache_dir / f".grid-map-{d}.json.tmp"
        dst = cache_dir / f"grid-map-{d}.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append((tmp, dst))

    for tmp, dst in written:
        tmp.replace(dst)


def run_job(
    *,
    database_url: str,
    backfill_days: int | None = None,
    force_cache_rebuild: bool = False,
) -> int:
    cache_dir = _cache_dir()
    meta_before = _read_meta(cache_dir)
    today_local = _now_local_day()

    with psycopg.connect(database_url) as conn:
        conn.autocommit = False

        if not _try_advisory_lock(conn):
            return 0

        try:
            max_ts = _max_strike_ts_utc(conn)
            max_ts_iso = _iso_utc(max_ts) if max_ts else None

            last_local_day = meta_before.last_local_day
            day_changed = last_local_day != today_local.isoformat()

            if backfill_days:
                start = today_local - timedelta(days=backfill_days - 1)
                with conn.transaction():
                    rebuild_grid_daily_aggregates(conn, day_from=start, day_to=today_local)
                rebuild_cache_files(conn, cache_dir=cache_dir, today_local=today_local)
                _write_meta_atomic(cache_dir, JobMeta(max_ts_iso, today_local.isoformat()))
                conn.commit()
                return 0

            last_max = _parse_ts_utc(meta_before.last_max_ts_utc) if meta_before.last_max_ts_utc else None
            has_new = bool(max_ts and (last_max is None or max_ts > last_max))

            if force_cache_rebuild:
                rebuild_cache_files(conn, cache_dir=cache_dir, today_local=today_local)
                _write_meta_atomic(cache_dir, JobMeta(max_ts_iso, today_local.isoformat()))
                conn.commit()
                return 0

            if not has_new and not day_changed:
                return 0

            if day_changed and not has_new:
                with conn.transaction():
                    rebuild_grid_daily_aggregates(conn, day_from=today_local, day_to=today_local)
                rebuild_cache_files(conn, cache_dir=cache_dir, today_local=today_local)
                _write_meta_atomic(cache_dir, JobMeta(max_ts_iso, today_local.isoformat()))
                conn.commit()
                return 0

            if has_new:
                if last_max:
                    last_local = last_max.astimezone(_LJ_TZ).date()
                else:
                    last_local = today_local
                with conn.transaction():
                    rebuild_grid_daily_aggregates(conn, day_from=last_local, day_to=today_local)
                rebuild_cache_files(conn, cache_dir=cache_dir, today_local=today_local)
                _write_meta_atomic(cache_dir, JobMeta(max_ts_iso, today_local.isoformat()))
                conn.commit()
                return 0

            return 0
        finally:
            try:
                _advisory_unlock(conn)
            except Exception:
                pass


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--database-url",
        default=os.getenv("UDARI_DATABASE_URL") or "",
        help="PostGIS URL (same DB as raw strikes). Defaults to UDARI_DATABASE_URL.",
    )
    p.add_argument(
        "--backfill-days",
        type=int,
        default=None,
        help="Backfill last N local days into lightning_grid_1km_daily and build caches.",
    )
    p.add_argument(
        "--force-cache-rebuild",
        action="store_true",
        help="Rebuild cache files even if watermark/day did not change (uses daily tables only).",
    )
    args = p.parse_args()
    if not args.database_url.strip():
        raise SystemExit("UDARI_DATABASE_URL is missing")
    raise SystemExit(
        run_job(
            database_url=args.database_url.strip(),
            backfill_days=args.backfill_days,
            force_cache_rebuild=bool(args.force_cache_rebuild),
        )
    )


if __name__ == "__main__":
    main()
