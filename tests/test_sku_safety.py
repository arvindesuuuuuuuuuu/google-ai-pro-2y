"""Safety tests for the read-only scanner (no purchase flags or mutated args)."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sku_scanner


class RecordingSource:
    def __init__(self):
        self.calls = []
        self.closed = False

    def fetch(self, skus, timeout):
        self.calls.append({"skus": skus, "timeout": timeout})
        return []

    def close(self):
        self.closed = True


class SafetyTests(unittest.TestCase):
    def setUp(self):
        """Point default reports to a temp directory to keep the repo clean."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = mock.patch.object(
            sku_scanner, "DEFAULT_REPORT_DIR", Path(temporary.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cli_help_has_no_purchase_or_checkout_flags(self):
        helps = []
        for argv in (
            ["--help"],
            ["generate", "--help"],
            ["query", "--help"],
            ["summarize", "--help"],
        ):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                try:
                    sku_scanner.main(argv)
                except SystemExit:
                    pass
            helps.append(output.getvalue().lower())

        text = "\n".join(helps)
        for command in ("generate", "query", "summarize"):
            self.assertIn(command, text)
        for forbidden in ("--purchase", "--checkout", "--buy", "--order"):
            with self.subTest(flag=forbidden):
                self.assertNotIn(forbidden, text)

    def test_source_factory_receives_only_sku_list_and_timeout(self):
        source = RecordingSource()
        factory_calls = []

        def source_factory():
            factory_calls.append(1)
            return source

        with redirect_stdout(io.StringIO()):
            code = sku_scanner.main(
                [
                    "query",
                    "--skus", "g1.2tb.ai.1month_eft", "g1.ai.pro.12months_eft",
                    "--batch-size", "3",
                    "--timeout", "7",
                    "--format", "json",
                ],
                source_factory=source_factory,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(source.calls), 1)
        self.assertTrue(source.closed)

        call = source.calls[0]
        self.assertEqual(set(call), {"skus", "timeout"})
        self.assertIsInstance(call["skus"], list)
        self.assertEqual(
            call["skus"],
            ["g1.2tb.ai.1month_eft", "g1.ai.pro.12months_eft"],
        )
        self.assertTrue(
            all(isinstance(sku, str) and sku.startswith("g1.") for sku in call["skus"])
        )
        self.assertEqual(float(call["timeout"]), 7.0)
        self.assertNotIn("frida", sys.modules)


if __name__ == "__main__":
    unittest.main()
