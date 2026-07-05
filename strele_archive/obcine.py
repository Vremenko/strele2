"""Nalaganje občin in point-in-polygon."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree


@dataclass(frozen=True)
class Obcina:
    id: int
    name: str
    ob_mid: int
    pov_km2: float
    geometry: object
    prepared: object


class ObcinaIndex:
    def __init__(self, obcine: list[Obcina]) -> None:
        self._obcine = obcine
        self._tree = STRtree([o.geometry for o in obcine])

    def lookup(self, lon: float, lat: float) -> int | None:
        point = Point(lon, lat)
        for idx in self._tree.query(point):
            obcina = self._obcine[int(idx)]
            if obcina.prepared.contains(point):
                return obcina.id
        return None

    @property
    def obcine(self) -> list[Obcina]:
        return self._obcine


def load_obcine(geojson_path: Path) -> ObcinaIndex:
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    obcine: list[Obcina] = []

    for feature in data["features"]:
        props = feature["properties"]
        geom = shape(feature["geometry"])
        obcine.append(
            Obcina(
                id=int(props["OB_ID"]),
                name=str(props["OB_UIME"]),
                ob_mid=int(props["OB_MID"]),
                pov_km2=float(props["POV_KM2"]),
                geometry=geom,
                prepared=prep(geom),
            )
        )

    obcine.sort(key=lambda o: o.id)
    return ObcinaIndex(obcine)
