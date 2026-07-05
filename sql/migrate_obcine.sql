-- Občine: tabele za obstoječe baze (po schema.sql).

BEGIN;

CREATE TABLE IF NOT EXISTS obcine (
    id       SMALLINT PRIMARY KEY,
    ime_sl   TEXT NOT NULL,
    ob_mid   INTEGER
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
