# SKU Scanner (Read-Only)

`scripts/sku_scanner.py` is a read-only tool for inspecting Google One subscription SKUs and offers (pricing and duration). Invoke it via `./scripts/run.sh scan ...`.

## Purpose

Whereas `step1` through `step4` enroll trials, `scan` checks **which SKUs and offers exist** ahead of time.
It supports generating candidate SKUs offline, querying offers on a live device, and formatting saved JSON reports.

## Prerequisites (For Live Device Queries)

`query` attaches via Frida only to an **already running Google One process**.
It never launches the app itself. All four conditions below must be satisfied, or it will exit with a connection error:

1. A rooted device with USB debugging enabled appears as a single entry in `adb devices`.
2. `frida-server` is running on the device (fix version mismatches with `./scripts/run.sh fix-frida`).
3. Google One (`com.google.android.apps.subscriptions.red`) is already running on the device (open it once from the home screen).
4. `./scripts/run.sh check` passes (verifies device / root / frida / billing module).

`generate` and `summarize` require neither a device nor frida.

## Read-Only Guarantees

The scanner never performs any of the following:

- Opening checkout sheets, purchasing, or subscribing
- Launching or restarting apps (never calls `launch_app()`)
- Evaluating eligibility, applying trials, or stacking offers
- Writing or saving purchase tokens or account credentials

When querying a device, it attaches only to the running Google One process and loads the agent in `list_only` mode (`build_js(plan="5x", list_only=True)`).
Billing checkout code paths are never entered. Immediately before injection, it verifies two invariants and aborts without injecting if either fails (fail-closed):

- Exactly one read-only flag `var LIST_ONLY=true;` is present
- Exactly one `PRODUCTS` literal is present for target SKU substitution

If `LIST_ONLY` ever fails to suppress checkout actions, receiving any `launched`, `stack_start`, `activity`, or `purchase_cb` event immediately aborts the scan.

## Usage

### Generate Candidate SKUs (Offline)

```bash
./scripts/run.sh scan generate --tiers 2tb.ai --periods 18months,12months --suffixes _eft --format table
```

With `--format json`, only the JSON array of SKUs is written to stdout (status notes go to stderr).

### Query Offers on a Device

`query` requires an explicit target list. You must pass either `--skus` or `--candidates` (omitting both exits with an error before touching the device).

```bash
# Query default candidate list in batches of 20 with an 8s timeout
./scripts/run.sh scan query --candidates --batch-size 20 --timeout 8

# Query a specific SKU and output JSON
./scripts/run.sh scan query --skus g1.2tb.ai.subs_variant --format json
```

- `--skus` and `--candidates` are mutually exclusive.
- `--timeout` must be between 1 and 30 seconds (default: 8). Out-of-range values error out before connecting.
- `--batch-size` must be >= 1 (default: 20). `0` raises an error.
- `--limit` caps the candidate count, not table rows. The default candidate set has 343 entries; setting `--limit 120` can silently drop longer-duration `ai.pro` SKUs. Omit `--limit` to scan the full set.

Results are saved to `ultra0-report/sku-scan-<timestamp>.json` (override with `--output`; file permissions `0600`). The saved path is printed to stderr so stdout stays clean for piping.

If a batch times out, partial results are still saved and a timeout warning is printed to stderr. The exit code becomes non-zero only if zero batches complete.
If timeouts persist, inspect the device state, increase `--timeout`, or run `./scripts/run.sh check`.

### Summarize a Saved Report

```bash
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-20261124-130000.json
```

Pass `--format json` to re-emit formatted JSON.

### Unit Tests

```bash
./scripts/run.sh test
# or
python3 -m unittest discover -s tests -v
```

## Ultra Tiers and iOS-Derived Findings

The default candidate set includes Ultra tiers (`20tb`, `play.20tb`, `ai.ultra`) along with `_eft.acq` and `_ip_75p.acq` suffixes. These were incorporated from product IDs seen in the iOS project [g1-acq](https://github.com/crhywun/g1-acq), yielding 7 tiers x (1 variant + 8 periods x 6 suffixes) = 343 default candidates.

- `_ip_90p` and `_ip_75p` represent **paid 90%-off / 75%-off introductory discounts**, not free trials.
- `_eft.acq` marks free trials carrying an `.acq` suffix, such as `g1.20tb.1month_eft.acq` and `g1.ai.ultra.1month_eft.acq` used for Ultra in `g1-acq` v7.6.0.
- `.acq` indicates an acquisition promo SKU (appended after the period suffix, e.g., `_ip_90p.acq`).
- `g1.30tb` is a bare SKU that does not fit the `g1.<tier>.<period><suffix>` pattern. It is excluded from candidate generation and must be queried explicitly via `scan query --skus g1.30tb`.

Whether Ultra tiers or `g1.30tb` are offered on your account or region is unverified; being in the candidate list does not guarantee Play returns an offer.

### Display Rewriting vs. Actual Billing Amount

`g1-acq` MITMs responses from `one.google.com` and regex-replaces `g1.*` product ID strings in the HTML body without parsing JSON or altering price, trial, or eligibility fields.

On Android, actual billing is controlled by Play Billing (`queryProductDetailsAsync` -> `offerToken`).
Rewriting display data from `one.google.com` does not alter what Play charges, so do not mistake display substitution for an active discount or trial. The scanner touches neither flow and strictly reads offers returned by Play.

## Exit Codes

| Code | Meaning |
| ---: | --- |
| 0 | Success (including partial timeouts as long as at least one batch completed) |
| 1 | Runtime error (device connection failure, read-only assertion failure, zero completed batches, etc.) |
| 2 | Invalid arguments (missing `--skus`/`--candidates`, or `--timeout`/`--batch-size` out of range) |

Errors are emitted as a single line to stderr without tracebacks.

## Tokens and Privacy

- Any `token`, `offerToken`, `purchaseToken`, `secret`, `email`, or `account` fields in offer events are stripped during parsing; only `sku`, `offer_id`, `period`, `micros`, and `title` are retained.
- Tokens are never written to reports or stdout. Review output carefully before sharing screenshots or snippets.
- Reports are stored in `ultra0-report/` (excluded by `.gitignore`) with `0600` permissions.

## Caveats

- **`micros=0` only reflects the initial billing phase price.** It does not prove trial eligibility or reveal renewal pricing. Always verify the actual charge and next payment date on Play's Subscriptions screen.
- Be mindful of device load. Avoid prolonged continuous scans or repeated runs on the same device (long Frida sessions can cause instability).
- Scan results are informational only; enrollment actions only occur when running `step1` through `step4` (or `combo run --execute --yes`) explicitly.
