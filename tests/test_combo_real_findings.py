"""Regression tests for three bugs discovered on real hardware:

1. Missing STACK flag during stack runs (treated as new purchase and not applied)
2. Millisecond/few-hour timestamp jitter misclassified as an extension
3. Mixing ownership rows across multiple Google accounts
"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo
import combo_core as core

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
NOW_MS = 1_764_000_000_000


def _row(account, until_ms, sku="g1.2tb.ai.subs_variant"):
    return (f"subs:com.google.android.apps.subscriptions.red:{sku}", account, until_ms)


class ExtensionThresholdTests(unittest.TestCase):
    def test_small_drift_is_not_treated_as_an_extension(self):
        """A few hours of timestamp drift does not count as an extension."""
        self.assertFalse(core.is_meaningful_extension(NOW_MS, NOW_MS + 2 * HOUR_MS))
        self.assertFalse(core.is_meaningful_extension(NOW_MS, NOW_MS))

    def test_a_real_month_extension_counts(self):
        self.assertTrue(core.is_meaningful_extension(NOW_MS, NOW_MS + 30 * DAY_MS))
        self.assertTrue(core.is_meaningful_extension(NOW_MS, NOW_MS + DAY_MS))


class AccountScopingTests(unittest.TestCase):
    def test_rows_from_different_accounts_are_not_mixed(self):
        rows = [
            _row("old@example.com", NOW_MS + 12 * 30 * DAY_MS),
            _row("new@example.com", NOW_MS + 6 * 30 * DAY_MS),
        ]
        states = core.parse_ownership_rows(rows, now_ms=NOW_MS)
        self.assertEqual(len(states), 2)

        only_new = core.filter_by_account(states, "new@example.com")
        self.assertEqual([s.account for s in only_new], ["new@example.com"])
        self.assertEqual(
            core.latest_end_ms(only_new), NOW_MS + 6 * 30 * DAY_MS
        )

    def test_distinct_accounts_are_reported(self):
        rows = [
            _row("a@example.com", NOW_MS + DAY_MS),
            _row("b@example.com", NOW_MS + 2 * DAY_MS),
        ]
        states = core.parse_ownership_rows(rows, now_ms=NOW_MS)
        self.assertEqual(core.distinct_accounts(states), ["a@example.com", "b@example.com"])


class StackFlagTests(unittest.TestCase):
    def test_stack_flag_survives_offer_substitution(self):
        executor = combo.RealExecutor.__new__(combo.RealExecutor)
        js = executor._assemble(
            core.ComboAction(
                kind="stack",
                sku="g1.2tb.ai.subs_variant",
                offer_id="6months-eft",
                months=6,
                expected_end_ms=NOW_MS,
                note="",
            )
        )
        self.assertIn("var STACK=true;", js)
        self.assertIn('var PREFER="6months-eft";', js)
        self.assertNotIn(combo.PREFER_LITERAL, js)

    def test_new_subscription_action_does_not_enable_stack(self):
        executor = combo.RealExecutor.__new__(combo.RealExecutor)
        js = executor._assemble(
            core.ComboAction(
                kind="new_subscription",
                sku="g1.2tb.ai.18months_eft",
                offer_id="eft-18months",
                months=18,
                expected_end_ms=NOW_MS,
                note="",
            )
        )
        self.assertNotIn("var STACK=true;", js)
        self.assertIn('var PREFER="eft-18months";', js)


class RunGuardsMultipleAccountsTests(unittest.TestCase):
    def _run(self, argv, state_reader):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(
            combo, "input", side_effect=EOFError, create=True
        ):
            code = combo.main(
                argv,
                state_reader=state_reader,
                offer_reader=lambda: [
                    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft",
                     "period": "P6M", "micros": 0},
                ],
                executor=lambda action: {"launched": True},
            )
        return code, out.getvalue(), err.getvalue()

    def test_run_refuses_when_multiple_accounts_and_no_account_flag(self):
        rows = [
            _row("a@example.com", NOW_MS + 30 * DAY_MS),
            _row("b@example.com", NOW_MS + 60 * DAY_MS),
        ]
        code, _, err = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            lambda: rows,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("--account", err)

    def test_run_proceeds_when_account_flag_is_given(self):
        rows = [
            _row("a@example.com", NOW_MS + 30 * DAY_MS),
            _row("b@example.com", NOW_MS + 60 * DAY_MS),
        ]
        code, _, err = self._run(
            [
                "run", "--execute", "--yes", "--account", "a@example.com",
                "--non-interactive", "--wait-seconds", "0",
            ],
            lambda: rows,
        )
        self.assertNotIn("Multiple accounts", err)


if __name__ == "__main__":
    unittest.main()


class ForegroundGuardTests(unittest.TestCase):
    """Billing checkout only opens when Google One is in the foreground (otherwise no_activity)."""

    def test_no_activity_is_a_terminal_failure(self):
        self.assertIn("no_activity", combo.TERMINAL_FAILURE_EVENTS)

    def test_foreground_detection_matches_google_one_only(self):
        is_fg = combo._is_google_one_foreground
        self.assertTrue(is_fg("com.google.android.apps.subscriptions.red/.MainActivity"))
        self.assertTrue(is_fg("  topResumedActivity=ActivityRecord{1 u0 com.google.android.apps.subscriptions.red/.MainActivity}"))
        self.assertFalse(is_fg("com.android.vending/com.google.android.finsky.activities.MainActivity"))
        self.assertFalse(is_fg(""))
        self.assertFalse(is_fg(None))

    def test_failure_message_mentions_opening_google_one(self):
        message = combo._no_activity_hint()
        self.assertIn("Google One", message)
        self.assertTrue("open" in message.lower() or "foreground" in message.lower())


class EnsureForegroundTests(unittest.TestCase):
    def test_raises_actionable_error_when_google_one_not_in_front(self):
        executor = combo.RealExecutor(timeout=1.0, open_app=False)
        with mock.patch.object(combo, "_foreground_dump", return_value="com.android.vending/..."):
            with self.assertRaises(RuntimeError) as caught:
                executor._ensure_foreground()
        self.assertIn("Google One", str(caught.exception))

    def test_passes_when_google_one_is_in_front(self):
        executor = combo.RealExecutor(timeout=1.0)
        with mock.patch.object(
            combo, "_foreground_dump",
            return_value="topResumedActivity=ActivityRecord{com.google.android.apps.subscriptions.red/.MainActivity}",
        ):
            executor._ensure_foreground()

    def test_open_app_brings_google_one_to_front(self):
        executor = combo.RealExecutor(timeout=2.0, open_app=True)
        dumps = iter(["com.android.vending/...", "com.google.android.apps.subscriptions.red/.MainActivity"])
        calls = []

        def fake_dump(timeout=30):
            try:
                return next(dumps)
            except StopIteration:
                return "com.google.android.apps.subscriptions.red/.MainActivity"

        with mock.patch.object(combo, "_foreground_dump", side_effect=fake_dump), \
             mock.patch.object(combo, "_adb", side_effect=lambda *a, **k: calls.append(a) or ""), \
             mock.patch.object(combo.time, "sleep", lambda *_: None):
            executor._ensure_foreground()
        self.assertTrue(any("monkey" in str(call) for call in calls), "Expected foreground launch command")
