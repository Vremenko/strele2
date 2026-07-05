-- Sledenje arhiviranim (nepreracunavani) dnevom.
BEGIN;

CREATE TABLE IF NOT EXISTS meta_sync_dnevno (
    datum     DATE PRIMARY KEY,
    stevilo   INTEGER NOT NULL DEFAULT 0 CHECK (stevilo >= 0),
    vir       TEXT NOT NULL DEFAULT 'meteoinfo_obcine',
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_meta_sync_dnevno_synced_at
    ON meta_sync_dnevno (synced_at DESC);

COMMIT;
