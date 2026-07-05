"""Konfiguracija iz okoljskih spremenljivk."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_base_url: str
    poll_interval_sec: int
    regions_geojson: Path
    obcine_geojson: Path
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    timezone: str
    dedup_retention_hours: int
    bbox_padding_deg: float


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        api_base_url=os.getenv("API_BASE_URL", "https://test.meteoinfo.si").rstrip("/"),
        poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "300")),
        regions_geojson=Path(
            os.getenv("REGIONS_GEOJSON", str(ROOT / "data" / "SR.geojson"))
        ),
        obcine_geojson=Path(
            os.getenv("OBCINE_GEOJSON", str(ROOT / "data" / "OB.geojson"))
        ),
        min_lat=float(os.getenv("SI_MIN_LAT", "nan")),
        max_lat=float(os.getenv("SI_MAX_LAT", "nan")),
        min_lon=float(os.getenv("SI_MIN_LON", "nan")),
        max_lon=float(os.getenv("SI_MAX_LON", "nan")),
        timezone=os.getenv("TIMEZONE", "Europe/Ljubljana"),
        dedup_retention_hours=int(os.getenv("DEDUP_RETENTION_HOURS", "26")),
        bbox_padding_deg=float(os.getenv("SI_BBOX_PADDING_DEG", "0.02")),
    )
