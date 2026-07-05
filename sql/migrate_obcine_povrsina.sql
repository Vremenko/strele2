-- Površina občin (km²) iz GURS OB.geojson (POV_KM2).

BEGIN;

ALTER TABLE obcine
    ADD COLUMN IF NOT EXISTS pov_km2 DOUBLE PRECISION;

UPDATE obcine SET pov_km2 = NULL WHERE pov_km2 IS NOT NULL AND pov_km2 <= 0;

COMMIT;
