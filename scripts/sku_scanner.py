#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sku_scanner.py -- Read-only CLI to inspect Google One SKUs and Play offers.

Never initiates purchases, checkout flows, or app launches. Lazily imports frida
only during live queries so `generate` and `summarize` run with zero device deps.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import sku_scanner_core as core

DEFAULT_REPORT_DIR = Path("ultra0-report")
REPORT_PREFIX = "sku-scan-"

MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 30.0
DEFAULT_BATCH_SIZE = 20

# build_js(plan="5x", list_only=True) rewrites PRODUCTS in injected.js to this literal.
PRODUCTS_LITERAL = 'var PRODUCTS=["g1.play.20tb"];'
# Read-only flag injected exactly once by build_js(list_only=True).
LIST_ONLY_LITERAL = "var LIST_ONLY=true;"


@dataclass
class ScanOutcome:
    """Aggregated offers plus per-batch success/timeout counts for exit status."""

    rows: list
    batches_ok: int
    batches_failed: int
    timed_out: bool


def _exit_code_for(outcome: ScanOutcome) -> int:
    """Return 0 if at least one batch completed; 1 if every batch failed."""
    return 0 if outcome.batches_ok > 0 else 1


def _split_values(values: Sequence[str]) -> list:
    """Accept both `--periods 18months,12months` and `--periods 18months 12months`."""
    result = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _add_generation_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tiers", nargs="+", default=list(core.DEFAULT_TIERS), metavar="TIER",
        help="Target tiers (space- or comma-separated)",
    )
    parser.add_argument(
        "--periods", nargs="+", default=list(core.DEFAULT_PERIODS), metavar="PERIOD",
        help="Target periods (e.g. 18months,12months)",
    )
    parser.add_argument(
        "--suffixes", nargs="+", default=list(core.DEFAULT_SUFFIXES), metavar="SUFFIX",
        help="Target offer suffixes",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum candidate SKUs to generate")
    parser.add_argument(
        "--no-variants", action="store_true",
        help="Exclude subs_variant SKUs from candidate generation",
    )


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--output", metavar="PATH",
        help="Save report to PATH (defaults to ultra0-report/sku-scan-*.json)",
    )


def _timeout_seconds(value: str) -> float:
    """Clamp --timeout to 1..30s to prevent hangs or missed callbacks."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--timeout must be a number of seconds: {value!r}"
        ) from None
    if not MIN_TIMEOUT <= seconds <= MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(
            f"--timeout must be between {MIN_TIMEOUT:g} and {MAX_TIMEOUT:g} seconds: {value}"
        )
    return seconds


def _batch_size(value: str) -> int:
    """Require --batch-size >= 1."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--batch-size must be an integer: {value!r}"
        ) from None
    if size < 1:
        raise argparse.ArgumentTypeError(
            f"--batch-size must be at least 1: {value}"
        )
    return size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sku_scanner.py",
        description="Read-only scanner for Google One SKUs and Play offers (never initiates checkout)",
    )
    commands = parser.add_subparsers(dest="command")

    generate = commands.add_parser("generate", help="Generate candidate SKUs to query")
    _add_generation_flags(generate)
    _add_output_flags(generate)

    query = commands.add_parser("query", help="Query offers from a connected device (read-only)")
    target = query.add_mutually_exclusive_group()
    target.add_argument("--skus", nargs="+", metavar="SKU", help="Explicit SKUs to query")
    target.add_argument("--candidates", action="store_true", help="Query all generated candidates")
    _add_generation_flags(query)
    query.add_argument(
        "--batch-size", type=_batch_size, default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"SKUs queried per Frida injection (>=1, default: {DEFAULT_BATCH_SIZE})",
    )
    query.add_argument(
        "--timeout", type=_timeout_seconds, default=8.0, metavar="SECONDS",
        help=f"Per-batch wait timeout ({MIN_TIMEOUT:g}-{MAX_TIMEOUT:g}s, default: 8)",
    )
    _add_output_flags(query)

    summarize = commands.add_parser("summarize", help="Format a saved JSON report")
    summarize.add_argument("--input", required=True, metavar="PATH", help="Input JSON report path")
    _add_output_flags(summarize)
    return parser


def _secure_write(path: str, text: str) -> None:
    """Atomically write report with 0600 file permissions."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".sku-scan-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _default_report_path() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(DEFAULT_REPORT_DIR / f"{REPORT_PREFIX}{stamp}.json")


def _candidate_kwargs(args: argparse.Namespace) -> dict:
    return {
        "tiers": _split_values(args.tiers),
        "periods": _split_values(args.periods),
        "suffixes": _split_values(args.suffixes),
        "include_variants": not args.no_variants,
        "limit": args.limit,
    }


def _format_candidate_table(skus: Sequence[str]) -> str:
    lines = ["Candidate SKUs:", "-" * 40]
    if not skus:
        lines.append("No matching candidate SKUs.")
    lines.extend(f"{index + 1:>3}. {sku}" for index, sku in enumerate(skus))
    return "\n".join(lines)


def _emit(text: str, args: argparse.Namespace, report_text: Optional[str] = None) -> Optional[str]:
    """Print text to stdout, save report when requested, and return saved path."""
    saved: Optional[str] = None
    if report_text is not None:
        saved = str(args.output or _default_report_path())
        _secure_write(saved, report_text)
    elif args.output:
        saved = str(args.output)
        _secure_write(saved, text)
    print(text)
    return saved


def _read_note() -> str:
    return (
        "Note: Read-only scan results are informational only; "
        "they do not prove eligibility or actual charge amounts."
    )


class FridaSource:
    """Inject the read-only Frida agent into Google One to collect offers.

    Never invokes launch_app() or checkout flows; only attaches to the running process.
    """

    # Terminal events per batch. `no_offers` fires per SKU before `continue` in
    # agent/injected.js:69, so treating it as terminal drops remaining SKUs.
    TERMINAL_EVENTS = frozenset({"list_only", "no_trial", "query_err", "disc"})
    # Events that must never appear in read-only mode (checkout flow evidence).
    FORBIDDEN_EVENTS = frozenset(
        {"launched", "stack_start", "activity", "purchase_cb"}
    )

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.batch_size = max(1, int(batch_size))
        self._timeout = 8.0
        self._device = None
        self._pid: Optional[int] = None
        self.timed_out = False
        self.batches_ok = 0
        self.batches_failed = 0

    @staticmethod
    def _find_pid(device) -> int:
        for process in device.enumerate_processes():
            name = getattr(process, "name", "") or ""
            if "subscriptions.red" in name or name == "Google One":
                return int(process.pid)
        raise RuntimeError(
            "Google One (com.google.android.apps.subscriptions.red) process not found"
        )

    @classmethod
    def _is_terminal(cls, event: object) -> bool:
        """Return True when an event marks batch completion (never raises on non-strings)."""
        if not isinstance(event, str):
            return False
        if event in cls.TERMINAL_EVENTS:
            return True
        return event.endswith("_err") or event == "disc"

    @classmethod
    def _is_forbidden(cls, event: object) -> bool:
        """Return True if an event indicates billing flow execution."""
        return isinstance(event, str) and event in cls.FORBIDDEN_EVENTS

    @staticmethod
    def _pd_terminates(code: object, count: object) -> bool:
        """Return True when onProductDetailsResponse ends without emitting offers.

        agent/injected.js returns early without `no_trial` when `code != 0` or the
        returned list is empty; treating that as terminal avoids false timeouts on
        unknown SKUs.
        """

        def _as_int(value: object) -> Optional[int]:
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        error_code = _as_int(code)
        size = _as_int(count)
        if size is not None and size <= 0:
            return True
        return error_code is not None and error_code != 0

    @staticmethod
    def _assert_read_only(js: str) -> None:
        """Fail closed unless the assembled JS strictly enforces read-only mode."""
        if not isinstance(js, str) or not js:
            raise RuntimeError(
                "Agent JS is empty (aborting because read-only mode cannot be verified)"
            )
        if js.count(LIST_ONLY_LITERAL) != 1:
            raise RuntimeError(
                f"Expected exactly one read-only flag `{LIST_ONLY_LITERAL}` in agent JS "
                "(aborting to prevent reaching checkout flow)"
            )
        if js.count(PRODUCTS_LITERAL) != 1:
            raise RuntimeError(
                "Expected exactly one PRODUCTS literal in agent JS "
                "(aborting because SKUs cannot be safely substituted)"
            )

    @staticmethod
    def _load_js() -> str:
        """Load ultra0-17.py via importlib without writing .pyc bytecode and build LIST_ONLY JS."""
        path = Path(__file__).resolve().parent / "ultra0-17.py"
        spec = importlib.util.spec_from_file_location("ultra0_17", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load agent builder module: {path}")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        if not hasattr(module, "build_js"):
            raise RuntimeError(f"build_js() not found in {path}")
        js = module.build_js(plan="5x", list_only=True)
        FridaSource._assert_read_only(js)
        return js

    @staticmethod
    def _inject_products(js: str, batch: Sequence[str]) -> str:
        if PRODUCTS_LITERAL not in js:
            raise RuntimeError(
                "PRODUCTS literal not found in agent JS (check build_js output)"
            )
        replacement = "var PRODUCTS=" + json.dumps(list(batch), ensure_ascii=False) + ";"
        injected = js.replace(PRODUCTS_LITERAL, replacement, 1)
        if injected.count(LIST_ONLY_LITERAL) != 1:
            raise RuntimeError(
                "Read-only flag lost after SKU substitution "
                "(aborting to prevent reaching checkout flow)"
            )
        return injected

    def _run_batch(self, js: str) -> tuple:
        """Execute one batch and return (collected_events, timed_out, forbidden_event, script_error)."""
        session = self._device.attach(self._pid)
        done = threading.Event()
        collected: list = []
        state: dict = {"forbidden": None, "script_error": False}
        script = None

        def on_message(message, _data) -> None:
            payload = message.get("payload") if isinstance(message, dict) else None
            if isinstance(payload, dict):
                event = payload.get("ev")
                if self._is_forbidden(event):
                    state["forbidden"] = event
                    done.set()
                    return
                if event == "offer":
                    collected.append(payload)
                elif event == "pd":
                    if self._pd_terminates(payload.get("code"), payload.get("n")):
                        done.set()
                elif self._is_terminal(event):
                    done.set()
            elif isinstance(message, dict) and message.get("type") == "error":
                state["script_error"] = True
                done.set()

        try:
            script = session.create_script(js)
            script.on("message", on_message)
            script.load()
            completed = done.wait(timeout=self._timeout)
        finally:
            if script is not None:
                try:
                    script.unload()
                except Exception:
                    pass
            try:
                session.detach()
            except Exception:
                pass
        return collected, not completed, state["forbidden"], bool(state["script_error"])

    def _connect(self) -> None:
        """Import frida and attach to Google One; wrap all frida exceptions in RuntimeError."""
        try:
            import frida  # Imported only when attaching to a device

            self._device = frida.get_usb_device(timeout=self._timeout)
            self._pid = self._find_pid(self._device)
        except Exception as exc:  # noqa: BLE001
            self._device = None
            raise RuntimeError(
                "Could not connect to device "
                f"(verify USB debugging, frida-server, and Google One running): {exc}"
            ) from exc

    def fetch(self, skus: Sequence[str], timeout: float) -> list:
        self._timeout = float(timeout)
        self.timed_out = False
        self.batches_ok = 0
        self.batches_failed = 0
        events: list = []
        try:
            self._connect()
            template = self._load_js()
            for start in range(0, len(skus), self.batch_size):
                batch = list(skus[start : start + self.batch_size])
                try:
                    injected = self._inject_products(template, batch)
                    collected, timed_out, forbidden, script_error = self._run_batch(injected)
                except RuntimeError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.batches_failed += 1
                    raise RuntimeError(
                        f"Batch injection failed (items {start + 1}-{start + len(batch)}): {exc}"
                    ) from exc
                if forbidden is not None:
                    self.batches_failed += 1
                    raise RuntimeError(
                        "Aborted after detecting a non-read-only event "
                        f"(checkout event: {forbidden})"
                    )
                if timed_out:
                    self.timed_out = True
                if timed_out or script_error:
                    self.batches_failed += 1
                else:
                    self.batches_ok += 1
                events.extend(collected)
        finally:
            self.close()
        return events

    def close(self) -> None:
        self._device = None
        self._pid = None


def _run_generate(args: argparse.Namespace) -> int:
    skus = core.generate_candidates(**_candidate_kwargs(args))
    text = (
        json.dumps(skus, ensure_ascii=False, indent=2)
        if args.format == "json"
        else _format_candidate_table(skus)
    )
    _emit(text, args)
    return 0


def _run_summarize(args: argparse.Namespace) -> int:
    try:
        with open(args.input, encoding="utf-8") as handle:
            payload = handle.read()
    except OSError as exc:
        raise ValueError(f"Could not read report: {args.input}: {exc}") from exc
    rows = core.parse_report(payload)
    text = (
        core.to_json(rows)
        if args.format == "json"
        else "\n".join(core.format_table(rows))
    )
    _emit(text, args)
    print(_read_note(), file=sys.stderr)
    return 0


def _run_query(args: argparse.Namespace, source_factory: Optional[Callable]) -> int:
    if not args.skus and not args.candidates:
        print(
            "Error: query requires either --skus or --candidates. "
            "Run `scan generate` to preview candidates without attaching.",
            file=sys.stderr,
        )
        return 2

    if args.skus:
        skus = list(args.skus)
    else:
        skus = core.generate_candidates(**_candidate_kwargs(args))
    if not skus:
        raise ValueError("No SKUs to query (check --skus or candidate filters)")

    source = source_factory() if source_factory is not None else FridaSource(args.batch_size)
    try:
        events = source.fetch(skus, float(args.timeout))
    finally:
        source.close()

    timed_out = bool(getattr(source, "timed_out", False))
    default_ok = 0 if timed_out else 1
    outcome = ScanOutcome(
        rows=core.dedupe_rows(core.parse_offer_events(events)),
        batches_ok=int(getattr(source, "batches_ok", default_ok)),
        batches_failed=int(getattr(source, "batches_failed", 0)),
        timed_out=timed_out,
    )

    report = core.to_json(outcome.rows)
    text = (
        report
        if args.format == "json"
        else "\n".join(core.format_table(outcome.rows))
    )
    saved = _emit(text, args, report_text=report)
    if saved is not None:
        print(f"Saved report: {saved}", file=sys.stderr)
    if outcome.timed_out:
        print(
            "Warning: batch timed out (no terminal event received). "
            "Check the device state, increase --timeout, or run `./scripts/run.sh check`.",
            file=sys.stderr,
        )
    print(_read_note(), file=sys.stderr)
    return _exit_code_for(outcome)


def main(argv: Optional[Sequence[str]] = None, source_factory: Optional[Callable] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            return _run_generate(args)
        if args.command == "summarize":
            return _run_summarize(args)
        if args.command == "query":
            return _run_query(args, source_factory)
        parser.print_help()
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
