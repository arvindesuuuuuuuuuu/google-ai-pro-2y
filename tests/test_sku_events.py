"""Tests for offer event parsing, secret stripping, deduplication, and report validation."""

import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner_core

TOKEN_LIKE_KEYS = (
    "token",
    "offerToken",
    "purchaseToken",
    "secret",
    "email",
    "account",
)


def _offer(**overrides):
    event = {
        "ev": "offer",
        "product": "g1.2tb.ai.1month_eft",
        "id": "offer-1",
        "period": "P1M",
        "micros": "990000",
        "title": "Standard",
    }
    event.update(overrides)
    return event


class OfferEventTests(unittest.TestCase):
    def test_parse_offer_events_skips_non_offer_and_malformed_events(self):
        events = [
            _offer(ev="log", id="not-an-offer"),
            _offer(id="empty-product", product=""),
            _offer(id="", product="g1.2tb.ai.1month_eft"),
            {"ev": "offer", "product": "g1.2tb.ai.1month_eft", "micros": 1},
            {"type": "send", "payload": _offer(id="from-envelope", micros=100)},
            _offer(id="numeric-micros", micros=250000),
            _offer(id="non-numeric-micros", micros="free"),
            _offer(id="name-title", name="From name field", title=None),
            "not-an-event-object",
        ]

        rows = sku_scanner_core.parse_offer_events(events)

        self.assertEqual(
            [row.offer_id for row in rows],
            ["from-envelope", "numeric-micros", "non-numeric-micros", "name-title"],
        )
        self.assertEqual(rows[0].sku, "g1.2tb.ai.1month_eft")
        self.assertEqual(rows[0].period, "P1M")
        self.assertEqual(rows[0].micros, 100)
        self.assertEqual(rows[1].micros, 250000)
        self.assertIsNone(rows[2].micros)
        self.assertEqual(rows[3].title, "From name field")

    def test_parse_offer_events_drops_all_token_like_fields(self):
        event = _offer()
        for key in TOKEN_LIKE_KEYS:
            event[key] = "dummy-secret-value"

        rows = sku_scanner_core.parse_offer_events([event])

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            set(dataclasses.asdict(rows[0])),
            {"sku", "offer_id", "period", "micros", "title"},
        )
        self.assertFalse(set(TOKEN_LIKE_KEYS) & set(dataclasses.asdict(rows[0])))
        self.assertEqual(rows[0].offer_id, "offer-1")

    def test_dedupe_rows_keeps_first_row_for_identical_offer_identity(self):
        events = [
            _offer(token="first-token", title="First"),
            _offer(token="second-token", title="Second"),
        ]
        rows = sku_scanner_core.parse_offer_events(events)
        self.assertEqual(len(rows), 2)

        deduped = sku_scanner_core.dedupe_rows(rows)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].title, "First")
        self.assertEqual(
            set(dataclasses.asdict(deduped[0])),
            {"sku", "offer_id", "period", "micros", "title"},
        )

    def test_parse_report_rejects_non_json_non_list_and_bad_rows(self):
        invalid_payloads = (
            "{this is not json",
            '{"sku": "g1.2tb.ai.1month_eft"}',
            '[{"foo": "bar"}]',
            "[42]",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as caught:
                    sku_scanner_core.parse_report(payload)
                self.assertTrue(str(caught.exception).strip())


if __name__ == "__main__":
    unittest.main()
