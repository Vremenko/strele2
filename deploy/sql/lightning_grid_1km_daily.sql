-- Idempotent schema for 1×1 km daily grid aggregates (strike density).
-- Runs in the same PostGIS database as raw strikes (UDARI_DATABASE_URL / schema strele.*).

CREATE TABLE IF NOT EXISTS lightning_grid_1km_daily (
  day_local    date        NOT NULL,
  grid_x       bigint      NOT NULL,
  grid_y       bigint      NOT NULL,
  strike_count integer     NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day_local, grid_x, grid_y)
);

CREATE INDEX IF NOT EXISTS lightning_grid_1km_daily_day_local_idx
  ON lightning_grid_1km_daily (day_local);

CREATE INDEX IF NOT EXISTS lightning_grid_1km_daily_grid_xy_idx
  ON lightning_grid_1km_daily (grid_x, grid_y);
