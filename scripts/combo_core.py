"""combo_core.py -- Pure planning logic for stacking free Google AI Pro offers.

Has zero dependency on frida, adb, or the network. Takes ownership rows and an
offer list read from a device, classifies the current subscription state, and
decides which actions to execute in what order. Payment confirmation is never
handled here (the operator performs checkout manually).
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

# library.db doc_id format: `subs:com.google.android.apps.subscriptions.red:<sku>`.
# Requiring `subs:` prevents one-time `inapp:...` rows from being misread as subscriptions.
SUBSCRIPTION_PREFIX = "subs:"
SUBSCRIPTION_MARKER = "subscriptions.red:"
# SKU suffix carrying stackable free offers.
STACK_SKU_SUFFIX = ".subs_variant"
# offerId representing the regular paid base plan on subs_variant.
BASE_OFFER_ID = "null"
# Standalone offer SKU suffix used for fresh signups.
NEW_SUBSCRIPTION_SUFFIX = "_eft"

STATE_FRESH = "fresh"
STATE_ACTIVE = "active"
STATE_EXPIRED = "expired"
STATE_CLASSES = (STATE_FRESH, STATE_ACTIVE, STATE_EXPIRED)

KIND_NEW = "new_subscription"
KIND_STACK = "stack"

_PERIOD_RE = re.compile(r"P(?:(\d+)Y)?(?:(\d+)M)?")
_INT_RE = re.compile(r"[+-]?\d+")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _coerce_ms(value: object) -> Optional[int]:
    """Convert valid epoch millisecond values to int; return None when unknown."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


@dataclass
class SubscriptionState:
    """Subscription state parsed from one ownership row in library.db."""

    sku: str
    valid_until_ms: Optional[int]
    account: str
    active: bool


def parse_ownership_rows(
    rows: Iterable[object], now_ms: Optional[int] = None
) -> list:
    """Extract subscription rows from ownership tuples.

    rows is an iterable of `(doc_id, account, subs_valid_until_time)`.
    Subscription rows follow `subs:com.google.android.apps.subscriptions.red:<sku>`,
    where the SKU is everything after the final `:`. Malformed rows are skipped
    without raising so one corrupt row does not halt the run.
    """
    now = _now_ms() if now_ms is None else int(now_ms)
    states: list = []
    for row in rows:
        try:
            doc_id, account, valid_until = row
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(doc_id, str)
            or not doc_id.startswith(SUBSCRIPTION_PREFIX)
            or SUBSCRIPTION_MARKER not in doc_id
        ):
            continue
        sku = doc_id.rsplit(":", 1)[-1].strip()
        if not sku:
            continue
        valid_ms = _coerce_ms(valid_until)
        states.append(
            SubscriptionState(
                sku=sku,
                valid_until_ms=valid_ms,
                account=account.strip() if isinstance(account, str) else "",
                active=valid_ms is not None and valid_ms > now,
            )
        )
    return states


def latest_end_ms(states: Sequence[SubscriptionState]) -> Optional[int]:
    """Return the latest expiration timestamp across active rows, or None."""
    ends = [
        state.valid_until_ms
        for state in states
        if state.active and state.valid_until_ms is not None
    ]
    return max(ends) if ends else None


def classify_state(
    states: Sequence[SubscriptionState], now_ms: Optional[int] = None
) -> str:
    """Return fresh (no sub rows), active (>=1 active row), or expired (all ended)."""
    now = _now_ms() if now_ms is None else int(now_ms)
    if not states:
        return STATE_FRESH
    if any(
        state.valid_until_ms is not None and state.valid_until_ms > now
        for state in states
    ):
        return STATE_ACTIVE
    return STATE_EXPIRED


@dataclass
class ComboAction:
    """Single planned execution step with calendar-estimated expected_end_ms."""

    kind: str
    sku: str
    offer_id: str
    months: int
    expected_end_ms: Optional[int]
    note: str


def months_from_period(period: object) -> int:
    """Convert Play ISO billingPeriod (e.g. P1Y6M) to integer months; 0 if unknown."""
    if not isinstance(period, str):
        return 0
    match = _PERIOD_RE.fullmatch(period.strip().upper())
    if match is None:
        return 0
    years = int(match.group(1) or 0)
    months = int(match.group(2) or 0)
    return 12 * years + months


def add_months_ms(base_ms: int, months: int) -> int:
    """Add calendar months (clamping day-of-month, e.g. Jan 31 + 1 month -> Feb 28)."""
    moment = datetime.fromtimestamp(int(base_ms) / 1000)
    total = moment.month - 1 + int(months)
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return int(moment.replace(year=year, month=month, day=day).timestamp() * 1000)


def _format_date(ms_value: Optional[int]) -> str:
    if ms_value is None:
        return "-"
    return datetime.fromtimestamp(int(ms_value) / 1000).strftime("%Y-%m-%d")


def _offer_field(offer: object, key: str) -> str:
    if not isinstance(offer, dict):
        return ""
    value = offer.get(key)
    return value.strip() if isinstance(value, str) else ""


def _is_free(offer: object) -> bool:
    """Return True only when micros is explicitly 0 (never treat unknown as free)."""
    if not isinstance(offer, dict):
        return False
    micros = _coerce_ms(offer.get("micros"))
    return micros == 0


def stack_offers(offers: Iterable[object]) -> list:
    """Return free stackable subs_variant offers with known durations, longest first."""
    picked: dict = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        sku = _offer_field(offer, "sku")
        offer_id = _offer_field(offer, "offer_id")
        if not sku.endswith(STACK_SKU_SUFFIX) or not offer_id:
            continue
        if offer_id == BASE_OFFER_ID or not _is_free(offer):
            continue
        months = months_from_period(offer.get("period"))
        if months <= 0 or offer_id in picked:
            continue
        picked[offer_id] = offer
    return sorted(
        picked.values(),
        key=lambda offer: (
            -months_from_period(offer.get("period")),
            _offer_field(offer, "offer_id"),
        ),
    )


def has_stack_offers(offers: Iterable[object]) -> bool:
    """Return True if at least one stackable free offer exists."""
    return bool(stack_offers(offers))


def new_subscription_offer(offers: Iterable[object]) -> Optional[dict]:
    """Return the longest free standalone offer suitable for a new subscription."""
    candidates = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        sku = _offer_field(offer, "sku")
        offer_id = _offer_field(offer, "offer_id")
        if not sku.endswith(NEW_SUBSCRIPTION_SUFFIX) or not offer_id:
            continue
        if sku.endswith(STACK_SKU_SUFFIX) or offer_id == BASE_OFFER_ID:
            continue
        if not _is_free(offer):
            continue
        months = months_from_period(offer.get("period"))
        if months <= 0:
            continue
        candidates.append((months, sku, offer_id, offer))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return candidates[0][3]


def stack_is_possible(
    state_class: str, has_stack_offers: bool, auto_renew: bool
) -> tuple:
    """Return (eligible, single-sentence explanation)."""
    if state_class == STATE_FRESH:
        return True, "No active subscription; eligible to start a new subscription with the longest free offer"
    if state_class == STATE_EXPIRED:
        return True, "Expired subscription; eligible to start as a new subscription"
    if state_class == STATE_ACTIVE:
        if not has_stack_offers:
            return False, "No stackable free offers found"
        if not auto_renew:
            return False, "Auto-renew is off; cannot stack (do not cancel before stacking)"
        return True, "Active subscription found; stacking is eligible"
    raise ValueError(f"Unknown subscription state: {state_class!r}")


def plan_actions(
    state_class: str,
    offers: Iterable[object],
    *,
    now_ms: Optional[int] = None,
    max_steps: Optional[int] = 4,
    base_end_ms: Optional[int] = None,
) -> list:
    """Build the ordered sequence of combo steps.

    - fresh / expired: start with the longest standalone free offer, then stack
      free subs_variant offers in descending month order.
    - active: stack subs_variant offers only; never emit a new_subscription step
      that could overwrite an active plan.
    - base_end_ms: accumulates expected expiration from this timestamp when set.
    """
    if state_class not in STATE_CLASSES:
        raise ValueError(f"Unknown subscription state: {state_class!r}")
    now = _now_ms() if now_ms is None else int(now_ms)
    steps = 4 if max_steps is None else max(0, int(max_steps))
    if steps == 0:
        return []

    candidates: list = []
    if state_class == STATE_ACTIVE:
        candidates.extend((KIND_STACK, offer) for offer in stack_offers(offers))
    else:
        chosen = new_subscription_offer(offers)
        if chosen is not None:
            candidates.append((KIND_NEW, chosen))
        candidates.extend((KIND_STACK, offer) for offer in stack_offers(offers))

    actions: list = []
    seen_offer_ids: set = set()
    end_ms = int(base_end_ms) if base_end_ms is not None else now
    for kind, offer in candidates:
        if len(actions) >= steps:
            break
        offer_id = _offer_field(offer, "offer_id")
        months = months_from_period(offer.get("period"))
        if not offer_id or months <= 0 or offer_id in seen_offer_ids:
            continue
        seen_offer_ids.add(offer_id)
        end_ms = add_months_ms(end_ms, months)
        actions.append(
            ComboAction(
                kind=kind,
                sku=_offer_field(offer, "sku"),
                offer_id=offer_id,
                months=months,
                expected_end_ms=end_ms,
                note=_action_note(kind, months),
            )
        )
    return actions


def _action_note(kind: str, months: int) -> str:
    if kind == KIND_NEW:
        return (
            f"Starts a new subscription with a +{months}-month free offer. "
            "Never run while an active subscription exists to avoid replacing it."
        )
    return (
        f"Stacks +{months} months onto your active subscription. "
        "Verify the Play checkout sheet displays 0 before confirming."
    )


def format_plan(
    actions: Sequence[ComboAction],
    state_class: str,
    latest_end_ms: Optional[int],
    *,
    auto_renew: bool = True,
    has_stack_offers: Optional[bool] = None,
) -> list:
    """Return human-readable plan lines for stdout."""
    lines = ["Google AI Pro Free-Offer Combo Plan", "-" * 40]
    if latest_end_ms is None:
        lines.append("Current expiration: None (not subscribed or expired)")
    else:
        lines.append(f"Current expiration: {_format_date(latest_end_ms)}")

    stack_available = (
        any(action.kind == KIND_STACK for action in actions)
        if has_stack_offers is None
        else bool(has_stack_offers)
    )
    possible, verdict = stack_is_possible(state_class, stack_available, auto_renew)
    lines.append(f"Verdict: {verdict}")
    if not possible:
        lines.append("Will not execute in this state (satisfy the requirement above first).")

    if actions:
        lines.append("Execution plan:")
        for index, action in enumerate(actions, start=1):
            label = "New" if action.kind == KIND_NEW else "Stack"
            lines.append(
                f"{index}. [{label}] {action.sku} / {action.offer_id} / "
                f"+{action.months} months -> {_format_date(action.expected_end_ms)}"
            )
    else:
        lines.append(
            "No runnable free offers found "
            "(inspect the read-only scanner output first)."
        )

    lines.extend(
        [
            "Note: micros=0 only reflects the price of the initial billing phase.",
            "      It does not prove free-trial eligibility or future renewal pricing; "
            "always verify exact charge and dates on the Play checkout sheet.",
            "Note: Purchasing an offer under a different SKU while subscribed may "
            "replace your active plan instead of stacking onto it.",
            "Note: Checkout must be confirmed manually by the operator (this script never taps Subscribe).",
        ]
    )
    return lines


# --------------------------------------------------------------------------
# Account scoping & meaningful extension detection
# --------------------------------------------------------------------------
#: Increments below 24h are ignored because Play occasionally rewrites timestamps.
MIN_EXTENSION_MS = 24 * 60 * 60 * 1000


def is_meaningful_extension(before_ms: Optional[int], after_ms: Optional[int]) -> bool:
    """Return True only if expiration grew by at least one full day.

    Play Store occasionally shifts subs_valid_until_time by a few hours on the same
    day; requiring >= 24h avoids mistaking timestamp jitter for a successful stack.
    """
    if after_ms is None:
        return False
    if before_ms is None:
        return True
    return int(after_ms) - int(before_ms) >= MIN_EXTENSION_MS


def filter_by_account(states: Iterable[object], account: str) -> list:
    """Filter SubscriptionState entries to the specified Google account."""
    target = (account or "").strip().lower()
    if not target:
        return list(states)
    return [s for s in states if str(getattr(s, "account", "")).strip().lower() == target]


def distinct_accounts(states: Iterable[object]) -> list:
    """Return sorted unique non-empty account emails present in ownership rows."""
    names = {
        str(getattr(s, "account", "")).strip()
        for s in states
        if str(getattr(s, "account", "")).strip()
    }
    return sorted(names)
