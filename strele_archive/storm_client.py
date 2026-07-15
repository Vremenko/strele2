"""StormAPI odjemalec z časovnimi rezinami in paginacijo."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from strele_archive.config import Settings
from strele_archive.regions import RegionIndex

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_MINUTES = 15
DEFAULT_OVERLAP_SECONDS = 120
DEFAULT_PAGE_SIZE = 5000
DEFAULT_TIMEOUT_SEC = 60


def api_bbox(settings: Settings, regions: RegionIndex) -> dict[str, float]:
    import math

    coords = (settings.min_lat, settings.max_lat, settings.min_lon, settings.max_lon)
    if all(not math.isnan(v) for v in coords):
        return {
            "min_lat": settings.min_lat,
            "max_lat": settings.max_lat,
            "min_lon": settings.min_lon,
            "max_lon": settings.max_lon,
        }
    return regions.bbox_for_api(settings.bbox_padding_deg)


def _strike_key(strike: dict[str, Any]) -> tuple[float, float, str]:
    return (float(strike["lat"]), float(strike["lon"]), str(strike["ts_utc"]))


def _parse_ts(ts_raw: str) -> datetime:
    return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))


def _iter_time_chunks(
    time_from: datetime,
    time_to: datetime,
    *,
    chunk_minutes: int,
    overlap_seconds: int,
) -> list[tuple[datetime, datetime]]:
    if time_from >= time_to:
        return []
    overlap = timedelta(seconds=overlap_seconds)
    chunk = timedelta(minutes=chunk_minutes)
    chunks: list[tuple[datetime, datetime]] = []
    cursor = time_from
    while cursor < time_to:
        chunk_end = min(cursor + chunk, time_to)
        chunks.append((cursor, chunk_end))
        if chunk_end >= time_to:
            break
        cursor = chunk_end - overlap
    return chunks


def fetch_strikes_page(
    settings: Settings,
    bbox: dict[str, float],
    *,
    time_from_utc: datetime,
    time_to_utc: datetime,
    limit: int | None = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Vrne (udari, pagination_supported).
    pagination_supported=False pomeni, da StormAPI ne podpira limit/offset (en klic = cel interval).
    """
    url = f"{settings.api_base_url.rstrip('/')}/api/v1/strele"
    params: dict[str, Any] = {
        **bbox,
        "time_from_utc": time_from_utc.isoformat().replace("+00:00", "Z"),
        "time_to_utc": time_to_utc.isoformat().replace("+00:00", "Z"),
    }
    pagination_supported = limit is not None
    if limit is not None:
        params["limit"] = limit
        params["offset"] = offset
    response = requests.get(url, params=params, timeout=timeout_sec)
    if response.status_code == 422 and limit is not None:
        params.pop("limit", None)
        params.pop("offset", None)
        pagination_supported = False
        response = requests.get(url, params=params, timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError(f"Pričakovan seznam udarcev, dobil: {type(data)!r}")
    return data, pagination_supported


def fetch_strikes_interval(
    settings: Settings,
    bbox: dict[str, float],
    time_from_utc: datetime,
    time_to_utc: datetime,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Paginiran zajem za en časovni interval."""
    merged: dict[tuple[float, float, str], dict[str, Any]] = {}
    offset = 0
    pagination_supported = True
    while True:
        page, page_paginated = fetch_strikes_page(
            settings,
            bbox,
            time_from_utc=time_from_utc,
            time_to_utc=time_to_utc,
            limit=page_size if pagination_supported else None,
            offset=offset,
            timeout_sec=timeout_sec,
        )
        if not page_paginated:
            pagination_supported = False
        if not page:
            break
        for strike in page:
            merged[_strike_key(strike)] = strike
        if not pagination_supported or len(page) < page_size:
            break
        offset += page_size
    return list(merged.values())


def fetch_strikes_window(
    settings: Settings,
    regions: RegionIndex,
    time_from_utc: datetime,
    time_to_utc: datetime,
    *,
    chunk_minutes: int = DEFAULT_CHUNK_MINUTES,
    overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """
    Zanesljiv zajem vseh udarov v oknu: časovne rezin + paginacija + deduplikacija.
    """
    bbox = api_bbox(settings, regions)
    start = time_from_utc if time_from_utc.tzinfo else time_from_utc.replace(tzinfo=timezone.utc)
    end = time_to_utc if time_to_utc.tzinfo else time_to_utc.replace(tzinfo=timezone.utc)

    merged: dict[tuple[float, float, str], dict[str, Any]] = {}
    chunks = _iter_time_chunks(start, end, chunk_minutes=chunk_minutes, overlap_seconds=overlap_seconds)
    for chunk_from, chunk_to in chunks:
        chunk_strikes = fetch_strikes_interval(
            settings,
            bbox,
            chunk_from,
            chunk_to,
            page_size=page_size,
            timeout_sec=timeout_sec,
        )
        for strike in chunk_strikes:
            merged[_strike_key(strike)] = strike

    logger.debug(
        "fetch_strikes_window: %s chunks, %s unique strikes [%s, %s)",
        len(chunks),
        len(merged),
        start.isoformat(),
        end.isoformat(),
    )
    return list(merged.values())
