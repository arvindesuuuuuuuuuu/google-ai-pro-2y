"""Resilience tests for combo execution.

Accounts with partial progress retain already-redeemed offers as candidates.
Stopping on an already-used offer prevents reaching later unused +6-month
offers, so used or ineligible steps must be skipped by default and the run
succeeds if at least one step extends expiration.
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

NOW_MS = 1_764_000_000_000  # Approx. 2025-11-24 fixed fixture timestamp
DAY_MS = 24 * 60 * 60 * 1000


def _states(end_ms):
    if end_ms is None:
        return []
    return [
        core.SubscriptionState(
            sku="g1.2tb.ai.subs_variant",
            valid_until_ms=end_ms,
            account="a@example.com",
            active=True,
        )
    ]


def _offers():
    return [
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": 0},
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft-2", "period": "P6M", "micros": 0},
    ]


class _SteppingState:
    """State reader that advances expiration each time executor runs."""

    def __init__(self, start_ms, advances):
        self.end = start_ms
        self.advances = list(advances)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > 1 and self.advances:
            step = self.advances.pop(0)
            if step:
                self.end += step
        return _states(self.end)


class ResilientExecutionTests(unittest.TestCase):
    def _run(self, argv, state_reader, executor):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(
            combo, "input", side_effect=EOFError, create=True
        ):
            code = combo.main(
                argv,
                state_reader=state_reader,
                offer_reader=_offers,
                executor=executor,
            )
        return code, out.getvalue(), err.getvalue()

    def test_already_used_offer_is_skipped_and_later_steps_still_run(self):
        """Even if step 1 is already redeemed, subsequent +6m offers still run."""
        state = _SteppingState(NOW_MS, advances=[0, 6 * 30 * DAY_MS, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertEqual(
            executed,
            ["12months-eft", "6months-eft", "6months-eft-2"],
            "All candidate steps must be attempted even when an early step is already redeemed",
        )
        self.assertEqual(code, 0, "Should succeed if at least one step extends expiration")
        self.assertIn("6months-eft", out)

    def test_all_steps_failing_returns_non_zero(self):
        state = _SteppingState(NOW_MS, advances=[0, 0, 0])

        def executor(action):
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertIn("1", out)

    def test_stop_on_failure_keeps_strict_behaviour_when_requested(self):
        state = _SteppingState(NOW_MS, advances=[0, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            return {"launched": True}

        code, _, _ = self._run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            state,
            executor,
        )

        self.assertEqual(executed, ["12months-eft"], "Strict mode stops on first failure")
        self.assertNotEqual(code, 0)

    def test_cannot_open_billing_screen_also_continues_by_default(self):
        state = _SteppingState(NOW_MS, advances=[6 * 30 * DAY_MS, 6 * 30 * DAY_MS, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            if action.offer_id == "12months-eft":
                return {"launched": False, "event": "query_err"}
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertEqual(
            executed, ["12months-eft", "6months-eft", "6months-eft-2"]
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
