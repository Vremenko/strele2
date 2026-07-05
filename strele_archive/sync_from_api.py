"""Sinhrinizacija Meteoinfo agregatov v lokalno bazo (rezerva)."""

from __future__ import annotations

import logging

from strele_archive.archive import sync_incremental

logger = logging.getLogger(__name__)


def sync_days(days: int | None = None) -> dict:
    return sync_incremental(days=days, full=False)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    stats = sync_days()
    print(stats)


if __name__ == "__main__":
    main()
