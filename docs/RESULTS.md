# Measured Run Log (2026-11-19)

> Account names and device identifiers are redacted (`example@gmail.com` is an example placeholder).

Environment:

| | |
|---|---|
| Host | Arch Linux / x86_64 |
| Device | Pixel 7a (`lynx`) / Android 16 / KernelSU |
| Frida | client 17.22.2 / server 17.22.2 |
| Billing path | Google One app + `module_521.apk` (BillingClient 5.x) |
| Account | `example@gmail.com` (placeholder; Japan region / JPY billing) |

## Initial State

- Play subscription: **Student plan `g1.play.2tb.ai.student`** active (¥720/mo, next payment 2026-12-18)
- `--list --plan pro12` returned 4 free trial offers for Pro 5TB:

```
offer 12months-eft        -> 0 micros, P1Y
offer 6months-eft         -> 0 micros, P6M
offer 6months-eft-2       -> 0 micros, P6M
offer 6months-eft-ss-2026 -> 0 micros, P6M
offer null                -> 2,900,000,000 micros, P1M   (standard price ¥2,900)
```

## Execution Results

| Timestamp | Action | Offer | `subs_valid_until_time` |
|---|---|---|---|
| 2026-11-19 02:34 | step1 (`--plan pro12`) | `12months-eft` | 2027-11-19 |
| 2026-11-19 02:38 | step2 (`--stack --plan pro6`) | `6months-eft` | 2028-05-19 (546 days) |
| 2026-11-19 02:41 | step3 (`--stack --plan pro6b`) | `6months-eft-2` | **2028-11-19 (730 days)** |
| 2026-11-19 02:45+ | step4 (`--stack --plan pro6c`) | `6months-eft-ss-2026` | Unchanged (immediate Play error) |

Each purchase rotates the `purchaseToken` in Play's `library.db` (`ownership` table).

> Because `purchaseToken` is sensitive credential data tied to the purchase, it is omitted from this document.
> Inspect Play's `library.db` locally if needed and handle it with care.

## Final State

`library.db`:

```
subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.subs_variant
  valid_until: 2028-11-19
  days_remaining: 730
```

Google Play Subscriptions screen:

```
Active
Google One
Google AI Pro (5 TB)
Next payment: ¥2,900 (2028/11/19)
```

Confirmed persistent across device reboot (stored server-side).

## 2026-11-25 Follow-Up: Probing Ultra Tiers and Discount Offers

Candidate SKUs were expanded from 132 to 287 using SKU names referenced by the iOS project (`crhywun/g1-acq`) and rescanned live:

| Metric | Value |
|---|---|
| Candidates | 7 tiers x 41 (1 variant + 8 periods x 5 suffixes) = 287 |
| Tiers | `2tb.ai`, `play.2tb.ai`, `5tb.ai`, `ai.pro`, `20tb`, `play.20tb`, `ai.ultra` |
| Suffixes | `_eft`, `_ip_90p`, `_ip_90p.acq`, `_ip_95p.acq`, `_ip_75p.acq` |
| Duration | 36.6s (batch size 20, timeout 20s, 0 timeouts) |

Only the 8 `g1.2tb.ai` SKUs appeared; Ultra tiers (`20tb` / `play.20tb` / `ai.ultra`) and discount suffixes (`_ip_90p` / `_ip_95p.acq` / `_ip_75p.acq`) returned 0 results.

Two control checks confirmed the query path itself was healthy:

- Positive control: in the same run, querying `g1.2tb.ai.18months_eft` returned `eft-18months` (`P1Y6M`, `0 micros`).
- Direct queries: querying bare `g1.30tb`, `g1.20tb`, and `g1.ai.ultra` directly also returned 0 results.

On this Japan-region account, Play only returns Pro 5TB (`g1.2tb.ai`); Ultra tiers and discount offers are absent.

## 2026-11-25 Addendum: Testing `g1-acq` v7.6.0 Ultra 1-Month Free Trial SKUs

On 2026-11-24, `g1-acq` v7.6.0 switched Ultra targets from 3-month 90%-off to **1-month free trials** (`g1.20tb.1month_eft.acq` and `g1.ai.ultra.1month_eft.acq`). Adding `_eft.acq` expanded candidates to 343 and was re-tested on-device:

| Queried SKU | Result |
|---|---|
| `g1.20tb.1month_eft.acq` | 0 hits |
| `g1.ai.ultra.1month_eft.acq` | 0 hits |
| `g1.20tb.1month_eft` | 0 hits |
| `g1.ai.ultra.1month_eft` | 0 hits |
| `g1.20tb.3months_ip_90p.acq` | 0 hits |
| `g1.ai.ultra.3months_ip_90p.acq` | 0 hits |

Across the full 343-SKU scan (46s, 0 timeouts), only the 8 `g1.2tb.ai` SKUs returned offers, while `g1.2tb.ai.18months_eft` returned `eft-18months` in the same run.

Play on this Japan-region account does not expose Ultra SKUs. Upstream in `g1-acq`, issue #2 (Apple checkout `"Unable to Complete Purchase"`) and issue #1 (Google app showing full price) remain open with no confirmed successful purchase.

## Side Effects Observed

- Repeated Frida injections caused Pixel 7a to enter a boot loop a few times (power-button hold recovered; subscription unaffected).
- USB dropped periodically through hubs; Wi-Fi ADB stabilized the session.
- Only the 4th stack (`-ss-2026`) failed without a checkout sheet, showing a Play "Something went wrong" error dialog instead.

## Screenshots (Author's Posts)

- <https://x.com/lvz3s/status/2123112734460170354>
- <https://x.com/lvz3s/status/2123109098443121001>

![Google Play Subscriptions Screen](../images/result-subscription.jpg)

![Result Summary](../images/result-summary.jpg)
