"""Tests for pure subscription state classification in combo_core.

Uses no device, frida, or adb; injects time via now_ms.
"""

import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo_core

DOC_PREFIX = "subs:com.google.android.apps.subscriptions.red:"


def ms(year, month, day, hour=12):
    """Return local epoch milliseconds (formatting also uses local time)."""
    return int(datetime(year, month, day, hour).timestamp() * 1000)


NOW = ms(2026, 11, 24)
FUTURE = ms(2028, 11, 21)
PAST = ms(2026, 1, 1)


class ParseOwnershipRowsTests(unittest.TestCase):
    def test_keeps_only_subscription_rows_and_parses_sku_after_last_colon(self):
        rows = [
            (DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", FUTURE),
            ("inapp:com.google.android.apps.subscriptions.red:coins", "user@example.com", FUTURE),
            ("subs:com.google.android.apps.subscriptions.red:g1.play.2tb.ai.student",
             "user@example.com", PAST),
            ("something_else", "user@example.com", FUTURE),
        ]

        states = combo_core.parse_ownership_rows(rows, now_ms=NOW)

        self.assertEqual(
            [state.sku for state in states],
            ["g1.2tb.ai.subs_variant", "g1.play.2tb.ai.student"],
        )
        self.assertTrue(all(state.account == "user@example.com" for state in states))
        self.assertEqual([state.active for state in states], [True, False])
        self.assertEqual([state.valid_until_ms for state in states], [FUTURE, PAST])

    def test_sku_keeps_everything_after_the_last_colon(self):
        rows = [
            ("subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.18months_eft",
             "a@example.com", FUTURE),
        ]

        states = combo_core.parse_ownership_rows(rows, now_ms=NOW)

        self.assertEqual(states[0].sku, "g1.2tb.ai.18months_eft")

    def test_missing_or_bogus_expiry_is_treated_as_unknown_and_inactive(self):
        rows = [
            (DOC_PREFIX + "sku.none", "a@example.com", None),
            (DOC_PREFIX + "sku.empty", "a@example.com", ""),
            (DOC_PREFIX + "sku.text", "a@example.com", "abc"),
        ]

        states = combo_core.parse_ownership_rows(rows, now_ms=NOW)

        self.assertEqual(len(states), 3)
        self.assertTrue(all(state.valid_until_ms is None for state in states))
        self.assertFalse(any(state.active for state in states))

    def test_active_flag_uses_injected_now_ms(self):
        rows = [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "a@example.com", FUTURE)]

        self.assertTrue(combo_core.parse_ownership_rows(rows, now_ms=NOW)[0].active)
        self.assertFalse(
            combo_core.parse_ownership_rows(rows, now_ms=FUTURE + 1)[0].active,
            "Rows at or past expiration must be inactive",
        )

    def test_default_now_is_wall_clock(self):
        future = int(time.time() * 1000) + 86_400_000

        states = combo_core.parse_ownership_rows(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "a@example.com", future)]
        )

        self.assertTrue(states[0].active)

    def test_malformed_rows_are_skipped_without_raising(self):
        rows = [
            ("short",),
            (None, "a@example.com", FUTURE),
            ("subs:com.google.android.apps.subscriptions.red:", "a@example.com", FUTURE),
            (12345, "a@example.com", FUTURE),
            (DOC_PREFIX + "g1.2tb.ai.subs_variant", None, FUTURE),
        ]

        states = combo_core.parse_ownership_rows(rows, now_ms=NOW)

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].sku, "g1.2tb.ai.subs_variant")
        self.assertEqual(states[0].account, "")

    def test_empty_input(self):
        self.assertEqual(combo_core.parse_ownership_rows([], now_ms=NOW), [])


class LatestEndTests(unittest.TestCase):
    def test_returns_max_of_active_states(self):
        states = [
            combo_core.SubscriptionState("a", PAST, "a@example.com", False),
            combo_core.SubscriptionState("b", FUTURE, "a@example.com", True),
            combo_core.SubscriptionState("c", FUTURE + 86_400_000, "a@example.com", True),
        ]

        self.assertEqual(combo_core.latest_end_ms(states), FUTURE + 86_400_000)

    def test_ignores_expired_states(self):
        states = [
            combo_core.SubscriptionState("a", PAST, "a@example.com", False),
            combo_core.SubscriptionState("b", None, "a@example.com", False),
        ]

        self.assertIsNone(combo_core.latest_end_ms(states))

    def test_empty_and_unknown_expiry(self):
        self.assertIsNone(combo_core.latest_end_ms([]))
        self.assertIsNone(
            combo_core.latest_end_ms(
                [combo_core.SubscriptionState("a", None, "a@example.com", True)]
            )
        )


class ClassifyStateTests(unittest.TestCase):
    def test_fresh_when_no_subscription_row_exists(self):
        self.assertEqual(combo_core.classify_state([], NOW), "fresh")

    def test_active_when_at_least_one_row_is_active(self):
        states = [
            combo_core.SubscriptionState("a", PAST, "a@example.com", False),
            combo_core.SubscriptionState("b", FUTURE, "a@example.com", True),
        ]

        self.assertEqual(combo_core.classify_state(states, NOW), "active")

    def test_expired_when_rows_exist_but_none_is_active(self):
        states = [
            combo_core.SubscriptionState("a", PAST, "a@example.com", False),
            combo_core.SubscriptionState("b", None, "a@example.com", False),
        ]

        self.assertEqual(combo_core.classify_state(states, NOW), "expired")


if __name__ == "__main__":
    unittest.main()
