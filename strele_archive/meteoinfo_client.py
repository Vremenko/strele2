"""Odjemalec Meteoinfo strele agregatov API."""

from __future__ import annotations

import time
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import requests

from strele_archive.config import Settings, get_settings


class MeteoinfoClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tz = ZoneInfo(self._settings.timezone)

    @property
    def api_base_url(self) -> str:
        return self._settings.api_base_url.rstrip("/")

    def _get(self, path: str, params: dict | None = None, *, attempts: int = 3) -> dict:
        url = f"{self.api_base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = requests.get(url, params=params, timeout=45)
                if response.status_code == 429 and attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _utc_iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    def day_window_utc(self, day: date) -> tuple[str, str]:
        start = datetime.combine(day, dt_time.min, tzinfo=self._tz)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        return self._utc_iso(start), self._utc_iso(end)

    def fetch_municipalities(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 500,
    ) -> list[dict]:
        data = self._get(
            "/api/v1/strele/heatmap/municipalities",
            {
                "time_from_utc": self._utc_iso(start),
                "time_to_utc": self._utc_iso(end),
                "normalize": "false",
                "sort": "count",
                "limit": limit,
            },
        )
        cells = data.get("cells") or []
        return [
            {"code": int(cell.get("code", cell.get("key", 0))), "count": int(cell.get("count", 0))}
            for cell in cells
        ]

    def fetch_slovenia_day_total(self, day: date) -> int:
        start_s, end_s = self.day_window_utc(day)
        t0 = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
        cells = self.fetch_municipalities(t0, t1)
        return sum(int(cell.get("count", 0)) for cell in cells)

    def fetch_slovenia_hourly(self, day: date) -> list[dict]:
        start_s, end_s = self.day_window_utc(day)
        payload = self.fetch_aggregates_series(
            datetime.fromisoformat(start_s.replace("Z", "+00:00")),
            datetime.fromisoformat(end_s.replace("Z", "+00:00")),
            bucket="hour",
        )
        totals = [0] * 24
        for point in payload.get("series") or []:
            ts = datetime.fromisoformat(str(point["t"]).replace("Z", "+00:00"))
            local = ts.astimezone(self._tz)
            totals[local.hour] += int(point.get("count", 0))
        return [{"ura": hour, "stevilo": totals[hour]} for hour in range(24)]

    def fetch_aggregates_series(
        self,
        start: datetime,
        end: datetime,
        *,
        bucket: str = "day",
        group_by: str = "none",
    ) -> dict:
        return self._get(
            "/api/v1/strele/aggregates/series",
            {
                "bucket": bucket,
                "time_from_utc": self._utc_iso(start),
                "time_to_utc": self._utc_iso(end),
                "group_by": group_by,
            },
        )
