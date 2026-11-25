"""Behavior tests for SKU candidate generation, validation, and parsing.

Assumes modules reside in scripts/; uses no device, frida, or network.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner_core


class CandidateGenerationTests(unittest.TestCase):
    def test_generate_candidates_is_deterministic_unique_and_ordered(self):
        self.assertEqual(sku_scanner_core.G1_PREFIX, "g1")
        self.assertEqual(
            sku_scanner_core.DEFAULT_TIERS,
            ("2tb.ai", "play.2tb.ai", "5tb.ai", "ai.pro", "20tb", "play.20tb", "ai.ultra"),
        )
        self.assertEqual(
            sku_scanner_core.DEFAULT_PERIODS,
            (
                "1month",
                "2months",
                "3months",
                "4months",
                "6months",
                "12months",
                "15months",
                "18months",
            ),
        )
        self.assertEqual(
            sku_scanner_core.DEFAULT_SUFFIXES,
            ("_eft", "_eft.acq", "_ip_90p", "_ip_90p.acq", "_ip_95p.acq", "_ip_75p.acq"),
        )

        kwargs = {
            "tiers": ("2tb.ai", "ai.pro", "2tb.ai"),
            "periods": ("1month",),
            "suffixes": ("_eft",),
        }
        first = sku_scanner_core.generate_candidates(**kwargs)
        second = sku_scanner_core.generate_candidates(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(
            first,
            [
                "g1.2tb.ai.subs_variant",
                "g1.2tb.ai.1month_eft",
                "g1.ai.pro.subs_variant",
                "g1.ai.pro.1month_eft",
            ],
        )

    def test_generate_candidates_respects_variants_flag_and_limit(self):
        without_variants = sku_scanner_core.generate_candidates(
            tiers=("2tb.ai",),
            periods=("1month", "2months"),
            suffixes=("_eft",),
            include_variants=False,
        )
        self.assertEqual(
            without_variants,
            ["g1.2tb.ai.1month_eft", "g1.2tb.ai.2months_eft"],
        )

        with_variants = sku_scanner_core.generate_candidates(
            tiers=("2tb.ai",),
            periods=("1month", "2months"),
            suffixes=("_eft",),
        )
        self.assertEqual(with_variants[0], "g1.2tb.ai.subs_variant")
        self.assertEqual(
            sku_scanner_core.generate_candidates(
                tiers=("2tb.ai",),
                periods=("1month", "2months"),
                suffixes=("_eft",),
                limit=2,
            ),
            with_variants[:2],
        )

    def test_validate_period_normalizes_valid_values_and_rejects_invalid(self):
        self.assertEqual(sku_scanner_core.validate_period("1month"), "1month")
        self.assertEqual(sku_scanner_core.validate_period("18months"), "18months")
        self.assertEqual(sku_scanner_core.validate_period("2month"), "2months")
        self.assertEqual(sku_scanner_core.validate_period("1months"), "1month")

        for period in ("0months", "month", "18mo", ""):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    sku_scanner_core.validate_period(period)

        with self.assertRaises(ValueError):
            sku_scanner_core.generate_candidates(periods=("1month", "18mo"))

    def test_build_and_parse_sku_round_trip_including_dotted_tiers(self):
        # Prefix variant suffixes with "." (parse_sku returns ".subs_variant").
        cases = (
            ("2tb.ai", "18months", "_eft", "g1.2tb.ai.18months_eft", ("2tb.ai", "18months", "_eft")),
            ("2tb.ai", "", ".subs_variant", "g1.2tb.ai.subs_variant", ("2tb.ai", "", ".subs_variant")),
            ("ai.pro", "12months", "_ip_95p.acq", "g1.ai.pro.12months_ip_95p.acq", ("ai.pro", "12months", "_ip_95p.acq")),
        )
        for tier, period, suffix, sku, parsed in cases:
            with self.subTest(sku=sku):
                self.assertEqual(sku_scanner_core.build_sku(tier, period, suffix), sku)
                self.assertEqual(sku_scanner_core.parse_sku(sku), parsed)

        for sku in ("g2.2tb.ai.18months_eft", "not-a-sku", ""):
            with self.subTest(unparseable=sku):
                self.assertIsNone(sku_scanner_core.parse_sku(sku))

        for bad_tier in ("", "2TB.AI", "2tb ai", ".2tb.ai", "2tb.ai."):
            with self.subTest(bad_tier=bad_tier):
                with self.assertRaises(ValueError):
                    sku_scanner_core.build_sku(bad_tier, "1month", "_eft")

        for bad_suffix in ("eft", "addon", "-eft"):
            with self.subTest(bad_suffix=bad_suffix):
                with self.assertRaises(ValueError):
                    sku_scanner_core.build_sku("2tb.ai", "1month", bad_suffix)


if __name__ == "__main__":
    unittest.main()
