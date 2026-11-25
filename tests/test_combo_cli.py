"""Tests for combo.py CLI.

Injects state_reader / offer_reader / executor without touching devices or frida.
"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo
import combo_core

DOC_PREFIX = "subs:com.google.android.apps.subscriptions.red:"
DAY_MS = 86_400_000


def ms(year, month, day, hour=12):
    return int(datetime(year, month, day, hour).timestamp() * 1000)


NOW = ms(2026, 11, 24)
END = ms(2028, 11, 21)

OFFERS = [
    {"sku": "g1.2tb.ai.6months_eft", "offer_id": "eft-6months", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.12months_eft", "offer_id": "eft-12months", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.18months_eft", "offer_id": "eft-18months", "period": "P1Y6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M", "micros": 2900000000},
]


class FakeDevice:
    """In-memory read-only state reader modeling ownership rows in library.db."""

    def __init__(self, rows, now_ms=NOW):
        self.rows = list(rows)
        self.now_ms = now_ms
        self.calls = 0

    def read_state(self):
        self.calls += 1
        return combo_core.parse_ownership_rows(self.rows, now_ms=self.now_ms)

    def read_offers(self):
        return [dict(offer) for offer in OFFERS]

    def bump_month(self, months):
        """Advance expiration by `months` months assuming a completed step."""
        current = combo_core.latest_end_ms(self.read_state()) or self.now_ms
        self.rows = [
            (DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com",
             current + months * 30 * DAY_MS)
        ]


def _run(argv, device, executor=None, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = combo.main(
            argv,
            state_reader=device.read_state,
            offer_reader=device.read_offers,
            executor=executor,
            **kwargs,
        )
    return code, out.getvalue(), err.getvalue()


class NoDeviceTests(unittest.TestCase):
    def test_plan_without_readers_refuses_and_touches_no_device(self):
        self.assertNotIn("frida", sys.modules)
        with mock.patch("subprocess.run") as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = combo.main(["plan"])
        self.assertNotEqual(code, 0)
        self.assertIn("--device", out.getvalue() + err.getvalue())
        run.assert_not_called()
        self.assertNotIn("frida", sys.modules)

    def test_no_subcommand_defaults_to_plan(self):
        with mock.patch("subprocess.run") as run:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = combo.main([])
        self.assertNotEqual(code, 0)
        run.assert_not_called()

    def test_run_without_flags_prints_usage_hint_and_never_executes(self):
        device = FakeDevice([])
        executor = mock.Mock()
        code, out, _ = _run(["run"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("To execute, pass both --execute --yes", out)
        executor.assert_not_called()

    def test_run_needs_both_flags(self):
        for argv in (["run", "--execute"], ["run", "--yes"]):
            with self.subTest(argv=argv):
                device = FakeDevice([])
                executor = mock.Mock()
                code, out, _ = _run(argv, device, executor)
                self.assertEqual(code, 2)
                self.assertIn("--execute --yes", out)
                executor.assert_not_called()


class PlanTests(unittest.TestCase):
    def test_plan_prints_verdict_and_actions_and_exits_zero(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, _ = _run(["plan"], device)

        self.assertEqual(code, 0)
        self.assertIn("Active subscription found; stacking is eligible", out)
        self.assertIn("2028-11-21", out)
        self.assertIn("[Stack] g1.2tb.ai.subs_variant / 12months-eft", out)
        self.assertIn("micros=0", out)
        self.assertEqual(device.calls, 1)
        self.assertNotIn("frida", sys.modules)

    def test_plan_reports_auto_renew_off(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, _ = _run(["plan", "--no-auto-renew"], device)

        self.assertEqual(code, 0)
        self.assertIn("Auto-renew is off", out)

    def test_plan_for_fresh_account_starts_with_longest_offer(self):
        code, out, _ = _run(["plan"], FakeDevice([]))

        self.assertEqual(code, 0)
        self.assertIn("new subscription", out)
        self.assertIn("1. [New] g1.2tb.ai.18months_eft / eft-18months / +18 months", out)

    def test_plan_warns_when_account_differs(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, err = _run(["plan", "--account", "other@example.com"], device)

        self.assertEqual(code, 0)
        self.assertIn("other@example.com", err)
        self.assertIn("does not match", err)

    def test_plan_silent_when_account_matches(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, _, err = _run(["plan", "--account", "user@example.com"], device)

        self.assertEqual(code, 0)
        self.assertNotIn("does not match", err)


class RunTests(unittest.TestCase):
    def test_run_executes_every_step_when_the_end_date_grows(self):
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            device.bump_month(action.months)
            return {"launched": True, "event": "launched"}

        with mock.patch("builtins.input") as fake_input:
            code, out, _ = _run(
                ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
                device,
                executor,
            )

        self.assertEqual(code, 0)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0].kind, "new_subscription")
        self.assertTrue(all(action.kind == "stack" for action in actions[1:]))
        self.assertIn("complete payment manually", out)
        self.assertIn("2028", out)
        fake_input.assert_not_called()

    def test_run_stops_after_the_first_step_when_the_end_date_does_not_grow(self):
        """Strict mode (--stop-on-failure) aborts as soon as a step fails to extend."""
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            return {"launched": True}

        code, out, _ = _run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            device,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(len(actions), 1, "Must stop instead of advancing when expiration did not extend")
        self.assertIn("Expiration did not extend", out)
        self.assertIn("Stopping", out)

    def test_run_stops_when_the_billing_view_cannot_be_opened(self):
        """Strict mode aborts as soon as Play checkout fails to open."""
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            return {"launched": False, "event": "no_trial"}

        code, out, _ = _run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            device,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(len(actions), 1)
        self.assertIn("Could not open Play checkout screen", out)

    def test_run_waits_for_enter_in_interactive_mode(self):
        device = FakeDevice([])

        def executor(action):
            device.bump_month(action.months)
            return {"launched": True}

        with mock.patch("builtins.input", return_value="") as fake_input:
            code, out, _ = _run(["run", "--execute", "--yes"], device, executor)

        self.assertEqual(code, 0)
        self.assertEqual(fake_input.call_count, 3)
        self.assertIn("Enter", out)

    def test_run_reports_a_closed_stdin_with_a_helpful_message(self):
        device = FakeDevice([])

        def executor(action):
            device.bump_month(action.months)
            return {"launched": True}

        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = _run(["run", "--execute", "--yes"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("--non-interactive", err)
        self.assertNotIn("Traceback", err)

    def test_run_refuses_an_active_plan_without_stack_offers(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )
        device.read_offers = lambda: [
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M",
             "micros": 2900000000}
        ]
        executor = mock.Mock()

        code, out, _ = _run(["run", "--execute", "--yes", "--non-interactive"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("No stackable free offers found", out)
        executor.assert_not_called()

    def test_run_reports_executor_exceptions_without_traceback(self):
        device = FakeDevice([])

        def executor(action):
            raise RuntimeError("Connection failed")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = combo.main(
                ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
                state_reader=device.read_state,
                offer_reader=device.read_offers,
                executor=executor,
            )

        self.assertNotEqual(code, 0)
        self.assertIn("Connection failed", err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
