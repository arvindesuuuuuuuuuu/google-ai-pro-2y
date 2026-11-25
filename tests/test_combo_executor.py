"""Tests for pure JS assembly inside RealExecutor.

Loads ultra0-17.py via importlib without touching frida or a real device, and
locks down the invariant that payment confirmation / UI tap calls never enter JS.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo
import combo_core

ACTION = combo_core.ComboAction(
    kind=combo_core.KIND_STACK,
    sku="g1.2tb.ai.subs_variant",
    offer_id="6months-eft",
    months=6,
    expected_end_ms=None,
    note="",
)


class BuildJsTests(unittest.TestCase):
    def setUp(self):
        self.executor = combo.RealExecutor()

    def test_build_js_injects_target_sku_and_offer(self):
        js = self.executor.build_js(ACTION)

        self.assertIn('var PRODUCTS=["g1.2tb.ai.subs_variant"];', js)
        self.assertIn('var PREFER="6months-eft";', js)
        self.assertNotIn(combo.PRODUCTS_LITERAL, js)
        self.assertNotIn(combo.PREFER_LITERAL, js)
        self.assertNotIn("frida", sys.modules)

    def test_build_js_never_enables_list_only(self):
        """Enabling LIST_ONLY prevents checkout from opening, so `run` must reject it."""
        js = self.executor.build_js(ACTION)

        self.assertNotIn(combo.LIST_ONLY_LITERAL, js)

    def test_build_js_keeps_the_billing_flow_but_no_payment_or_ui_taps(self):
        js = self.executor.build_js(ACTION)

        self.assertEqual(js.count("launchBillingFlow"), 1)
        for forbidden in ("confirm_zero", "add_card", "launch_app", "input tap", "input keyevent"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, js)

    def test_build_js_fails_closed_when_list_only_is_present(self):
        def fake_loader():
            def build_js(**_kwargs):
                return combo.PRODUCTS_LITERAL + "\n" + combo.PREFER_LITERAL + "\nvar LIST_ONLY=true;"

            return build_js

        with mock.patch.object(combo.RealExecutor, "_load_build_js", staticmethod(fake_loader)):
            with self.assertRaises(RuntimeError):
                self.executor.build_js(ACTION)

    def test_build_js_fails_closed_when_a_literal_is_missing(self):
        def fake_loader():
            def build_js(**_kwargs):
                return combo.PRODUCTS_LITERAL + "\n"

            return build_js

        with mock.patch.object(combo.RealExecutor, "_load_build_js", staticmethod(fake_loader)):
            with self.assertRaises(RuntimeError):
                self.executor.build_js(ACTION)

    def test_build_js_quotes_values_so_they_cannot_break_out(self):
        action = combo_core.ComboAction(
            kind=combo_core.KIND_STACK,
            sku="g1.2tb.ai.subs_variant",
            offer_id='bad"; S({ev:"pwn"}); //',
            months=6,
            expected_end_ms=None,
            note="",
        )

        js = self.executor.build_js(action)

        expected = "var PREFER=" + json.dumps(action.offer_id, ensure_ascii=False) + ";"
        self.assertIn(expected, js)
        self.assertEqual(js.count("var PREFER="), 1)
        self.assertNotIn('var PREFER="bad";', js)


if __name__ == "__main__":
    unittest.main()
