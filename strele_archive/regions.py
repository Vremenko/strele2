"""Nalaganje statističnih regij in point-in-polygon."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree


@dataclass(frozen=True)
class Region:
    id: int
    name: str
    sr_mid: int
    geometry: object
    prepared: object


class RegionIndex:
    def __init__(self, regions: list[Region]) -> None:
        self._regions = regions
        self._geometries = [r.geometry for r in regions]
        self._tree = STRtree(self._geometries)
        self._slovenia = prep(unary_union(self._geometries))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """min_lon, min_lat, max_lon, max_lat (EPSG:4326)."""
        minx, miny, maxx, maxy = unary_union(self._geometries).bounds
        return float(minx), float(miny), float(maxx), float(maxy)

    def bbox_for_api(self, padding_deg: float = 0.02) -> dict[str, float]:
        min_lon, min_lat, max_lon, max_lat = self.bounds
        return {
            "min_lon": min_lon - padding_deg,
            "min_lat": min_lat - padding_deg,
            "max_lon": max_lon + padding_deg,
            "max_lat": max_lat + padding_deg,
        }

    def contains(self, lon: float, lat: float) -> bool:
        return self._slovenia.contains(Point(lon, lat))

    def lookup(self, lon: float, lat: float) -> int | None:
        if not self.contains(lon, lat):
            return None
        point = Point(lon, lat)
        for idx in self._tree.query(point):
            region = self._regions[int(idx)]
            if region.prepared.contains(point):
                return region.id
        return None

    @property
    def regions(self) -> list[Region]:
        return self._regions


def load_regions(geojson_path: Path) -> RegionIndex:
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    regions: list[Region] = []

    for feature in data["features"]:
        props = feature["properties"]
        geom = shape(feature["geometry"])
        regions.append(
            Region(
                id=int(props["SR_ID"]),
                name=str(props["SR_UIME"]),
                sr_mid=int(props["SR_MID"]),
                geometry=geom,
                prepared=prep(geom),
            )
        )

    regions.sort(key=lambda r: r.id)
    return RegionIndex(regions)
