"""Preverjanje velikosti markerjev na zemljevidu widgeta."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDGET_HTML = ROOT / "web" / "public" / "obcina-widget.html"
PREVIEW_HTML = ROOT / "web" / "public" / "obcina-preview.html"
EMBED_HTML = ROOT / "web" / "public" / "obcina-embed.html"
EST_JS = ROOT / "web" / "public" / "estimated-strike-time.js"


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

    def test_widgets_load_shared_estimated_strike_time(self):
        self.assertTrue(EST_JS.is_file())
        for path in (WIDGET_HTML, PREVIEW_HTML, EMBED_HTML):
            html = path.read_text(encoding="utf-8")
            self.assertIn("/widget/public/estimated-strike-time.js", html)
            self.assertIn("formatEstimatedStrikeDateTime", html)
            self.assertIn("bindStrikeMarkerTip", html)

    def test_widgets_use_shared_sl_int_format(self):
        sl_js = ROOT / "web" / "public" / "sl-number-format.js"
        self.assertTrue(sl_js.is_file())
        for path in (WIDGET_HTML, PREVIEW_HTML, EMBED_HTML):
            html = path.read_text(encoding="utf-8")
            self.assertIn("/widget/public/sl-number-format.js", html)
            self.assertIn("formatSlInt(", html)
            self.assertNotIn("Intl.NumberFormat(\"sl-SI\")", html)
            self.assertIn("return formatSlInt(value);", html)


if __name__ == "__main__":
    unittest.main()
