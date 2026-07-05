"""Izvoz agregatov iz PostgreSQL."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

from strele_archive import export_impl
from strele_archive import meteoinfo_client as _  # noqa: F401

logger = logging.getLogger(__name__)


def _load_native_export() -> ModuleType | None:
    cache = Path(__file__).resolve().parent / "__pycache__"
    major, minor = sys.version_info[:2]
    pyc = cache / f"_export_native.cpython-{major}{minor}.pyc"
    if not pyc.is_file() or pyc.stat().st_size <= 5000:
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "strele_archive._export_native",
            pyc,
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        logger.warning("Native export modul ni na voljo: %s", exc)
        return None


_impl = _load_native_export() or export_impl

for _name in dir(_impl):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_impl, _name)
