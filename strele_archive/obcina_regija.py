"""Preslikava občine → statistična regija (največje prekrivanje poligonov GURS)."""

from __future__ import annotations

from strele_archive.config import get_settings
from strele_archive.obcine import load_obcine
from strele_archive.regions import load_regions

_map: dict[int, int] | None = None


def _build_map() -> dict[int, int]:
    settings = get_settings()
    regions = load_regions(settings.regions_geojson)
    obcine = load_obcine(settings.obcine_geojson)
    mapping: dict[int, int] = {}

    for obcina in obcine.obcine:
        best_regija_id: int | None = None
        best_area = 0.0
        for region in regions.regions:
            if not obcina.geometry.intersects(region.geometry):
                continue
            area = obcina.geometry.intersection(region.geometry).area
            if area > best_area:
                best_area = area
                best_regija_id = region.id
        if best_regija_id is not None:
            mapping[obcina.id] = best_regija_id

    return mapping


def get_obcina_regija_map() -> dict[int, int]:
    global _map
    if _map is None:
        _map = _build_map()
    return _map
