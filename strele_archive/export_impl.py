"""Izvoz agregatov iz PostgreSQL (čista Python implementacija)."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from strele_archive.config import get_settings


def _conn() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url)


def _date_bounds(*, day: date | None = None, days: int | None = None) -> tuple[date, date]:
    if day is not None:
        return day, day
    if days is None:
        raise ValueError("Podaj day ali days")
    end = date.today()
    start = end - timedelta(days=days - 1)
    return start, end


def export_si_daily(days: int) -> list[dict]:
    start, end = _date_bounds(days=days)
    sql = """
        WITH date_series AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS datum
        )
        SELECT ds.datum, COALESCE(s.stevilo, 0)::int AS stevilo
        FROM date_series ds
        LEFT JOIN strele_si_dnevno s ON s.datum = ds.datum
        ORDER BY ds.datum
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            rows = cur.fetchall()
    return [{"datum": str(r[0]), "stevilo": int(r[1])} for r in rows]


def export_si_hourly_period(days: int) -> list[dict]:
    start, end = _date_bounds(days=days)
    sql = """
        SELECT ura, COALESCE(SUM(stevilo), 0)::int AS stevilo
        FROM strele_si_urno
        WHERE datum BETWEEN %s AND %s
        GROUP BY ura
        ORDER BY ura
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            by_hour = {int(r[0]): int(r[1]) for r in cur.fetchall()}
    return [{"ura": h, "stevilo": by_hour.get(h, 0)} for h in range(24)]


def export_regije_daily(day: date) -> list[dict]:
    sql = """
        SELECT r.ime_sl AS regija, COALESCE(s.stevilo, 0)::int AS stevilo
        FROM regije r
        LEFT JOIN strele_regija_dnevno s ON s.regija_id = r.id AND s.datum = %s
        ORDER BY stevilo DESC, regija
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (day,))
            rows = cur.fetchall()
    return [{"regija": r[0], "stevilo": int(r[1])} for r in rows]


def export_regije_period(days: int) -> list[dict]:
    start, end = _date_bounds(days=days)
    sql = """
        SELECT r.ime_sl AS regija, COALESCE(SUM(s.stevilo), 0)::int AS stevilo
        FROM regije r
        LEFT JOIN strele_regija_dnevno s
            ON s.regija_id = r.id AND s.datum BETWEEN %s AND %s
        GROUP BY r.id, r.ime_sl
        ORDER BY stevilo DESC, regija
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end))
            rows = cur.fetchall()
    return [{"regija": r[0], "stevilo": int(r[1])} for r in rows]


def export_obcine_top(day: date, limit: int) -> list[dict]:
    sql = """
        SELECT o.ime_sl AS obcina, s.stevilo::int
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum = %s
        ORDER BY s.stevilo DESC, obcina
        LIMIT %s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (day, limit))
            rows = cur.fetchall()
    return [{"obcina": r[0], "stevilo": int(r[1])} for r in rows]


def export_obcine_top_period(days: int, limit: int) -> list[dict]:
    start, end = _date_bounds(days=days)
    sql = """
        SELECT o.ime_sl AS obcina, SUM(s.stevilo)::int AS stevilo
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum BETWEEN %s AND %s
        GROUP BY o.id, o.ime_sl
        ORDER BY stevilo DESC, obcina
        LIMIT %s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, limit))
            rows = cur.fetchall()
    return [{"obcina": r[0], "stevilo": int(r[1])} for r in rows]


def export_obcine_gostota_top(day: date, limit: int) -> list[dict]:
    sql = """
        SELECT
            o.ime_sl AS obcina,
            s.stevilo::float / NULLIF(o.pov_km2, 0) AS gostota,
            s.stevilo::int AS stevilo
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum = %s
        ORDER BY gostota DESC NULLS LAST, obcina
        LIMIT %s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (day, limit))
            rows = cur.fetchall()
    return [
        {"obcina": r[0], "gostota": float(r[1] or 0), "stevilo": int(r[2])}
        for r in rows
    ]


def export_obcine_gostota_period(days: int, limit: int) -> list[dict]:
    start, end = _date_bounds(days=days)
    sql = """
        SELECT
            o.ime_sl AS obcina,
            SUM(s.stevilo)::float / NULLIF(o.pov_km2, 0) AS gostota,
            SUM(s.stevilo)::int AS stevilo
        FROM strele_obcina_dnevno s
        JOIN obcine o ON o.id = s.obcina_id
        WHERE s.datum BETWEEN %s AND %s
        GROUP BY o.id, o.ime_sl, o.pov_km2
        ORDER BY gostota DESC NULLS LAST, obcina
        LIMIT %s
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, limit))
            rows = cur.fetchall()
    return [
        {"obcina": r[0], "gostota": float(r[1] or 0), "stevilo": int(r[2])}
        for r in rows
    ]


def export_obcine_map(day: date) -> list[dict]:
    sql = """
        SELECT
            o.ob_mid,
            o.ime_sl AS obcina,
            COALESCE(o.pov_km2, 0) AS pov_km2,
            COALESCE(s.stevilo, 0)::int AS stevilo
        FROM obcine o
        LEFT JOIN strele_obcina_dnevno s
            ON s.obcina_id = o.id AND s.datum = %s
        ORDER BY o.ob_mid
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (day,))
            rows = cur.fetchall()
    return [
        {"ob_id": int(r[0]), "obcina": r[1], "pov_km2": float(r[2] or 0), "stevilo": int(r[3])}
        for r in rows
    ]


def export_latest_date() -> date | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(datum) FROM strele_si_dnevno")
            row = cur.fetchone()
    return row[0] if row and row[0] else None


def export_archive_info() -> dict:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(datum), MAX(datum), COUNT(*)
                FROM strele_si_dnevno
                WHERE stevilo > 0
                """
            )
            od, do, count = cur.fetchone()
    return {
        "od": od.isoformat() if od else None,
        "do": do.isoformat() if do else None,
        "dni": int(count or 0),
    }
