"""Naloži iz __pycache__ (obnovitev manjkajočih .py virov)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _pyc_path(name: str) -> Path | None:
    base = Path(__file__).resolve().parent
    cache = base / "__pycache__"
    major, minor = sys.version_info[:2]
    preferred = cache / f"{name}.cpython-{major}{minor}.pyc"
    if preferred.is_file() and preferred.stat().st_size > 500:
        return preferred
    for pyc in sorted(
        cache.glob(f"{name}.cpython-*.pyc"),
        key=lambda path: path.stat().st_size,
        reverse=True,
    ):
        if pyc.stat().st_size > 500:
            return pyc
    return None


def load_pyc_module(name: str, *, impl_name: str | None = None) -> ModuleType:
    """Naloži modul iz .pyc; impl_name prepreči kroženje z wrapper .py."""
    full_name = impl_name or f"strele_archive._pyc_{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    pyc = _pyc_path(name)
    if pyc is None:
        raise ImportError(f"Manjka {name}.py in .pyc v __pycache__")

    spec = importlib.util.spec_from_file_location(full_name, pyc)
    if spec is None or spec.loader is None:
        raise ImportError(f"Ne morem naložiti {pyc}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod
