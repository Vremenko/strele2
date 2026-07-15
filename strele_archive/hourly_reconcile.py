"""Uskladitev urnih agregatov z dnevnimi (interni popravek iz dedup).

Opomba: ne pridobiva manjkajočih udarov iz StormAPI. Za popolno uskladitev
glej strele_archive.day_reconcile (avtoritativni vir + PiP razvrstitev).
"""

from __future__ import annotations

from datetime import date

import psycopg

from strele_archive.config import get_settings


def _daily_count(conn: psycopg.Connection, day: date) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stevilo FROM strele_si_dnevno WHERE datum = %s",
            (day,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def _hourly_sum(conn: psycopg.Connection, day: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(stevilo), 0)::int FROM strele_si_urno WHERE datum = %s",
            (day,),
        )
        return int(cur.fetchone()[0])


def hourly_from_dedup(conn: psycopg.Connection, day: date, tz: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXTRACT(HOUR FROM ts_utc AT TIME ZONE %s)::int AS ura,
                   COUNT(*)::int AS stevilo
            FROM strele_dedup
            WHERE (ts_utc AT TIME ZONE %s)::date = %s
            GROUP BY 1
            ORDER BY 1
            """,
            (tz, tz, day),
        )
        by_hour = {int(r[0]): int(r[1]) for r in cur.fetchall()}
    return [{"ura": h, "stevilo": by_hour.get(h, 0)} for h in range(24)]


def query_hourly_from_db(day: date) -> list[dict]:
    with psycopg.connect(get_settings().database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ura, stevilo FROM strele_si_urno
                WHERE datum = %s ORDER BY ura
                """,
                (day,),
            )
            rows = {int(r[0]): int(r[1]) for r in cur.fetchall()}
    return [{"ura": h, "stevilo": rows.get(h, 0)} for h in range(24)]


def reconcile_hourly_for_day(day: date) -> list[dict] | None:
    """
    Če strele_si_urno ne ustreza strele_si_dnevno, vrni urni profil iz dedup.
    Vrne None, če uskladitev ni potrebna ali ni mogoča.
    """
    settings = get_settings()
    tz = settings.timezone
    with psycopg.connect(settings.database_url) as conn:
        daily = _daily_count(conn, day)
        if daily is None:
            return None
        hourly_sum = _hourly_sum(conn, day)
        if hourly_sum == daily:
            return None
        dedup_hourly = hourly_from_dedup(conn, day, tz)
        if sum(r["stevilo"] for r in dedup_hourly) == daily:
            return dedup_hourly
    return None


def rebuild_urno_from_dedup(day: date) -> int:
    """Popravi strele_si_urno za dan iz dedup. Vrne 0 če popravek ni bil potreben."""
    hourly = reconcile_hourly_for_day(day)
    if hourly is None:
        return 0
    with psycopg.connect(get_settings().database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM strele_si_urno WHERE datum = %s", (day,))
            for row in hourly:
                cur.execute(
                    """
                    INSERT INTO strele_si_urno (datum, ura, stevilo)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (datum, ura) DO UPDATE SET stevilo = EXCLUDED.stevilo
                    """,
                    (day, row["ura"], row["stevilo"]),
                )
        conn.commit()
    return len(hourly)
