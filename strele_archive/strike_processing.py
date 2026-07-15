"""Geografska razvrstitev in agregacija udarov (enako kot ingest)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from strele_archive.obcine import ObcinaIndex
from strele_archive.regions import RegionIndex
from strele_archive.timezone_utils import local_parts


@dataclass
class ClassifiedStrike:
    lat: float
    lon: float
    ts_utc: datetime
    local_date: date
    local_hour: int
    regija_id: int
    obcina_id: int | None


@dataclass
class DayAggregates:
    day: date
    national_hourly: Counter[int] = field(default_factory=Counter)
    regija_daily: Counter[int] = field(default_factory=Counter)
    regija_hourly: Counter[tuple[int, int]] = field(default_factory=Counter)
    obcina_daily: Counter[int] = field(default_factory=Counter)

    @property
    def national_daily(self) -> int:
        return sum(self.national_hourly.values())


def parse_strike_ts(strike: dict) -> datetime:
    return datetime.fromisoformat(str(strike["ts_utc"]).replace("Z", "+00:00"))


def classify_strikes(
    strikes: list[dict],
    regions: RegionIndex,
    obcine: ObcinaIndex,
    tz_name: str,
) -> tuple[list[ClassifiedStrike], int]:
    """Razvrsti udare z PiP (regije + občine). Vrne (inside, outside_count)."""
    inside: list[ClassifiedStrike] = []
    outside = 0
    for strike in strikes:
        lat = float(strike["lat"])
        lon = float(strike["lon"])
        regija_id = regions.lookup(lon, lat)
        if regija_id is None:
            outside += 1
            continue
        ts_utc = parse_strike_ts(strike)
        local_date, local_hour = local_parts(ts_utc, tz_name)
        obcina_id = obcine.lookup(lon, lat)
        inside.append(
            ClassifiedStrike(
                lat=lat,
                lon=lon,
                ts_utc=ts_utc,
                local_date=local_date,
                local_hour=local_hour,
                regija_id=regija_id,
                obcina_id=obcina_id,
            )
        )
    return inside, outside


def aggregate_for_day(classified: list[ClassifiedStrike], day: date) -> DayAggregates:
    """Agregati samo za izbrani lokalni koledarski dan."""
    aggs = DayAggregates(day=day)
    for strike in classified:
        if strike.local_date != day:
            continue
        aggs.national_hourly[strike.local_hour] += 1
        aggs.regija_daily[strike.regija_id] += 1
        aggs.regija_hourly[(strike.regija_id, strike.local_hour)] += 1
        if strike.obcina_id is not None:
            aggs.obcina_daily[strike.obcina_id] += 1
    return aggs


def hourly_series(aggs: DayAggregates) -> list[dict]:
    return [{"ura": h, "stevilo": aggs.national_hourly.get(h, 0)} for h in range(24)]
