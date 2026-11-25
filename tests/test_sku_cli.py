"""Tests for sku_scanner CLI subcommands (generate / summarize / query).

Injects source_factory so neither frida nor a device is required.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner
import sku_scanner_core


class FakeSource:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []
        self.closed = False

    def fetch(self, skus, timeout):
        self.calls.append((list(skus), timeout))
        return list(self.events)

    def close(self):
        self.closed = True


class CliSubcommandTests(unittest.TestCase):
    def setUp(self):
        """Point default reports to a temp directory to keep the repo clean."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = mock.patch.object(
            sku_scanner, "DEFAULT_REPORT_DIR", Path(temporary.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_generate_and_summarize_subcommands_print_without_frida(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = sku_scanner.main(
                [
                    "generate",
                    "--tiers", "2tb.ai",
                    "--periods", "1month",
                    "--suffixes", "_eft",
                    "--format", "json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("g1.2tb.ai.1month_eft", json.loads(output.getvalue()))
        self.assertNotIn("frida", sys.modules)

        report = sku_scanner_core.to_json(
            [
                sku_scanner_core.OfferRow(
                    sku="g1.2tb.ai.1month_eft",
                    offer_id="offer-1",
                    period="P1M",
                    micros=990000,
                    title="Test Plan",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "report.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(report)

            summarized = io.StringIO()
            with redirect_stdout(summarized):
                self.assertEqual(
                    sku_scanner.main(["summarize", "--input", path, "--format", "json"]),
                    0,
                )
            self.assertIn("g1.2tb.ai.1month_eft", summarized.getvalue())

            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ this is not json")
            errors = io.StringIO()
            with redirect_stderr(errors), redirect_stdout(io.StringIO()):
                invalid_code = sku_scanner.main(
                    ["summarize", "--input", path, "--format", "json"]
                )
            self.assertNotEqual(invalid_code, 0)
            self.assertNotIn("Traceback", errors.getvalue())
            self.assertTrue(errors.getvalue().strip())

        self.assertNotIn("frida", sys.modules)

    def test_query_with_fake_source_is_token_free_and_rejects_conflicting_flags(self):
        source = FakeSource(
            [
                {
                    "ev": "offer",
                    "product": "g1.2tb.ai.1month_eft",
                    "id": "offer-1",
                    "period": "P1M",
                    "micros": "990000",
                    "title": "Plan",
                    "purchaseToken": "dummy-token-value",
                },
                {"ev": "log", "message": "ignore me"},
            ]
        )
        factory_calls = []

        def source_factory():
            factory_calls.append(1)
            return source

        output = io.StringIO()
        with redirect_stdout(output):
            code = sku_scanner.main(
                [
                    "query",
                    "--skus", "g1.2tb.ai.1month_eft",
                    "--timeout", "5",
                    "--format", "json",
                ],
                source_factory=source_factory,
            )
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("g1.2tb.ai.1month_eft", text)
        self.assertNotIn("token", text.lower())
        self.assertNotIn("dummy-token-value", text)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(source.calls), 1)
        self.assertTrue(source.closed)
        self.assertNotIn("frida", sys.modules)

        errors = io.StringIO()
        with redirect_stderr(errors), redirect_stdout(io.StringIO()):
            try:
                conflict_code = sku_scanner.main(
                    ["query", "--skus", "g1.2tb.ai.1month_eft", "--candidates"],
                    source_factory=source_factory,
                )
            except SystemExit as exc:
                conflict_code = exc.code
        self.assertNotEqual(conflict_code, 0)


if __name__ == "__main__":
    unittest.main()
