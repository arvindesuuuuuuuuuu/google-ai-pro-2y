# Combo Planner (`scripts/combo.py`)

A planning and execution tool for stacking Google AI Pro free offers based on your account's current state. It defaults to dry-run mode, and payment confirmation is always manual.

- Pure planning logic in `scripts/combo_core.py` decides what to apply and in what order.
- The device is only queried when `--device` is passed.
- Actual execution requires both `--execute` and `--yes`.

## 1. Purpose

Measurements on 2026-11-24 (Pixel 7a / Google AI Pro 5TB / valid until 2028-11-21) observed both:

- Standalone free offers: `g1.2tb.ai.{2,3,4,6,12,15,18}months_eft` (`eft-*`)
- Stackable free offers under `g1.2tb.ai.subs_variant`: `12months-eft`, `6months-eft`, `6months-eft-2`, and `6months-eft-ss-2026`

However, the same sequence does not work identically for every account.
Because `fresh`, `expired`, and `active` subscriptions require different actions, this tool first detects account state and checks stackability before executing.

## 2. Three Account States

Account state is read from Google Play's `library.db` (`ownership` table).
Subscription ownership rows have `doc_id` formatted as `subs:com.google.android.apps.subscriptions.red:<sku>`, and are treated as active when `subs_valid_until_time` (epoch milliseconds) is in the future.

| State | Detection Rule | Initial Action |
|---|---|---|
| `fresh` | Zero subscription ownership rows | Start a new subscription with the longest standalone free offer (`18months_eft`) |
| `active` | One or more valid ownership rows | Check stackability; if eligible, stack free `subs_variant` offers |
| `expired` | Ownership rows exist, all expired | Treat as a new subscription starting from `18months_eft` |

Although `fresh` and `expired` both start from a new subscription, keeping them distinct makes it clear why an expired account starts fresh.

## 3. Determining Stackability

Stackability is centralized in `stack_is_possible()`:

| State | Stackable Free Offers | Auto-Renew | Stackable | Displayed Reason |
|---|---|---|---|---|
| `fresh` | (any) | (any) | Yes | Can start as a new subscription with the longest free offer |
| `expired` | (any) | (any) | Yes | Expired subscription can be treated as a new registration |
| `active` | Present | ON | Yes | Active subscription present; eligible for stacking |
| `active` | Present | OFF | No | Auto-renew is off; stacking unavailable (do not cancel early) |
| `active` | None | (any) | No | No stackable free offers found |

Only entries whose `sku` ends with `.subs_variant`, whose `offer_id` is not `null` (standard base plan), whose `micros` equals `0`, and whose period is parseable count as stackable free offers.

Because auto-renew state cannot be reliably read from `library.db`, you pass `--auto-renew` or `--no-auto-renew` manually (default: ON).
Keep in mind that the default assumes auto-renew is ON -- verify under Google Play > "Payments & subscriptions" before executing.

## 4. Human-in-the-Loop Payment

The script stops at **opening the billing sheet**. Reviewing the price and confirming payment is strictly done by a human:

1. Script opens the billing flow for the target SKU / `offerId` (up to `launchBillingFlow`)
2. User visually confirms price = 0 and the applied duration on the device screen
3. User completes payment manually
4. User presses Enter in the terminal
5. Script re-reads `library.db` to check whether the expiration date extended:
   - If extended by at least 1 day, marks the step successful and moves to the next step (smaller millisecond shifts from Play rewriting ownership rows do not count as success).
   - If unchanged, assumes the offer was already used or ineligible and **skips to the next offer** (important for partial stacks where previously claimed offers still appear in query results).
   - Pass `--stop-on-failure` to halt immediately on the first non-extending step.
   - Exit code is non-zero if zero steps succeed.

The script never performs any of the following:

- Auto-tapping "Subscribe"
- Calling `ultra0-17.confirm_zero()`, `add_card()`, or `launch_app()`
- Proceeding without waiting for user confirmation

Before injecting JS, the runner verifies that `var LIST_ONLY=true;` is absent and that both `PRODUCTS` and `PREFER` literals were substituted exactly once. If any check fails, it aborts immediately.

### 4-1. How Stacking Works (Passing `purchaseToken` Directly)

Stacking (applying another offer on `subs_variant`) requires passing the **existing subscription's purchase token** to `BillingFlowParams.SubscriptionUpdateParams.setOldPurchaseToken()`.
This tool reads that token from `library.db` (`inapp_purchase_data` in the `ownership` table).

- It intentionally avoids `queryPurchasesAsync`, which stalled without firing callbacks on live Android 16 + billing module 5.x devices. Only the verified `library.db` path is used.
- The purchase token is never printed to stdout, logs, or reports; it is passed purely in-memory to the Frida agent.
- Temporary copies of `library.db` on both device (`/data/local/tmp/`) and host (temp dir) are deleted in a `finally` block regardless of success or failure.
- Without `--account`, multiple Play accounts on the same device can cause token mix-ups. See "6-6. Account Mismatches" below.

### 4-2. Google One Must Be in the Foreground

Billing sheets can only launch from a **live Activity** of Google One (`com.google.android.apps.subscriptions.red`).
If another app is in the foreground, the agent returns `no_activity` and exits without showing a sheet.

Open Google One and leave it in the foreground before running.
Pass `--open-app` to have `adb` (`monkey`) bring Google One to the foreground only when needed (disabled by default to avoid unsolicited app launches).

## 5. Commands

```bash
# View plan only (requires --device to inspect the device)
./scripts/run.sh combo plan --device

# Specify an account to guard against multi-account mix-ups
./scripts/run.sh combo plan --device --account you@example.com

# Execute (wait for Enter after each step)
./scripts/run.sh combo run --device --execute --yes

# Stop immediately if any step fails to extend expiration (default skips and continues)
./scripts/run.sh combo run --device --execute --yes --stop-on-failure

# Wait a fixed number of seconds instead of pressing Enter (automation/testing)
./scripts/run.sh combo run --device --execute --yes \
  --non-interactive --wait-seconds 30

# Simulate auto-renew OFF (reports stacking unavailable)
./scripts/run.sh combo plan --device --no-auto-renew

# Bring Google One to the foreground automatically if not active
./scripts/run.sh combo run --device --execute --yes --open-app

# Limit step count (default: 4)
./scripts/run.sh combo plan --device --max-steps 2
```

Omitting the subcommand defaults `combo` to `plan`.

### Sample Output (`active` / Valid Until 2028-11-21)

```text
Google AI Pro Free Offer Combo Plan
-----------------------------------
Current expiration: 2028-11-21
Status: Stackable (active subscription detected)
Execution plan:
1. [Stack] g1.2tb.ai.subs_variant / 12months-eft / +12m -> 2029-11-21
2. [Stack] g1.2tb.ai.subs_variant / 6months-eft / +6m -> 2030-05-21
3. [Stack] g1.2tb.ai.subs_variant / 6months-eft-2 / +6m -> 2030-11-21
Note: micros=0 only indicates that the initial billing phase price is 0.
...
```

`expected_end_ms` estimates calendar-month extensions; always confirm the actual date on Play's UI.

### Exit Codes

| Code | Meaning |
|---:|---|
| 0 | Success (`plan` displayed, or `run` completed with at least one extension) |
| 1 | Failure (no step extended expiration, preconditions unmet, etc.) |
| 2 | Usage error (`run` invoked without both `--execute` and `--yes`) |
| 3 | Device unreadable (`--device` omitted or device unreachable) |

### Why `--device` Is Opt-In by Default

Although `plan` is read-only, device access requires explicit `--device` opt-in to avoid accidentally inspecting an unintended phone or Play account.
Without `--device`, the script touches neither the device nor `adb`, explains why, and exits with code 3.

## 6. Risks and Caveats

### 6-1. Replacement vs. Stacking

**`g1.2tb.ai.18months_eft` is a different SKU from `g1.2tb.ai.subs_variant`.**
Buying `18months_eft` while an active subscription exists may replace your current subscription (or be rejected) instead of stacking.
Accordingly, `combo` never schedules a `new_subscription` action when state is `active`; it only starts with the longest standalone offer for `fresh` or `expired` accounts.

Conversely, switching between offers under `subs_variant` (`12months-eft` -> `6months-eft`, etc.) performs a same-SKU subscription upgrade designed to stack.
Play's behavior can still vary by account/region, so always verify the expiration date after every step.

### 6-2. `micros=0` Does Not Prove Eligibility

`micros=0` only reports an initial phase price of 0.
It does not guarantee free-trial eligibility or disclose post-trial renewal prices.
Always confirm actual amounts and dates on Play's checkout sheet. This warning is also printed with every plan.

### 6-3. Cancellation Order

Stacking requires an **active subscription (auto-renew ON)**.
If you cancel first, subsequent purchases may become end-of-period conversions without applying a trial or may fail to stack. **Do not cancel before running.**

### 6-4. Device Load

Repeatedly injecting Frida, launching Play billing sheets, and copying `library.db` in rapid succession can trigger transient Play errors or slow down the device.
Check the screen after each step, and pause if errors occur consecutively.
`--wait-seconds` gives the device time to finish updating state during non-interactive runs.

### 6-5. Duplicate Purchases

Do not execute the same offer twice in a row. Because failed steps skip ahead to the next offer, another checkout sheet may appear right afterward -- check Play's "Next payment" date yourself before confirming.

### 6-6. Account Mismatches

Passing `--account` compares against the `account` column in `library.db` ownership rows and warns on mismatch.
The existing UI-tapping helper (`current_play_account()`) is deliberately unused to avoid driving UI outside payment flows, which means accounts with zero ownership rows (`fresh`) cannot be cross-checked automatically and trigger a warning instead. Visually verify the active account in Google Play.

If ownership rows span multiple accounts, `run` refuses to proceed unless `--account` is specified, preventing false extension detections across mixed accounts (`plan` still prints without blocking). Specify the target explicitly with `--account you@example.com`.

## 7. Implementation Split

| File | Role |
|---|---|
| `scripts/combo_core.py` | State detection, plan construction, formatting (no frida/adb dependency) |
| `scripts/combo.py` | CLI, `library.db` reader, offer query wrapper, checkout sheet runner |
| `scripts/sku_scanner.py` | Reused for `list_only` offer queries |
| `tests/test_combo_*.py` | Unit tests requiring no device or frida |

## 8. Related Docs

- [SETUP.md](SETUP.md) -- Environment setup and execution order
- [SKU-SCANNER.md](SKU-SCANNER.md) -- Read-only offer scanner
- [RESULTS.md](RESULTS.md) -- Measured run logs (expiration tracking)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) -- Error-specific remedies
