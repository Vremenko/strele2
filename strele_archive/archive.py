"""Arhiv agregatov v PostgreSQL — inkrementalni sync iz API."""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta

import psycopg
import requests

from strele_archive.config import Settings, get_settings
from strele_archive.meteoinfo_client import MeteoinfoClient
from strele_archive.obcina_regija import get_obcina_regija_map

logger = logging.getLogger(__name__)


def _fetch_municipalities_with_retry(
    client: MeteoinfoClient,
    start: datetime,
    end: datetime,
    *,
    attempts: int = 4,
) -> list[dict]:
    delay = float(os.getenv("SYNC_API_DELAY_SEC", "0.4"))
    for attempt in range(attempts):
        try:
            if delay > 0:
                time.sleep(delay)
            return client.fetch_municipalities(start, end, limit=500)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                wait = 2 ** attempt
                logger.warning("API 429, cakam %ss ...", wait)
                time.sleep(wait)
                continue
            raise
    return []


def ensure_meta_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_sync_dnevno (
                datum     DATE PRIMARY KEY,
                stevilo   INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
                vir       TEXT NOT NULL DEFAULT 'meteoinfo_obcine',
                synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def archived_dates(conn: psycopg.Connection) -> set[date]:
    with conn.cursor() as cur:
        cur.execute("SELECT datum FROM meta_sync_dnevno")
        return {row[0] for row in cur.fetchall()}


def archive_range(conn: psycopg.Connection) -> tuple[date | None, date | None, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MIN(datum), MAX(datum), COUNT(*)
            FROM meta_sync_dnevno
            WHERE stevilo > 0
            """
        )
        row = cur.fetchone()
        return row[0], row[1], row[2] if row else (None, None, 0)


def persist_empty_meta(conn: psycopg.Connection, day: date) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO meta_sync_dnevno (datum, stevilo, vir, synced_at)
            VALUES (%s, 0, 'no_api', now())
            ON CONFLICT (datum) DO NOTHING
            """,
            (day,),
        )
    conn.commit()


def persist_regije_from_cells(
    conn: psycopg.Connection,
    day: date,
    cells: list[dict],
) -> None:
    obcina_regija = get_obcina_regija_map()
    totals: dict[int, int] = {}
    for cell in cells:
        regija_id = obcina_regija.get(int(cell["code"]))
        if regija_id is None:
            continue
        totals[regija_id] = totals.get(regija_id, 0) + int(cell.get("count", 0))

    with conn.cursor() as cur:
        for regija_id, stevilo in totals.items():
            cur.execute(
                """
                INSERT INTO strele_regija_dnevno (regija_id, datum, stevilo)
                VALUES (%s, %s, %s)
                ON CONFLICT (regija_id, datum) DO UPDATE
                SET stevilo = EXCLUDED.stevilo
                """,
                (regija_id, day, stevilo),
            )
    conn.commit()


def persist_hourly(
    conn: psycopg.Connection,
    client: MeteoinfoClient,
    day: date,
) -> None:
    hourly = client.fetch_slovenia_hourly(day)
    api_sum = sum(int(row["stevilo"]) for row in hourly)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stevilo FROM strele_si_dnevno WHERE datum = %s",
            (day,),
        )
        row = cur.fetchone()
        if row is not None and int(row[0]) < api_sum:
            logger.info(
                "Preskočen hourly sync za %s: arhiv=%s < API=%s",
                day,
                row[0],
                api_sum,
            )
            return
    with conn.cursor() as cur:
        for row in hourly:
            cur.execute(
                """
                INSERT INTO strele_si_urno (datum, ura, stevilo)
                VALUES (%s, %s, %s)
                ON CONFLICT (datum, ura) DO UPDATE
                SET stevilo = EXCLUDED.stevilo
                """,
                (day, row["ura"], row["stevilo"]),
            )
    conn.commit()


def persist_day(
    conn: psycopg.Connection,
    day: date,
    cells: list[dict],
    *,
    source: str = "meteoinfo_obcine",
) -> int:
    day_total = 0
    with conn.cursor() as cur:
        for cell in cells:
            obcina_id = int(cell["code"])
            stevilo = int(cell.get("count", 0))
            day_total += stevilo
            cur.execute(
                """
                INSERT INTO strele_obcina_dnevno (obcina_id, datum, stevilo)
                VALUES (%s, %s, %s)
                ON CONFLICT (obcina_id, datum) DO UPDATE
                SET stevilo = EXCLUDED.stevilo
                """,
                (obcina_id, day, stevilo),
            )

        cur.execute(
            """
            INSERT INTO strele_si_dnevno (datum, stevilo)
            VALUES (%s, %s)
            ON CONFLICT (datum) DO UPDATE
            SET stevilo = EXCLUDED.stevilo
            """,
            (day, day_total),
        )
        cur.execute(
            """
            INSERT INTO meta_sync_dnevno (datum, stevilo, vir, synced_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (datum) DO UPDATE
            SET stevilo = EXCLUDED.stevilo,
                vir = EXCLUDED.vir,
                synced_at = now()
            """,
            (day, day_total, source),
        )
    conn.commit()
    return day_total


def sync_day(
    client: MeteoinfoClient,
    conn: psycopg.Connection,
    day: date,
    *,
    mark_empty: bool = False,
) -> int | None:
    start_s, end_s = client.day_window_utc(day)
    t0 = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
    cells = _fetch_municipalities_with_retry(client, t0, t1)
    if not cells:
        if mark_empty:
            persist_empty_meta(conn, day)
            logger.info("Arhiviran %s: brez API podatkov (oznaceno)", day)
        return None
    total = persist_day(conn, day, cells)
    persist_regije_from_cells(conn, day, cells)
    persist_hourly(conn, client, day)
    logger.info("Arhiviran %s: %s udarcev (%s obcin)", day, total, len(cells))
    return total


def days_to_sync(
    conn: psycopg.Connection,
    since: date,
    until: date,
    *,
    full: bool,
    refresh_days: int,
) -> list[date]:
    archived = archived_dates(conn)
    refresh_since = date.today() - timedelta(days=max(refresh_days - 1, 0))
    result: list[date] = []
    d = since
    while d <= until:
        if full or d not in archived or d >= refresh_since:
            result.append(d)
        d += timedelta(days=1)
    return result


def backfill_hourly_mismatch(
    client: MeteoinfoClient,
    since: date,
    until: date,
    *,
    max_days: int = 5,
) -> int:
    from strele_archive.export import _hourly_mismatch_days, _persist_si_hourly

    todo = _hourly_mismatch_days(since, until)[:max_days]
    fixed = 0
    for day in todo:
        try:
            hourly = client.fetch_slovenia_hourly(day)
            with psycopg.connect(get_settings().database_url) as conn:
                _persist_si_hourly(conn, day, hourly)
            fixed += 1
            logger.info("Urni profil dopolnjen za %s", day)
        except Exception:
            logger.warning("Urni profil za %s ni bil dopolnjen", day, exc_info=True)
    return fixed


def sync_incremental(
    settings: Settings | None = None,
    *,
    days: int | None = None,
    full: bool = False,
) -> dict:
    settings = settings or get_settings()
    days = days or int(os.getenv("SYNC_DAYS", "90"))
    refresh_days = int(os.getenv("SYNC_REFRESH_DAYS", "2"))
    client = MeteoinfoClient(settings)

    since = date.today() - timedelta(days=days - 1)
    until = date.today()
    refresh_since = date.today() - timedelta(days=max(refresh_days - 1, 0))

    with psycopg.connect(settings.database_url) as conn:
        ensure_meta_table(conn)
        todo = days_to_sync(conn, since, until, full=full, refresh_days=refresh_days)
        synced = 0
        skipped = 0
        obcine_rows = 0

        for d in todo:
            mark_empty = d < refresh_since
            total = sync_day(client, conn, d, mark_empty=mark_empty)
            if total is None:
                skipped += 1
                continue
            synced += 1
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM strele_obcina_dnevno WHERE datum = %s
                    """,
                    (d,),
                )
                obcine_rows += cur.fetchone()[0]

        lo, hi, archived_count = archive_range(conn)

    backfilled = backfill_hourly_mismatch(
        client,
        since,
        until,
        max_days=int(os.getenv("SYNC_HOURLY_BACKFILL_DAYS", "5")),
    )

    stats = {
        "mode": "full" if full else "incremental",
        "todo_days": len(todo),
        "synced_days": synced,
        "skipped_no_api": skipped,
        "obcina_rows": obcine_rows,
        "archive_from": lo.isoformat() if lo else None,
        "archive_to": hi.isoformat() if hi else None,
        "archive_days": archived_count,
        "hourly_backfilled": backfilled,
    }
    logger.info("Sync koncan: %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Arhiviraj agregate v lokalno bazo")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ponovno syncaj vse dni v oknu (ne samo manjkajoce + sveze)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Stevilo dni nazaj (privzeto SYNC_DAYS iz .env)",
    )
    args = parser.parse_args()
    stats = sync_incremental(days=args.days, full=args.full)
    print(stats)


if __name__ == "__main__":
    main()
