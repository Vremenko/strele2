"""Testi slovenskega oblikovanja celih števil (ločilo tisočic = pika)."""

from __future__ import annotations

import unittest

from strele_archive.sl_number_format import format_sl_int


class FormatSlIntTest(unittest.TestCase):
    def test_below_thousand(self):
        self.assertEqual(format_sl_int(0), "0")
        self.assertEqual(format_sl_int(1), "1")
        self.assertEqual(format_sl_int(999), "999")

    def test_above_thousand(self):
        self.assertEqual(format_sl_int(1000), "1.000")
        self.assertEqual(format_sl_int(1725), "1.725")
        self.assertEqual(format_sl_int(68195), "68.195")

    def test_above_million(self):
        self.assertEqual(format_sl_int(1_000_000), "1.000.000")
        self.assertEqual(format_sl_int(12_345_678), "12.345.678")

    def test_negative_and_invalid(self):
        self.assertEqual(format_sl_int(-1725), "-1.725")
        self.assertEqual(format_sl_int(None), "—")
        self.assertEqual(format_sl_int(""), "—")
        self.assertEqual(format_sl_int("x"), "—")


if __name__ == "__main__":
    unittest.main()
