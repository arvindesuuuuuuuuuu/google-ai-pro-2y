"""Tests verifying SKU catalog entries imported from the iOS g1-acq project.

Confirms Ultra tiers (20tb / play.20tb / ai.ultra) and acquisition suffixes
(_ip_75p.acq) participate in both candidate generation and SKU parsing.
Uses no device, frida, or network.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner_core


class CatalogConstantTests(unittest.TestCase):
    """Default catalog values and ordering (coupled to generation order and docs)."""

    def test_default_tiers_keep_existing_four_then_append_ultra_family(self):
        self.assertEqual(
            sku_scanner_core.DEFAULT_TIERS,
            (
                "2tb.ai",
                "play.2tb.ai",
                "5tb.ai",
                "ai.pro",
                "20tb",
                "play.20tb",
                "ai.ultra",
            ),
        )

    def test_default_suffixes_append_acquisition_75_percent(self):
        self.assertEqual(
            sku_scanner_core.DEFAULT_SUFFIXES,
            ("_eft", "_eft.acq", "_ip_90p", "_ip_90p.acq", "_ip_95p.acq", "_ip_75p.acq"),
        )

    def test_default_periods_are_unchanged(self):
        # Periods must remain unchanged across catalog updates.
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


class CandidateCountTests(unittest.TestCase):
    """Verify 7 tiers x (1 variant + 8 periods x 6 suffixes) = 343 candidates."""

    def test_candidate_count_is_343(self):
        self.assertEqual(len(sku_scanner_core.generate_candidates()), 343)

    def test_candidate_count_without_variants_is_336(self):
        self.assertEqual(
            len(sku_scanner_core.generate_candidates(include_variants=False)),
            336,
        )

    def test_candidates_contain_ultra_family_samples(self):
        candidates = sku_scanner_core.generate_candidates()
        self.assertIn("g1.20tb.3months_ip_90p.acq", candidates)
        self.assertIn("g1.ai.ultra.3months_ip_75p.acq", candidates)
        self.assertIn("g1.play.20tb.12months_eft", candidates)
        self.assertEqual(len(candidates), len(set(candidates)))


class ParseSkuExpansionTests(unittest.TestCase):
    """Dotless Ultra tiers must parse via the period-token path."""

    def test_parse_sku_handles_period_suffix_for_new_tiers(self):
        cases = (
            ("g1.20tb.3months_ip_90p.acq", ("20tb", "3months", "_ip_90p.acq")),
            ("g1.ai.ultra.3months_ip_75p.acq", ("ai.ultra", "3months", "_ip_75p.acq")),
            ("g1.play.20tb.12months_eft", ("play.20tb", "12months", "_eft")),
            ("g1.20tb.subs_variant", ("20tb", "", ".subs_variant")),
            ("g1.ai.ultra.subs_variant", ("ai.ultra", "", ".subs_variant")),
        )
        for sku, parsed in cases:
            with self.subTest(sku=sku):
                self.assertEqual(sku_scanner_core.parse_sku(sku), parsed)

    def test_new_tiers_round_trip_through_build_sku(self):
        for tier in ("20tb", "play.20tb", "ai.ultra"):
            for suffix in ("_eft", "_ip_75p.acq"):
                with self.subTest(tier=tier, suffix=suffix):
                    sku = sku_scanner_core.build_sku(tier, "3months", suffix)
                    self.assertEqual(
                        sku_scanner_core.parse_sku(sku),
                        (tier, "3months", suffix),
                    )


if __name__ == "__main__":
    unittest.main()
