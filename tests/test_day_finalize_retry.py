"""Odložitev nočnega zaključka, če je total 0 ali sumljivo nizek."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from strele_archive.day_reconcile import ReconcileResult, should_defer_finalize


def _result(*, total: int, prev: int | None) -> ReconcileResult:
    return ReconcileResult(
        day=datetime(2026, 7, 21).date(),
        fetched=total,
        inside=total,
        outside=0,
        national_total=total,
        applied=False,
        previous_daily=prev,
    )


class TestShouldDeferFinalize(unittest.TestCase):
    tz = ZoneInfo("Europe/Ljubljana")

    def test_zero_before_noon_defers(self) -> None:
        now = datetime(2026, 7, 22, 3, 30, tzinfo=self.tz)
        self.assertTrue(should_defer_finalize(_result(total=0, prev=0), now))

    def test_zero_after_noon_accepts(self) -> None:
        now = datetime(2026, 7, 22, 12, 0, tzinfo=self.tz)
        self.assertFalse(should_defer_finalize(_result(total=0, prev=0), now))

    def test_lower_than_archive_defers(self) -> None:
        now = datetime(2026, 7, 22, 2, 0, tzinfo=self.tz)
        self.assertTrue(should_defer_finalize(_result(total=10, prev=505), now))

    def test_equal_or_higher_accepts(self) -> None:
        now = datetime(2026, 7, 22, 2, 0, tzinfo=self.tz)
        self.assertFalse(should_defer_finalize(_result(total=505, prev=400), now))
        self.assertFalse(should_defer_finalize(_result(total=1, prev=1), now))

    def test_quiet_day_one_strike_accepts(self) -> None:
        """Majhna, a skladna številka (npr. 1) ni razlog za odložitev."""
        now = datetime(2026, 7, 22, 1, 0, tzinfo=self.tz)
        self.assertFalse(should_defer_finalize(_result(total=1, prev=0), now))
        self.assertFalse(should_defer_finalize(_result(total=1, prev=1), now))


if __name__ == "__main__":
    unittest.main()
