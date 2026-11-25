"""Behavior tests for sorting, Markdown table rendering, and JSON serialization."""

import dataclasses
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner_core


def _row(sku="g1.2tb.ai.1month_eft", offer_id="o1", period="P1M", micros=None, title=""):
    return sku_scanner_core.OfferRow(
        sku=sku, offer_id=offer_id, period=period, micros=micros, title=title
    )


class RowFormattingTests(unittest.TestCase):
    def test_sort_rows_is_stable_and_sorts_unknown_periods_last(self):
        self.assertEqual(sku_scanner_core.period_months("P1M"), 1)
        self.assertEqual(sku_scanner_core.period_months("P6M"), 6)
        self.assertEqual(sku_scanner_core.period_months("P1Y"), 12)
        self.assertEqual(sku_scanner_core.period_months("P2Y"), 24)
        self.assertGreater(sku_scanner_core.period_months("UNKNOWN"), 24)

        rows = [
            _row(offer_id="z", period="P2Y", micros=10),
            _row(offer_id="b", period="P1M", micros=20),
            _row(offer_id="a", period="P1M", micros=20),
            _row(offer_id="c", period="P6M", micros=30),
            _row(offer_id="u", period="UNKNOWN", micros=5),
            _row(sku="g1.ai.pro.1month_eft", offer_id="x", period="P1M", micros=1),
        ]
        self.assertEqual(
            [row.offer_id for row in sku_scanner_core.sort_rows(rows)],
            ["a", "b", "c", "z", "u", "x"],
        )

        first_tie = _row(offer_id="same", micros=7, title="first")
        second_tie = _row(offer_id="same", micros=7, title="second")
        self.assertEqual(
            sku_scanner_core.sort_rows([first_tie, second_tie]),
            [first_tie, second_tie],
        )

    def test_format_table_labels_zero_and_non_numeric_micros_and_handles_empty(self):
        rows = [
            _row(offer_id="zero", micros=0, title="Free"),
            _row(offer_id="unknown", micros=None, title="Unknown"),
            _row(offer_id="priced", micros=990000, title="Standard"),
        ]
        lines = sku_scanner_core.format_table(rows, limit=20)
        self.assertTrue(all(isinstance(line, str) for line in lines))
        text = "\n".join(lines)
        for offer_id in ("zero", "unknown", "priced"):
            self.assertIn(offer_id, text)
        self.assertTrue("990000" in text or "990,000" in text)
        self.assertTrue(any("micros" in line.lower() and "0" in line for line in lines))
        self.assertTrue(
            any(
                any(word in line.lower() for word in ("eligib", "renew"))
                for line in lines
            ),
            "Table footer must warn that micros=0 does not guarantee eligibility or renewal pricing",
        )

        empty_lines = sku_scanner_core.format_table([])
        self.assertTrue(empty_lines)
        self.assertTrue(all(isinstance(line, str) for line in empty_lines))
        self.assertTrue(
            any("no matching offers" in line.lower() for line in empty_lines),
            "Empty table must include a clear English empty-state message",
        )

    def test_format_table_reports_number_of_omitted_rows_over_limit(self):
        rows = [_row(offer_id=f"o{index}", micros=index) for index in range(1, 6)]
        limited = sku_scanner_core.format_table(rows, limit=2)
        full = sku_scanner_core.format_table(rows, limit=20)
        text = "\n".join(limited)

        self.assertLess(len(limited), len(full))
        self.assertIn("o1", text)
        self.assertIn("o2", text)
        self.assertNotIn("o3", text)
        self.assertIn("3", text)

    def test_to_json_round_trip_uses_allowlisted_keys_and_no_tokens(self):
        rows = [
            _row(offer_id="b", period="P2Y", micros=100, title="Two-Year Plan"),
            _row(offer_id="a", period="P1M", micros=0, title="Plan A (tax included)"),
        ]
        text = sku_scanner_core.to_json(rows, indent=2)

        self.assertIsInstance(text, str)
        self.assertNotIn("token", text.lower())
        self.assertIn("Plan A (tax included)", text)

        data = json.loads(text)
        self.assertIsInstance(data, list)
        expected = [dataclasses.asdict(row) for row in sku_scanner_core.sort_rows(rows)]
        self.assertEqual(data, expected)
        for item in data:
            self.assertEqual(
                set(item),
                {"sku", "offer_id", "period", "micros", "title"},
            )

        self.assertEqual(
            sku_scanner_core.parse_report(text),
            sku_scanner_core.sort_rows(rows),
        )


if __name__ == "__main__":
    unittest.main()
