"""Tests for pure planning logic in combo_core.

Uses captured 2026-11-24 fixture data (Pixel 7a / expiration 2028-11-21).
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo_core


def ms(year, month, day, hour=12):
    """Return local epoch milliseconds (formatting also uses local time)."""
    return int(datetime(year, month, day, hour).timestamp() * 1000)


NOW = ms(2026, 11, 24)
END = ms(2028, 11, 21)

OFFERS = [
    {"sku": "g1.2tb.ai.2months_eft", "offer_id": "eft-2months", "period": "P2M", "micros": 0},
    {"sku": "g1.2tb.ai.3months_eft", "offer_id": "eft-3months", "period": "P3M", "micros": 0},
    {"sku": "g1.2tb.ai.4months_eft", "offer_id": "eft-4months", "period": "P4M", "micros": 0},
    {"sku": "g1.2tb.ai.6months_eft", "offer_id": "eft-6months", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.12months_eft", "offer_id": "eft-12months", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.15months_eft", "offer_id": "eft-15months", "period": "P1Y3M", "micros": 0},
    {"sku": "g1.2tb.ai.18months_eft", "offer_id": "eft-18months", "period": "P1Y6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft-2", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft-ss-2026", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M", "micros": 2900000000},
]


class MonthsFromPeriodTests(unittest.TestCase):
    def test_known_periods(self):
        cases = {
            "P1M": 1,
            "P2M": 2,
            "P3M": 3,
            "P4M": 4,
            "P6M": 6,
            "P1Y": 12,
            "P1Y3M": 15,
            "P1Y6M": 18,
            "P2Y": 24,
            "P12M": 12,
        }
        for period, expected in cases.items():
            with self.subTest(period=period):
                self.assertEqual(combo_core.months_from_period(period), expected)

    def test_unknown_periods_are_zero(self):
        for period in ("", None, "bogus", "P", "P1W", "month"):
            with self.subTest(period=period):
                self.assertEqual(combo_core.months_from_period(period), 0)


class StackIsPossibleTests(unittest.TestCase):
    def test_fresh(self):
        possible, message = combo_core.stack_is_possible("fresh", False, False)
        self.assertTrue(possible)
        self.assertIn("new subscription", message)
        self.assertIn("longest", message)

    def test_expired(self):
        possible, message = combo_core.stack_is_possible("expired", False, False)
        self.assertTrue(possible)
        self.assertIn("Expired", message)

    def test_active_with_stack_offers_and_auto_renew(self):
        possible, message = combo_core.stack_is_possible("active", True, True)
        self.assertTrue(possible)
        self.assertEqual(message, "Active subscription found; stacking is eligible")

    def test_active_without_auto_renew(self):
        possible, message = combo_core.stack_is_possible("active", True, False)
        self.assertFalse(possible)
        self.assertEqual(
            message, "Auto-renew is off; cannot stack (do not cancel before stacking)"
        )

    def test_active_without_stack_offers(self):
        possible, message = combo_core.stack_is_possible("active", False, True)
        self.assertFalse(possible)
        self.assertEqual(message, "No stackable free offers found")


class PlanActionsTests(unittest.TestCase):
    def test_fresh_starts_with_the_longest_single_free_offer_then_stacks(self):
        actions = combo_core.plan_actions("fresh", OFFERS, now_ms=NOW, max_steps=4)

        self.assertEqual(len(actions), 4)
        first = actions[0]
        self.assertEqual(first.kind, "new_subscription")
        self.assertEqual(first.sku, "g1.2tb.ai.18months_eft")
        self.assertEqual(first.offer_id, "eft-18months")
        self.assertEqual(first.months, 18)

        self.assertEqual([action.kind for action in actions[1:]], ["stack"] * 3)
        self.assertEqual(
            [action.offer_id for action in actions[1:]],
            ["12months-eft", "6months-eft", "6months-eft-2"],
        )
        self.assertTrue(all(action.sku == "g1.2tb.ai.subs_variant" for action in actions[1:]))

    def test_fresh_expected_end_accumulates_from_now(self):
        actions = combo_core.plan_actions("fresh", OFFERS, now_ms=NOW, max_steps=2)

        self.assertEqual(actions[0].expected_end_ms, ms(2028, 5, 24))
        self.assertEqual(actions[1].expected_end_ms, ms(2029, 5, 24))

    def test_active_emits_only_stack_actions(self):
        actions = combo_core.plan_actions("active", OFFERS, now_ms=NOW, max_steps=4)

        self.assertTrue(actions)
        self.assertFalse(
            any(action.kind == "new_subscription" for action in actions),
            "Must never emit new_subscription when an active plan already exists",
        )
        self.assertTrue(all(action.kind == "stack" for action in actions))
        self.assertEqual(
            [action.offer_id for action in actions],
            ["12months-eft", "6months-eft", "6months-eft-2", "6months-eft-ss-2026"],
        )

    def test_active_expected_end_accumulates_from_latest_end(self):
        actions = combo_core.plan_actions(
            "active", OFFERS, now_ms=NOW, max_steps=2, base_end_ms=END
        )

        self.assertEqual(actions[0].expected_end_ms, ms(2029, 11, 21))
        self.assertEqual(actions[1].expected_end_ms, ms(2030, 5, 21))

    def test_expired_is_planned_as_new_subscription(self):
        actions = combo_core.plan_actions("expired", OFFERS, now_ms=NOW, max_steps=1)

        self.assertEqual(actions[0].kind, "new_subscription")
        self.assertEqual(actions[0].sku, "g1.2tb.ai.18months_eft")

    def test_dedupes_by_offer_id(self):
        duplicated = OFFERS + [
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
        ]

        actions = combo_core.plan_actions("active", duplicated, now_ms=NOW, max_steps=4)

        offer_ids = [action.offer_id for action in actions]
        self.assertEqual(len(offer_ids), len(set(offer_ids)))
        self.assertEqual(offer_ids.count("6months-eft"), 1)

    def test_respects_max_steps(self):
        self.assertEqual(len(combo_core.plan_actions("active", OFFERS, now_ms=NOW, max_steps=2)), 2)
        self.assertEqual(len(combo_core.plan_actions("active", OFFERS, now_ms=NOW, max_steps=1)), 1)
        self.assertEqual(len(combo_core.plan_actions("active", OFFERS, now_ms=NOW, max_steps=0)), 0)

    def test_ignores_non_free_offers(self):
        offers = [dict(offer) for offer in OFFERS]
        for offer in offers:
            if offer["offer_id"] == "12months-eft":
                offer["micros"] = 2900000000
            if offer["offer_id"] == "eft-18months":
                offer["micros"] = 1

        actions = combo_core.plan_actions("active", offers, now_ms=NOW, max_steps=4)

        self.assertNotIn("12months-eft", [action.offer_id for action in actions])
        fresh = combo_core.plan_actions("fresh", offers, now_ms=NOW, max_steps=1)
        self.assertNotEqual(fresh[0].sku, "g1.2tb.ai.18months_eft")

    def test_accepts_micros_as_string_and_rejects_unknown_micros(self):
        offers = [
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": "0"},
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": None},
        ]

        actions = combo_core.plan_actions("active", offers, now_ms=NOW, max_steps=4)

        self.assertEqual([action.offer_id for action in actions], ["12months-eft"])

    def test_excludes_the_base_null_offer_even_when_free(self):
        offers = [
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M", "micros": 0},
        ]

        self.assertEqual(combo_core.plan_actions("active", offers, now_ms=NOW), [])

    def test_no_offers_means_no_actions(self):
        self.assertEqual(combo_core.plan_actions("fresh", [], now_ms=NOW), [])
        self.assertEqual(combo_core.plan_actions("active", OFFERS[11:], now_ms=NOW), [])


class FormatPlanTests(unittest.TestCase):
    def test_active_plan_shows_end_date_steps_and_caveat(self):
        actions = combo_core.plan_actions(
            "active", OFFERS, now_ms=NOW, max_steps=2, base_end_ms=END
        )

        lines = combo_core.format_plan(actions, "active", END)
        text = "\n".join(lines)

        self.assertIn("2028-11-21", text)
        self.assertIn("Active subscription found; stacking is eligible", text)
        self.assertIn("1. [Stack] g1.2tb.ai.subs_variant / 12months-eft / +12 months", text)
        self.assertIn("2. [Stack] g1.2tb.ai.subs_variant / 6months-eft / +6 months", text)
        self.assertIn("micros=0", text)
        self.assertIn("Play", text)

    def test_fresh_plan_is_labelled_new_subscription(self):
        actions = combo_core.plan_actions("fresh", OFFERS, now_ms=NOW, max_steps=1)

        lines = combo_core.format_plan(actions, "fresh", None)
        text = "\n".join(lines)

        self.assertIn("1. [New] g1.2tb.ai.18months_eft / eft-18months / +18 months", text)
        self.assertIn("new subscription", text)
        self.assertIn("not subscribed", text)

    def test_active_without_stack_offers_reports_it_and_has_no_steps(self):
        lines = combo_core.format_plan([], "active", END)
        text = "\n".join(lines)

        self.assertIn("No stackable free offers found", text)
        self.assertNotIn("1. [", text)

    def test_empty_fresh_plan_reports_no_offers(self):
        text = "\n".join(combo_core.format_plan([], "fresh", None))

        self.assertIn("free offers", text)
        self.assertIn("micros=0", text)

    def test_caveat_is_always_present(self):
        for state_class, latest in (("fresh", None), ("active", END), ("expired", None)):
            with self.subTest(state_class=state_class):
                lines = combo_core.format_plan(
                    combo_core.plan_actions(state_class, OFFERS, now_ms=NOW, max_steps=1),
                    state_class,
                    latest,
                )
                self.assertIn("micros=0", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
