"""Slovensko oblikovanje celih števil (pika kot ločilo tisočic)."""

from __future__ import annotations

from typing import Any


def format_sl_int(value: Any) -> str:
    """
    Oblikuj celo število s piko kot ločilom tisočic.

    Primeri: 999 → \"999\", 1000 → \"1.000\", 68195 → \"68.195\".
    Neoblikovane / neveljavne vrednosti → \"—\".
    """
    if value is None or value is False:
        return "—"
    if isinstance(value, bool):
        return "—"
    if isinstance(value, str) and not value.strip():
        return "—"
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return "—"
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    parts: list[str] = []
    while len(digits) > 3:
        parts.append(digits[-3:])
        digits = digits[:-3]
    parts.append(digits)
    return sign + ".".join(reversed(parts))
