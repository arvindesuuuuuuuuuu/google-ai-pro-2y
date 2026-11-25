"""sku_scanner_core.py -- Pure logic for the read-only Play SKU scanner.

Has zero dependency on frida, adb, or the network. Extracts only the five
allowlisted fields from offer events and strips tokens and account metadata.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

G1_PREFIX = "g1"
# Ultra-family tiers (20tb / play.20tb / ai.ultra) are adapted from iOS product IDs
# tracked in https://github.com/crhywun/g1-acq and are unverified on Play Store;
# dotless tiers are split by _PERIOD_TOKEN_RE inside parse_sku().
DEFAULT_TIERS = ("2tb.ai", "play.2tb.ai", "5tb.ai", "ai.pro", "20tb", "play.20tb", "ai.ultra")
DEFAULT_PERIODS = (
    "1month",
    "2months",
    "3months",
    "4months",
    "6months",
    "12months",
    "15months",
    "18months",
)
DEFAULT_SUFFIXES = (
    "_eft",
    "_eft.acq",
    "_ip_90p",
    "_ip_90p.acq",
    "_ip_95p.acq",
    "_ip_75p.acq",
)
VARIANT_SUFFIX = ".subs_variant"

# Allowlist for serialized input/output fields (any other keys are discarded).
ROW_FIELDS = ("sku", "offer_id", "period", "micros", "title")

_PERIOD_RE = re.compile(r"(\d+)(months?)")
_TIER_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_SUFFIX_RE = re.compile(r"[._][A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_VARIANT_TAIL_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_PERIOD_TOKEN_RE = re.compile(r"\.(\d+months?)([._].+)$")
_SKU_TAIL_RE = re.compile(r"(\d+months?)([._].+)$")
_MICROS_RE = re.compile(r"[+-]?\d+")
_YEAR_RE = re.compile(r"P(\d+)Y")
_MONTH_RE = re.compile(r"P(\d+)M")
_UNKNOWN_PERIOD_MONTHS = 10**6


def validate_period(period: str) -> str:
    """Normalize period strings (singular for 1month, plural otherwise)."""
    if not isinstance(period, str):
        raise ValueError(f"period must be a string: {period!r}")
    match = _PERIOD_RE.fullmatch(period.strip())
    if match is None or int(match.group(1)) < 1:
        raise ValueError(f"Invalid period: {period!r}")
    months = int(match.group(1))
    return "1month" if months == 1 else f"{months}months"


def build_sku(tier: str, period: str, suffix: str) -> str:
    """Assemble a g1 SKU from tier, period, and suffix."""
    if not isinstance(tier, str) or _TIER_RE.fullmatch(tier or "") is None:
        raise ValueError(f"Invalid tier: {tier!r}")
    if not isinstance(suffix, str) or _SUFFIX_RE.fullmatch(suffix or "") is None:
        raise ValueError(f"Invalid suffix: {suffix!r}")
    if period:
        return f"{G1_PREFIX}.{tier}.{validate_period(period)}{suffix}"
    return f"{G1_PREFIX}.{tier}{suffix}"


def _split_sku_tail(tier: str, tail: str) -> Optional[tuple]:
    """Split substring after tier into (tier, period, suffix)."""
    match = _SKU_TAIL_RE.fullmatch(tail)
    if match is not None:
        return (tier, match.group(1), match.group(2))
    if _VARIANT_TAIL_RE.fullmatch(tail) is not None:
        return (tier, "", "." + tail)
    return None


def parse_sku(sku: str) -> Optional[tuple]:
    """Parse an SKU into (tier, period, suffix), or None if invalid.

    Matches longer known tiers first so dotted tiers (e.g. 2tb.ai) stay intact,
    then falls back to period-token and variant-suffix matching.
    """
    if not isinstance(sku, str):
        return None
    prefix = G1_PREFIX + "."
    if not sku.startswith(prefix):
        return None
    body = sku[len(prefix) :]

    for tier in sorted(set(DEFAULT_TIERS), key=len, reverse=True):
        head = tier + "."
        if body.startswith(head):
            parsed = _split_sku_tail(tier, body[len(head) :])
            if parsed is not None:
                return parsed

    # Support unlisted tiers when a period token is present.
    match = _PERIOD_TOKEN_RE.search(body)
    if match is not None:
        tier = body[: match.start()]
        if _TIER_RE.fullmatch(tier) is not None:
            return (tier, match.group(1), match.group(2))
    if body.endswith(VARIANT_SUFFIX):
        tier = body[: -len(VARIANT_SUFFIX)].rstrip(".")
        if _TIER_RE.fullmatch(tier) is not None:
            return (tier, "", VARIANT_SUFFIX)
    return None


def generate_candidates(
    tiers: Sequence[str] = DEFAULT_TIERS,
    periods: Sequence[str] = DEFAULT_PERIODS,
    suffixes: Sequence[str] = DEFAULT_SUFFIXES,
    include_variants: bool = True,
    limit: Optional[int] = None,
) -> list:
    """Deterministically generate unique tier + period + suffix SKU combinations."""
    for period in periods:
        validate_period(period)
    normalized_periods = [validate_period(period) for period in periods]

    unique_tiers = list(dict.fromkeys(tiers))
    candidates: list = []
    seen = set()

    def add(sku: str) -> None:
        if sku not in seen:
            seen.add(sku)
            candidates.append(sku)

    for tier in unique_tiers:
        if include_variants:
            add(build_sku(tier, "", VARIANT_SUFFIX))
        for period in normalized_periods:
            for suffix in suffixes:
                add(build_sku(tier, period, suffix))
    return candidates[:limit] if limit is not None else candidates


@dataclass
class OfferRow:
    """Sanitized offer row extracted from an agent offer event."""

    sku: str
    offer_id: str
    period: str
    micros: Optional[int]
    title: str


def _coerce_micros(value: object) -> Optional[int]:
    """Parse integer or numeric string micros; return None when unknown."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _MICROS_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def parse_offer_events(events: Iterable[object]) -> list:
    """Convert raw events or Frida envelopes into OfferRow entries (ignores non-offer events)."""
    rows: list = []
    for event in events:
        payload = event
        if isinstance(event, dict) and isinstance(event.get("payload"), dict):
            payload = event["payload"]
        if not isinstance(payload, dict) or payload.get("ev") != "offer":
            continue
        sku = payload.get("product")
        offer_id = payload.get("id")
        if not isinstance(sku, str) or not sku or not isinstance(offer_id, str) or not offer_id:
            continue
        rows.append(
            OfferRow(
                sku=sku,
                offer_id=offer_id,
                period=str(payload.get("period") or ""),
                micros=_coerce_micros(payload.get("micros")),
                title=str(payload.get("title") or payload.get("name") or ""),
            )
        )
    return rows


def dedupe_rows(rows: Iterable[OfferRow]) -> list:
    """Keep only the first OfferRow per (sku, offer_id) pair."""
    seen = set()
    unique: list = []
    for row in rows:
        key = (row.sku, row.offer_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def period_months(period: str) -> int:
    """Convert P1M / P1Y strings to month counts; unknown formats sort last."""
    if isinstance(period, str):
        match = _MONTH_RE.fullmatch(period)
        if match is not None:
            return int(match.group(1))
        match = _YEAR_RE.fullmatch(period)
        if match is not None:
            return 12 * int(match.group(1))
    return _UNKNOWN_PERIOD_MONTHS


def sort_rows(rows: Iterable[OfferRow]) -> list:
    """Stable sort by sku -> billing period -> offer_id."""
    return sorted(
        rows,
        key=lambda row: (row.sku, period_months(row.period), row.offer_id or ""),
    )


def _table_footer() -> list:
    return [
        "Note: micros=0 only indicates the price of the first billing phase; "
        "it does not guarantee trial eligibility or renewal pricing.",
        "Note: Read-only scan results are informational only; verify actual charges on Google Play.",
    ]


def format_table(rows: Iterable[OfferRow], limit: int = 20) -> list:
    """Render a Markdown-style table of offers with safety notes."""
    ordered = sort_rows(rows)
    lines = [
        "SKU | offerId | Period | micros | Title",
        "--- | --- | --- | ---: | ---",
    ]
    if not ordered:
        lines.append("No matching offers found (read-only scan).")
        lines.extend(_table_footer())
        return lines

    shown = ordered[:limit] if limit is not None else ordered
    for row in shown:
        micros = "unknown" if row.micros is None else f"{row.micros:,}"
        lines.append(
            f"{row.sku} | {row.offer_id} | {row.period or '-'} | "
            f"{micros} | {row.title or '-'}"
        )
    if limit is not None and len(ordered) > limit:
        # --limit caps generated candidates, not display rows; avoid mentioning --limit here.
        lines.append(
            f"... {len(ordered) - limit} more row(s) omitted (showing first {limit})."
        )
    lines.extend(_table_footer())
    return lines


def to_json(rows: Iterable[OfferRow], indent: int = 2) -> str:
    """Serialize allowlisted OfferRow fields to JSON (preserving Unicode)."""
    payload = [
        {field: getattr(row, field) for field in ROW_FIELDS}
        for row in sort_rows(rows)
    ]
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def parse_report(payload: str) -> list:
    """Validate and parse a saved JSON report back into OfferRow instances."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Report must be valid UTF-8 text") from exc
    if not isinstance(payload, str):
        raise ValueError("Report must be a JSON string")
    try:
        data = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not parse report JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("Report must be a JSON array of OfferRow objects")

    allowed = set(ROW_FIELDS)
    rows: list = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"report[{index}] must be an object")
        if set(item) != allowed:
            raise ValueError(
                f"report[{index}] has unexpected/missing keys: {sorted(set(item) ^ allowed)}"
            )
        if not all(isinstance(item[key], str) for key in ("sku", "offer_id", "period")):
            raise ValueError(f"report[{index}] sku / offer_id / period must be strings")
        micros = item["micros"]
        if micros is not None and (isinstance(micros, bool) or not isinstance(micros, int)):
            raise ValueError(f"report[{index}] micros must be an integer or null")
        title = item["title"]
        if title is not None and not isinstance(title, str):
            raise ValueError(f"report[{index}] title must be a string or null")
        rows.append(
            OfferRow(
                sku=item["sku"],
                offer_id=item["offer_id"],
                period=item["period"],
                micros=micros,
                title=title,
            )
        )
    return rows
