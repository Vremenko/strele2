"""Preverjanje velikosti markerjev na zemljevidu widgeta."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDGET_HTML = ROOT / "web" / "public" / "obcina-widget.html"


class ObcinaWidgetMapStyleTest(unittest.TestCase):
    def test_slovenia_uses_smaller_canvas_markers(self):
        html = WIDGET_HTML.read_text(encoding="utf-8")
        self.assertIn("const markerOpts = useCanvas", html)
        self.assertIn("radius: 1.15", html)
        self.assertIn("radius: 2.5", html)
        self.assertIn("strikeCanvasRenderer = L.canvas", html)

    def test_slovenia_map_uses_canvas_flag(self):
        html = WIDGET_HTML.read_text(encoding="utf-8")
        self.assertIn("renderObcinaMap(view.strikes, geo, view.isNational)", html)


if __name__ == "__main__":
    unittest.main()
