"""PostgreSQL operacije."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator

import psycopg


class Database:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self._database_url) as conn:
            yield conn

    def upsert_regije(self, regions: list[tuple[int, str, int]]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO regije (id, ime_sl, sr_mid)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET ime_sl = EXCLUDED.ime_sl,
                        sr_mid = EXCLUDED.sr_mid
                    """,
                    regions,
                )
            conn.commit()

    def upsert_obcine(self, obcine: list[tuple[int, str, int, float]]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO obcine (id, ime_sl, ob_mid, pov_km2)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET ime_sl = EXCLUDED.ime_sl,
                        ob_mid = EXCLUDED.ob_mid,
                        pov_km2 = EXCLUDED.pov_km2
                    """,
                    obcine,
                )
            conn.commit()

    def process_new_strikes(
        self,
        strikes: list[tuple[float, float, datetime, date, int, int, int | None]],
    ) -> int:
        """
        strikes: (lat, lon, ts_utc, local_date, local_hour, regija_id, obcina_id)
        Vrne število novih udarcev, ki so bili šteti.
        """
        if not strikes:
            return 0

        counted = 0
        with self.connect() as conn:
            with conn.cursor() as cur:
                for lat, lon, ts_utc, datum, ura, regija_id, obcina_id in strikes:
                    cur.execute(
                        """
                        INSERT INTO strele_dedup (lat, lon, ts_utc)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING 1
                        """,
                        (lat, lon, ts_utc),
                    )
                    if cur.fetchone() is None:
                        continue

                    cur.execute(
                        """
                        INSERT INTO strele_si_dnevno (datum, stevilo)
                        VALUES (%s, 1)
                        ON CONFLICT (datum) DO UPDATE
                        SET stevilo = strele_si_dnevno.stevilo + 1
                        """,
                        (datum,),
                    )
                    cur.execute(
                        """
                        INSERT INTO strele_si_urno (datum, ura, stevilo)
                        VALUES (%s, %s, 1)
                        ON CONFLICT (datum, ura) DO UPDATE
                        SET stevilo = strele_si_urno.stevilo + 1
                        """,
                        (datum, ura),
                    )
                    cur.execute(
                        """
                        INSERT INTO strele_regija_dnevno (regija_id, datum, stevilo)
                        VALUES (%s, %s, 1)
                        ON CONFLICT (regija_id, datum) DO UPDATE
                        SET stevilo = strele_regija_dnevno.stevilo + 1
                        """,
                        (regija_id, datum),
                    )
                    cur.execute(
                        """
                        INSERT INTO strele_regija_urno (regija_id, datum, ura, stevilo)
                        VALUES (%s, %s, %s, 1)
                        ON CONFLICT (regija_id, datum, ura) DO UPDATE
                        SET stevilo = strele_regija_urno.stevilo + 1
                        """,
                        (regija_id, datum, ura),
                    )
                    if obcina_id is not None:
                        cur.execute(
                            """
                            INSERT INTO strele_obcina_dnevno (obcina_id, datum, stevilo)
                            VALUES (%s, %s, 1)
                            ON CONFLICT (obcina_id, datum) DO UPDATE
                            SET stevilo = strele_obcina_dnevno.stevilo + 1
                            """,
                            (obcina_id, datum),
                        )
                    counted += 1
            conn.commit()
        return counted

    def cleanup_dedup(self, retention_hours: int) -> int:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM strele_dedup
                    WHERE created_at < now() - make_interval(hours => %s)
                    """,
                    (retention_hours,),
                )
                deleted = cur.rowcount
            conn.commit()
            return deleted
