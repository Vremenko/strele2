"""Uskladitev dnevnega grafa občinskega widgeta z živimi StormAPI podatki."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_LJ_TZ = ZoneInfo("Europe/Ljubljana")


@dataclass(frozen=True)
class StormObcinaLiveStats:
    total_24h: int
    last_hour: int
    hourly: list[dict]
    today_from_midnight: int


class StormUnavailable(Exception):
    """StormAPI ni dosegljiv (429, timeout, …)."""


def local_today(now_utc: datetime | None = None) -> date:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_LJ_TZ).date()


def today_count_from_hourly_buckets(
    by_hour: dict[str, int],
    *,
    today: date | None = None,
    now_utc: datetime | None = None,
) -> int:
    """Sešteje urne buckete od lokalne polnoči do zdaj (Europe/Ljubljana)."""
    day = today or local_today(now_utc)
    prefix = day.isoformat()
    return sum(count for key, count in by_hour.items() if key[:10] == prefix)


def parse_storm_hourly_payload(
    payload: dict,
    *,
    now_utc: datetime | None = None,
) -> StormObcinaLiveStats:
    """Iz StormAPI /strele/aggregates/series (bucket=hour) izračuna rolling in današnji dan."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    groups = payload.get("groups") or []
    points = groups[0].get("points", []) if groups else []
    by_hour: dict[str, int] = {}
    for pt in points:
        ts = str(pt.get("t", ""))
        if not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        local = dt.astimezone(_LJ_TZ)
        key = local.strftime("%Y-%m-%dT%H:00:00")
        by_hour[key] = by_hour.get(key, 0) + int(pt.get("count") or 0)

    hourly: list[dict] = []
    cursor = now.astimezone(_LJ_TZ).replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    for _ in range(24):
        key = cursor.strftime("%Y-%m-%dT%H:00:00")
        hourly.append({
            "ura": cursor.hour,
            "label": f"{cursor.hour:02d}:00",
            "stevilo": by_hour.get(key, 0),
            "t": key,
        })
        cursor += timedelta(hours=1)

    total = int(payload.get("total") or sum(h["stevilo"] for h in hourly))
    last_hour = hourly[-1]["stevilo"] if hourly else 0
    today_from_midnight = today_count_from_hourly_buckets(by_hour, now_utc=now)
    return StormObcinaLiveStats(
        total_24h=total,
        last_hour=last_hour,
        hourly=hourly,
        today_from_midnight=today_from_midnight,
    )


def merge_live_today_into_daily(
    daily: list[dict],
    today: date,
    today_count: int,
) -> list[dict]:
    """Zamenja današnji stolpec v daily[] z live vrednostjo; pretekle dni ostanejo."""
    today_str = str(today)
    merged: list[dict] = []
    replaced = False
    for row in daily:
        datum = str(row.get("datum", ""))[:10]
        if datum == today_str:
            merged.append({"datum": today_str, "stevilo": int(today_count)})
            replaced = True
        else:
            merged.append(dict(row))
    if not replaced:
        merged.append({"datum": today_str, "stevilo": int(today_count)})
    return merged


def merge_live_today_into_obcine_map_rows(
    rows: list[dict],
    live_by_ob_mid: dict[int, int],
) -> list[dict]:
    """
    Prišteje današnje žive števce k občinam.

    Predpogoj: rows NE smejo že vsebovati arhivskega zapisa za danes
    (sicer bi prišlo do dvojnega štetja). Live torej nadomesti morebitni
    delni arhiv — enaka zamenjalna semantika kot merge_live_today_into_daily.
    """
    merged: list[dict] = []
    for row in rows:
        mid = int(row["ob_id"])
        live = int(live_by_ob_mid.get(mid, 0) or 0)
        stevilo = int(row.get("stevilo") or 0) + live
        dni = int(row.get("dni_z_nevihto") or 0)
        if live > 0:
            dni += 1
        out = dict(row)
        out["stevilo"] = stevilo
        out["dni_z_nevihto"] = dni
        merged.append(out)
    return merged


def archive_end_excluding_live_today(
    start: date,
    end: date,
    today: date,
) -> date | None:
    """
    Vrne zgornjo mejo arhivskega obdobja, ko je danes pokrit z live.

    Če obdobje vključuje today: arhiv do včeraj (ali None, če ni preteklih dni).
    Sicer: celotno [start, end] (vrne end).
    """
    if start <= today <= end:
        archive_end = today - timedelta(days=1)
        if start <= archive_end:
            return archive_end
        return None
    return end


def daily_value_for_date(daily: list[dict], day: date) -> int:
    day_str = str(day)
    for row in daily:
        if str(row.get("datum", ""))[:10] == day_str:
            return int(row.get("stevilo") or 0)
    return 0


def recalc_period_total(daily: list[dict]) -> int:
    return sum(int(row.get("stevilo") or 0) for row in daily)


def recalc_peak(daily: list[dict]) -> dict | None:
    if not daily:
        return None
    peak = max(daily, key=lambda d: int(d.get("stevilo") or 0))
    if int(peak.get("stevilo") or 0) <= 0:
        return None
    return {"datum": str(peak["datum"]), "stevilo": int(peak["stevilo"])}


def apply_live_daily_sync(
    daily: list[dict],
    *,
    data_source: str,
    today_live: int | None,
    today: date | None = None,
) -> tuple[list[dict], int, dict | None]:
    """
    Po potrebi zamenja današnji stolpec in ponovno izračuna period_total ter peak.
    Vrne (daily, period_total, peak).
    """
    day = today or local_today()
    if data_source == "live" and today_live is not None:
        daily = merge_live_today_into_daily(daily, day, today_live)
    return daily, recalc_period_total(daily), recalc_peak(daily)
