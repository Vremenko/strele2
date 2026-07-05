-- Agregatni arhiv strel za Slovenijo (brez shranjevanja natančnih koordinat).
-- PostgreSQL 14+

BEGIN;

CREATE TABLE IF NOT EXISTS regije (
    id       SMALLINT PRIMARY KEY,
    ime_sl   TEXT NOT NULL,
    sr_mid   INTEGER
);

CREATE TABLE IF NOT EXISTS strele_si_dnevno (
    datum    DATE NOT NULL PRIMARY KEY,
    stevilo  INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0)
);

CREATE TABLE IF NOT EXISTS strele_si_urno (
    datum    DATE NOT NULL,
    ura      SMALLINT NOT NULL CHECK (ura BETWEEN 0 AND 23),
    stevilo  INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
    PRIMARY KEY (datum, ura)
);

CREATE TABLE IF NOT EXISTS strele_regija_dnevno (
    regija_id SMALLINT NOT NULL REFERENCES regije (id),
    datum     DATE NOT NULL,
    stevilo   INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
    PRIMARY KEY (regija_id, datum)
);

CREATE TABLE IF NOT EXISTS strele_regija_urno (
    regija_id SMALLINT NOT NULL REFERENCES regije (id),
    datum     DATE NOT NULL,
    ura       SMALLINT NOT NULL CHECK (ura BETWEEN 0 AND 23),
    stevilo   INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
    PRIMARY KEY (regija_id, datum, ura)
);

-- Za deduplikacijo med poll-i ( začasno, ~26 h ).
CREATE TABLE IF NOT EXISTS strele_dedup (
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    ts_utc     TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (lat, lon, ts_utc)
);

CREATE INDEX IF NOT EXISTS idx_strele_dedup_created_at
    ON strele_dedup (created_at);

CREATE INDEX IF NOT EXISTS idx_strele_si_dnevno_datum
    ON strele_si_dnevno (datum DESC);

CREATE INDEX IF NOT EXISTS idx_strele_si_urno_datum
    ON strele_si_urno (datum DESC, ura);

CREATE INDEX IF NOT EXISTS idx_strele_regija_dnevno_datum
    ON strele_regija_dnevno (datum DESC);

CREATE INDEX IF NOT EXISTS idx_strele_regija_urno_datum
    ON strele_regija_urno (datum DESC, ura);

CREATE TABLE IF NOT EXISTS obcine (
    id       SMALLINT PRIMARY KEY,
    ime_sl   TEXT NOT NULL,
    ob_mid   INTEGER,
    pov_km2  DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS strele_obcina_dnevno (
    obcina_id SMALLINT NOT NULL REFERENCES obcine (id),
    datum     DATE NOT NULL,
    stevilo   INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
    PRIMARY KEY (obcina_id, datum)
);

CREATE INDEX IF NOT EXISTS idx_strele_obcina_dnevno_datum
    ON strele_obcina_dnevno (datum DESC, stevilo DESC);

COMMIT;
