"""Testi javnega SI widget embeda (brez zemljevida in grafa)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "web" / "public" / "obcina-si-embed.html"
SERVER_PATH = ROOT / "strele_archive" / "obcine_public_server.py"


def test_obcina_si_embed_html_file_exists():
    assert HTML_PATH.is_file()
    html = HTML_PATH.read_text(encoding="utf-8")
    assert "chart.umd" not in html
    assert "Chart.js" not in html
    assert "leaflet" not in html.lower()
    assert "maptiler" not in html.lower()
    assert "/si-widget" in html
    assert "SLOVENIJA" in html
    assert "strele-embed-resize" in html
    assert 'themeParam' in html or 'theme' in html


def test_server_registers_si_embed_route_with_csp():
    src = SERVER_PATH.read_text(encoding="utf-8")
    assert '@app.get("/public/obcina-si-embed.html")' in src
    assert "STRELKO_SI_WIDGET_FRAME_ANCESTORS" in src
    assert "frame-ancestors" in src
    assert "meteoinfo.si" in src
