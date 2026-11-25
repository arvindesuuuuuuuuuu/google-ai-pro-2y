#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""combo.py -- Plan and execute free Google AI Pro offer stacking by account state.

Key safety gates:
- Dry-run by default. Never touches a connected device unless `--device` is passed.
- Execution requires both `--execute` and `--yes`.
- Stops after opening the Play checkout screen; the operator always confirms
  payment manually (never taps Subscribe, confirms, or calls `confirm_zero()`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import combo_core as core

VENDING_PKG = "com.android.vending"
LIBRARY_DB = f"/data/data/{VENDING_PKG}/databases/library.db"

# Verified free SKUs on 2026-11-24. 1month_eft returned empty so it is omitted.
DEFAULT_OFFER_SKUS = (
    "g1.2tb.ai.2months_eft",
    "g1.2tb.ai.3months_eft",
    "g1.2tb.ai.4months_eft",
    "g1.2tb.ai.6months_eft",
    "g1.2tb.ai.12months_eft",
    "g1.2tb.ai.15months_eft",
    "g1.2tb.ai.18months_eft",
    "g1.2tb.ai.subs_variant",
)

# Literals appearing once each in raw build_js(plan="auto", list_only=False) output.
PRODUCTS_LITERAL = 'var PRODUCTS = ["g1.play.20tb", "g1.play.ai.pro", "g1.30tb"];'
PREFER_LITERAL = 'var PREFER = "";'
LIST_ONLY_LITERAL = "var LIST_ONLY=true;"
STACK_LITERAL = "var STACK=true;"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_DEVICE = 3

DEVICE_HINT = (
    "Pass --device to read subscription state and offers from a connected device "
    "(this command does not touch any device by default)."
)
EXECUTE_HINT = "To execute, pass both --execute --yes"

COMMANDS = ("plan", "run")

TERMINAL_FAILURE_EVENTS = frozenset({"no_trial", "no_offers", "no_activity"})


# --------------------------------------------------------------------------
# Read-only device inspection
# --------------------------------------------------------------------------
GOOGLE_ONE_PACKAGE = "com.google.android.apps.subscriptions.red"


def _is_google_one_foreground(dump: Optional[str]) -> bool:
    """Return True if Google One appears in `dumpsys activity activities`."""
    if not dump:
        return False
    return GOOGLE_ONE_PACKAGE in str(dump)


def _no_activity_hint() -> str:
    return (
        "Google One has no active foreground Activity. "
        "Open Google One on your device so it is in the foreground, then retry "
        "(or pass --open-app to bring it to the foreground automatically)."
    )


def _foreground_dump(timeout: float = 30) -> str:
    """Read foreground activity dump without altering device state."""
    try:
        return _adb("shell", "dumpsys activity activities", timeout=timeout)
    except RuntimeError:
        return ""


def _adb_prefix() -> list:
    adb = os.environ.get("ADB_PATH") or "adb"
    serial = os.environ.get("ADB_SERIAL") or ""
    return [adb] + (["-s", serial] if serial else [])


def _adb(*args: str, timeout: float = 60) -> str:
    """Run one adb command and wrap failures in a single-line RuntimeError."""
    command = _adb_prefix() + list(args)
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "adb not found (install Android platform-tools or set ADB_PATH)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"adb timed out: adb {args[0]}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "ignore").strip()[:200]
        raise RuntimeError(f"adb failed (adb {args[0]}): {detail}")
    return completed.stdout.decode("utf-8", "ignore")


def _query_library_db(
    prefix: str, query: str, params: tuple, timeout: float
) -> list:
    """Copy Play's library.db via direct shell or Magisk su -c and run one query."""
    remote = f"/data/local/tmp/{prefix}_{os.getpid()}.db"
    local = Path(tempfile.gettempdir()) / f"{prefix}_{os.getpid()}.db"
    copy_cmd = (
        f"(cp {LIBRARY_DB} {remote} 2>/dev/null || su -c 'cp {LIBRARY_DB} {remote}') "
        f"&& (chmod 666 {remote} 2>/dev/null || su -c 'chmod 666 {remote}')"
    )
    try:
        _adb("shell", copy_cmd, timeout=timeout)
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        _adb("pull", remote, str(local), timeout=timeout)
        if not local.exists() or local.stat().st_size == 0:
            return []
        connection = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
        try:
            return connection.execute(query, params).fetchall()
        finally:
            connection.close()
    finally:
        try:
            _adb("shell", f"rm -f {remote}", timeout=timeout)
        except Exception:  # noqa: BLE001 - never mask original failure during cleanup
            pass
        try:
            local.unlink()
        except OSError:
            pass


def read_state(timeout: float = 60) -> list:
    """Read ownership rows from Play's library.db (cleans up remote/local copies)."""
    rows = _query_library_db(
        "combo_library",
        "select doc_id, account, subs_valid_until_time from ownership",
        (),
        timeout,
    )
    if not rows:
        # Distinguish genuinely empty table from unreadable DB by verifying pullability.
        remote = f"/data/local/tmp/combo_check_{os.getpid()}.db"
        local = Path(tempfile.gettempdir()) / f"combo_check_{os.getpid()}.db"
        try:
            _adb(
                "shell",
                f"(cp {LIBRARY_DB} {remote} 2>/dev/null || su -c 'cp {LIBRARY_DB} {remote}') "
                f"&& (chmod 666 {remote} 2>/dev/null || su -c 'chmod 666 {remote}')",
                timeout=timeout,
            )
            _adb("pull", remote, str(local), timeout=timeout)
            if not local.exists() or local.stat().st_size == 0:
                raise RuntimeError(
                    "Could not read library.db "
                    "(verify root access and Play Store login on device)"
                )
        finally:
            try:
                _adb("shell", f"rm -f {remote}", timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
            try:
                local.unlink()
            except OSError:
                pass
    return core.parse_ownership_rows(rows)


def read_stack_token(account: str, sku: str = "g1.2tb.ai.subs_variant", timeout: float = 60) -> str:
    """Read purchaseToken for the target account from Play's library.db.

    queryPurchasesAsync callbacks can stall on real devices, so reading
    ownership.inapp_purchase_data directly avoids blocking stacking flows.
    """
    rows = _query_library_db(
        "combo_token",
        "select account, subs_valid_until_time, inapp_purchase_data "
        "from ownership where doc_id like ?",
        (f"%{sku}%",),
        timeout,
    )

    want = (account or "").strip().lower()
    best = None
    for row_account, valid, data in rows:
        if want and str(row_account or "").strip().lower() != want:
            continue
        if not data:
            continue
        try:
            token = (json.loads(data) or {}).get("purchaseToken")
        except ValueError:
            continue
        if not token:
            continue
        try:
            alive = int(valid or 0) > int(time.time() * 1000)
        except (TypeError, ValueError):
            alive = False
        if best is None or (alive and not best[1]):
            best = (token, alive)
    return best[0] if best else ""


def read_offers(
    timeout: float = 20.0,
    batch_size: int = 20,
    skus: Optional[Sequence[str]] = None,
) -> list:
    """Collect offers via the read-only sku_scanner FridaSource."""
    import sku_scanner
    import sku_scanner_core

    source = sku_scanner.FridaSource(batch_size)
    try:
        events = source.fetch(list(skus or DEFAULT_OFFER_SKUS), float(timeout))
    finally:
        source.close()
    if getattr(source, "timed_out", False):
        print(
            "Warning: offer scan timed out (results may be incomplete).",
            file=sys.stderr,
        )
    return [
        {
            "sku": row.sku,
            "offer_id": row.offer_id,
            "period": row.period,
            "micros": row.micros,
        }
        for row in sku_scanner_core.parse_offer_events(events)
    ]


# --------------------------------------------------------------------------
# Checkout screen launcher (never taps or confirms payment)
# --------------------------------------------------------------------------
class RealExecutor:
    """Opens the Play checkout sheet for a target SKU and offerId.

    Loads build_js(plan="auto", list_only=False) from ultra0-17.py, substitutes
    PRODUCTS and PREFER, and aborts if LIST_ONLY remains. Never invokes
    launch_app(), confirm_zero(), or add_card().
    """

    def __init__(
        self,
        timeout: float = 20.0,
        open_app: bool = False,
        account: str = "",
        token_reader: Optional[Callable[[], str]] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.open_app = bool(open_app)
        self.account = account or ""
        self._token_reader = token_reader

    def _stack_token(self) -> str:
        if self._token_reader is not None:
            return self._token_reader()
        if not self.account:
            return ""
        return read_stack_token(self.account, timeout=self.timeout)

    def _ensure_foreground(self) -> None:
        """Require a live foreground Google One Activity so launchBillingFlow succeeds."""
        if _is_google_one_foreground(_foreground_dump(self.timeout)):
            return
        if self.open_app:
            try:
                _adb(
                    "shell",
                    f"monkey -p {GOOGLE_ONE_PACKAGE} "
                    "-c android.intent.category.LAUNCHER 1",
                    timeout=self.timeout,
                )
            except RuntimeError:
                raise RuntimeError(_no_activity_hint()) from None
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                time.sleep(1.0)
                if _is_google_one_foreground(_foreground_dump(self.timeout)):
                    return
        raise RuntimeError(_no_activity_hint())

    @staticmethod
    def _load_build_js():
        path = Path(__file__).resolve().parent / "ultra0-17.py"
        spec = importlib.util.spec_from_file_location("ultra0_17_combo", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load agent builder module: {path}")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        build_js = getattr(module, "build_js", None)
        if build_js is None:
            raise RuntimeError(f"build_js() not found in {path}")
        return build_js

    def build_js(self, action: core.ComboAction, old_token: str = "") -> str:
        """Assemble and verify the injected Frida JS for one ComboAction."""
        return self._assemble(action, old_token=old_token)

    def _assemble(self, action: core.ComboAction, old_token: str = "") -> str:
        """Build agent JS cleanly with no device access (testable offline)."""
        is_stack = getattr(action, "kind", "") == "stack"
        js = self._load_build_js()(
            plan="auto",
            list_only=False,
            stack=is_stack,
            old_token=old_token or "",
        )
        if not isinstance(js, str) or not js:
            raise RuntimeError("Agent JS is empty (aborting)")
        if LIST_ONLY_LITERAL in js:
            raise RuntimeError(
                "Read-only flag LIST_ONLY remained in agent JS "
                "(aborting because checkout cannot open)"
            )
        for literal, label in ((PRODUCTS_LITERAL, "PRODUCTS"), (PREFER_LITERAL, "PREFER")):
            if js.count(literal) != 1:
                raise RuntimeError(
                    f"Expected exactly one {label} literal in agent JS "
                    "(aborting because target substitution is unsafe)"
                )
        if is_stack and STACK_LITERAL not in js:
            raise RuntimeError(
                "Failed to inject STACK flag; "
                "continuing would launch a new purchase instead of stacking (aborting)"
            )

        products = f"var PRODUCTS=[{json.dumps(action.sku, ensure_ascii=False)}];"
        prefer = f"var PREFER={json.dumps(action.offer_id, ensure_ascii=False)};"
        injected = js.replace(PRODUCTS_LITERAL, products, 1)
        injected = injected.replace(PREFER_LITERAL, prefer, 1)

        if PRODUCTS_LITERAL in injected or PREFER_LITERAL in injected:
            raise RuntimeError("Failed to substitute SKU / offerId literals (aborting)")
        if LIST_ONLY_LITERAL in injected:
            raise RuntimeError("Read-only flag appeared after substitution (aborting)")
        if products not in injected or prefer not in injected:
            raise RuntimeError("Failed to verify substituted JS payload (aborting)")
        if is_stack and STACK_LITERAL not in injected:
            raise RuntimeError("STACK flag lost during substitution (aborting)")
        return injected

    @staticmethod
    def _find_pid(device) -> int:
        for process in device.enumerate_processes():
            name = getattr(process, "name", "") or ""
            if "subscriptions.red" in name or name == "Google One":
                return int(process.pid)
        raise RuntimeError(
            "Google One (com.google.android.apps.subscriptions.red) process not found "
            "(open Google One on the device first)"
        )

    def __call__(self, action: core.ComboAction) -> dict:
        old_token = ""
        if getattr(action, "kind", "") == "stack":
            old_token = self._stack_token()
            if not old_token:
                raise RuntimeError(
                    "Could not read purchaseToken from library.db for stacking. "
                    "Verify the active Play account matches --account and auto-renew is enabled."
                )
        js = self.build_js(action, old_token=old_token)
        self._ensure_foreground()
        try:
            import frida
        except ImportError as exc:
            raise RuntimeError(
                "frida is not installed (run `./scripts/setup.sh` first)"
            ) from exc

        try:
            device = frida.get_usb_device(timeout=10)
            pid = self._find_pid(device)
            session = device.attach(pid)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Could not connect to device "
                f"(verify USB debugging and Google One running): {exc}"
            ) from exc

        done = threading.Event()
        events: list = []
        state: dict = {"launched": None, "failure": None}
        script = None

        def on_message(message, _data) -> None:
            payload = message.get("payload") if isinstance(message, dict) else None
            if not isinstance(payload, dict):
                return
            event = payload.get("ev")
            if not isinstance(event, str):
                return
            # Strip sensitive fields such as purchaseToken.
            events.append(
                {
                    "ev": event,
                    "product": payload.get("product"),
                    "id": payload.get("id"),
                    "period": payload.get("period"),
                    "code": payload.get("code"),
                }
            )
            if event == "launched":
                state["launched"] = payload
                done.set()
            elif event in TERMINAL_FAILURE_EVENTS or event.endswith("_err"):
                state["failure"] = event
                done.set()

        try:
            script = session.create_script(js)
            script.on("message", on_message)
            script.load()
            completed = done.wait(timeout=self.timeout)
        finally:
            if script is not None:
                try:
                    script.unload()
                except Exception:  # noqa: BLE001
                    pass
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass

        if state["launched"] is not None:
            return {"launched": True, "event": "launched", "events": events}
        if state["failure"] is not None:
            return {"launched": False, "event": state["failure"], "events": events}
        return {
            "launched": False,
            "event": None,
            "timed_out": not completed,
            "events": events,
        }


# --------------------------------------------------------------------------
# Plan assembly
# --------------------------------------------------------------------------
@dataclass
class PlanBundle:
    state_class: str
    latest_end_ms: Optional[int]
    has_stack_offers: bool
    possible: bool
    verdict: str
    actions: list
    now_ms: int
    account_warning: Optional[str]
    accounts: list = field(default_factory=list)


def _normalize_states(items: Iterable[object]) -> list:
    """Accept either SubscriptionState objects or raw ownership tuples/dicts."""
    states: list = []
    raw_rows: list = []
    for item in items:
        if isinstance(item, core.SubscriptionState):
            states.append(item)
        elif isinstance(item, dict) and "doc_id" in item:
            raw_rows.append(
                (item["doc_id"], item.get("account", ""), item.get("subs_valid_until_time"))
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            raw_rows.append((item[0], item[1], item[2]))
        else:
            raise ValueError(f"Cannot interpret ownership row: {item!r}")
    if raw_rows:
        states.extend(core.parse_ownership_rows(raw_rows))
    return states


def _account_warning(states: Sequence[core.SubscriptionState], account: str) -> Optional[str]:
    """Compare --account against library.db ownership rows without tapping Play UI."""
    want = account.strip().lower()
    if not want:
        return None
    accounts = sorted({state.account.strip().lower() for state in states if state.account.strip()})
    if not accounts:
        return (
            f"Warning: could not verify account {account} in library.db ownership rows "
            "(no subscription rows found)."
        )
    if want not in accounts:
        return (
            f"Warning: account {account} does not match library.db ownership rows "
            f"(found: {', '.join(accounts)}). Check the active account signed into Play."
        )
    return None


def _collect(
    args: argparse.Namespace,
    state_reader: Callable[[], list],
    offer_reader: Callable[[], list],
) -> PlanBundle:
    now_ms = int(time.time() * 1000)
    raw_states = _normalize_states(list(state_reader()))
    states = (
        core.filter_by_account(raw_states, args.account)
        if getattr(args, "account", None)
        else raw_states
    )
    offers = list(offer_reader())
    state_class = core.classify_state(states, now_ms)
    latest = core.latest_end_ms(states)
    stack_available = core.has_stack_offers(offers)
    possible, verdict = core.stack_is_possible(state_class, stack_available, args.auto_renew)
    actions = core.plan_actions(
        state_class,
        offers,
        now_ms=now_ms,
        max_steps=args.max_steps,
        base_end_ms=latest,
    )
    return PlanBundle(
        state_class=state_class,
        latest_end_ms=latest,
        has_stack_offers=stack_available,
        possible=possible,
        verdict=verdict,
        actions=actions,
        now_ms=now_ms,
        account_warning=_account_warning(raw_states, args.account) if args.account else None,
        accounts=core.distinct_accounts(raw_states),
    )


def _print_plan(bundle: PlanBundle, args: argparse.Namespace) -> None:
    account = getattr(args, "account", None)
    if account:
        print(f"Target account: {account}")
    elif len(bundle.accounts) > 1:
        print(
            f"Target account: unspecified (computed from latest expiration across {len(bundle.accounts)} ownership rows)"
        )
    for line in core.format_plan(
        bundle.actions,
        bundle.state_class,
        bundle.latest_end_ms,
        auto_renew=args.auto_renew,
        has_stack_offers=bundle.has_stack_offers,
    ):
        print(line)
    if bundle.account_warning:
        print(bundle.account_warning, file=sys.stderr)


def _resolve_readers(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
) -> Optional[tuple]:
    state = state_reader
    offers = offer_reader
    if args.device:
        state = state or read_state
        offers = offers or read_offers
    if state is None or offers is None:
        return None
    return state, offers


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def _wait_seconds_type(value: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"seconds must be a number: {value!r}") from None
    if seconds < 0:
        raise argparse.ArgumentTypeError(f"seconds must be >= 0: {value}")
    return seconds


def _max_steps_type(value: str) -> int:
    try:
        steps = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"max steps must be an integer: {value!r}") from None
    if steps < 0:
        raise argparse.ArgumentTypeError(f"max steps must be >= 0: {value}")
    return steps


def _print_step(index: int, total: int, action: core.ComboAction) -> None:
    label = "New subscription" if action.kind == core.KIND_NEW else "Stack"
    expected = (
        datetime.fromtimestamp(action.expected_end_ms / 1000).strftime("%Y-%m-%d")
        if action.expected_end_ms
        else "-"
    )
    print()
    print(
        f"[{index}/{total}] {label}: {action.sku} / {action.offer_id}"
        f" (+{action.months} months -> {expected})"
    )
    print("  Inspect the Play checkout screen on your device and complete payment manually")
    print("  - Verify the charge shows 0 (Free)")
    print("  - Do NOT tap Cancel subscription (canceling prevents stacking)")
    if action.kind == core.KIND_NEW:
        print("  - Buying an offer under a different SKU may replace an existing active plan")


def _wait_for_human(args: argparse.Namespace, index: int, total: int) -> None:
    if args.non_interactive:
        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        return
    print(
        f"  Press Enter after completing payment ({index}/{total})... ",
        end="",
        flush=True,
    )
    try:
        input()
    except EOFError as exc:
        raise RuntimeError(
            "Could not wait for Enter (stdin is closed). "
            "Run interactively at the terminal or pass --non-interactive --wait-seconds N"
        ) from exc
    print()


def _execute_actions(
    args: argparse.Namespace,
    bundle: PlanBundle,
    state_reader: Callable[[], list],
    executor: Callable[[core.ComboAction], object],
) -> int:
    """Execute planned steps sequentially, skipping exhausted offers by default."""
    total = len(bundle.actions)
    before = bundle.latest_end_ms if bundle.latest_end_ms is not None else bundle.now_ms
    succeeded: list = []
    skipped: list = []
    strict = bool(getattr(args, "stop_on_failure", False))

    for index, action in enumerate(bundle.actions, start=1):
        _print_step(index, total, action)
        result = executor(action)
        if isinstance(result, dict) and result.get("launched") is False:
            event = result.get("event") or ("timeout" if result.get("timed_out") else "unknown")
            print(
                f"  Could not open Play checkout screen ({event}). "
                "This offer may already be redeemed or ineligible for this account.",
            )
            skipped.append(action.offer_id)
            if strict:
                print("  Stopping because --stop-on-failure is set.")
                return EXIT_FAILED
            print("  Continuing to next step.")
            continue

        _wait_for_human(args, index, total)

        states = _normalize_states(list(state_reader()))
        if getattr(args, "account", None):
            states = core.filter_by_account(states, args.account)
        after = core.latest_end_ms(states)
        if not core.is_meaningful_extension(before, after):
            print(
                "  Expiration did not extend. "
                "This offer may already be redeemed or ineligible.",
            )
            skipped.append(action.offer_id)
            if strict:
                print("  Stopping because --stop-on-failure is set.")
                return EXIT_FAILED
            print("  Continuing to next step.")
            continue
        print(f"  OK: expiration extended {_date(before)} -> {_date(after)}.")
        succeeded.append(action.offer_id)
        before = after

    print()
    if skipped:
        print(f"Skipped/unapplied offers ({len(skipped)}): {', '.join(skipped)}")
        print("  These offers were already redeemed or unavailable on this account.")
    if not succeeded:
        print("No steps extended the subscription expiration.")
        print(
            "Check Payments & subscriptions in Google Play. "
            "If all stack offers are exhausted, further extension is not available."
        )
        return EXIT_FAILED

    print(f"Extended expiration across {len(succeeded)} step(s).")
    print("Verify the final renewal date and price in Google Play -> Payments & subscriptions.")
    return EXIT_OK


def _date(ms_value: Optional[int]) -> str:
    if ms_value is None:
        return "-"
    return time.strftime("%Y-%m-%d", time.localtime(ms_value / 1000))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device", action="store_true",
        help="Read state and offers from connected device (touches no device when omitted)",
    )
    parser.add_argument(
        "--account", metavar="EMAIL",
        help="Expected Google account; warns if library.db ownership rows differ",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0, metavar="SECONDS",
        help="Timeout for offer scan and checkout launch (default: 20)",
    )
    parser.add_argument(
        "--open-app", action="store_true",
        help="Launch Google One to foreground if needed (disabled by default)",
    )
    parser.add_argument(
        "--max-steps", type=_max_steps_type, default=4, metavar="N",
        help="Maximum steps to include in plan (default: 4)",
    )
    parser.add_argument(
        "--auto-renew", action=argparse.BooleanOptionalAction, default=True,
        help="Whether subscription auto-renew is active (default: True)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="combo.py",
        description=(
            "Plan and execute Google AI Pro free-offer stacking by account state "
            "(dry-run by default; payment is always confirmed manually)"
        ),
    )
    commands = parser.add_subparsers(dest="command")

    plan = commands.add_parser("plan", help="Read state and offers and print plan (no execution)")
    _add_common_flags(plan)

    run = commands.add_parser("run", help="Execute combo plan (requires --execute --yes)")
    _add_common_flags(run)
    run.add_argument("--execute", action="store_true", help="Enable execution (requires --yes)")
    run.add_argument("--yes", action="store_true", help="Confirm execution")
    run.add_argument(
        "--non-interactive", action="store_true",
        help="Wait --wait-seconds instead of prompting for Enter (for automation/tests)",
    )
    run.add_argument(
        "--wait-seconds", type=_wait_seconds_type, default=30.0, metavar="SECONDS",
        help="Seconds to wait under --non-interactive (default: 30)",
    )
    run.add_argument(
        "--stop-on-failure", action="store_true",
        help="Abort on first step that fails to extend expiration (default skips and continues)",
    )
    return parser


def _normalize_argv(argv: Optional[Sequence[str]]) -> list:
    """Default to `plan` when subcommand is omitted or only flags are passed."""
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        return ["plan"]
    if values[0] in COMMANDS or values[0] in ("-h", "--help"):
        return values
    return ["plan"] + values


def _plan(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
) -> int:
    readers = _resolve_readers(args, state_reader, offer_reader)
    if readers is None:
        print(DEVICE_HINT, file=sys.stderr)
        return EXIT_NO_DEVICE
    bundle = _collect(args, *readers)
    _print_plan(bundle, args)
    return EXIT_OK


def _run(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
    executor: Optional[Callable[[core.ComboAction], object]],
) -> int:
    if not (args.execute and args.yes):
        readers = _resolve_readers(args, state_reader, offer_reader)
        if readers is None:
            print(DEVICE_HINT, file=sys.stderr)
        else:
            _print_plan(_collect(args, *readers), args)
            print()
        print(EXECUTE_HINT)
        return EXIT_USAGE

    readers = _resolve_readers(args, state_reader, offer_reader)
    if readers is None:
        print(DEVICE_HINT, file=sys.stderr)
        return EXIT_NO_DEVICE
    state_fn, offer_fn = readers

    bundle = _collect(args, state_fn, offer_fn)
    _print_plan(bundle, args)
    print()
    if len(bundle.accounts) > 1 and not getattr(args, "account", None):
        print(
            "Multiple accounts found in ownership rows. "
            "Specify which account to target with --account <email>.",
            file=sys.stderr,
        )
        for name in bundle.accounts:
            print(f"  - {name}", file=sys.stderr)
        print(
            "(Mixing accounts would compare the wrong expiration timestamp.)",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if not bundle.possible:
        print(f"Cannot execute in this state: {bundle.verdict}", file=sys.stderr)
        return EXIT_FAILED
    if not bundle.actions:
        print("Aborting: no runnable free offers available.", file=sys.stderr)
        return EXIT_FAILED

    runner = (
        executor
        if executor is not None
        else RealExecutor(
            timeout=args.timeout,
            open_app=getattr(args, "open_app", False),
            account=getattr(args, "account", "") or "",
        )
    )
    return _execute_actions(args, bundle, state_fn, runner)


def main(
    argv: Optional[Sequence[str]] = None,
    state_reader: Optional[Callable[[], list]] = None,
    offer_reader: Optional[Callable[[], list]] = None,
    executor: Optional[Callable[[core.ComboAction], object]] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv))
    try:
        if args.command == "run":
            return _run(args, state_reader, offer_reader, executor)
        return _plan(args, state_reader, offer_reader)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
