# iOS Google One / AI Pro Offer Experiments (2026-11-25)

Separate from the Android tooling (`scripts/ultra0-17.py`, etc.), this note documents experiments run against the iOS Google app, separating confirmed facts from unverified claims.

## Conclusions

- The iOS Google app fetches its SKU catalog inside HTML from `one.google.com/ai` (~1.2 MB); 11 entries were captured live.
- None of those 11 catalog entries are **free trials (`_eft`)** -- every promotional SKU in the catalog is `_ip_` (percentage discount).
- However, individual accounts can still receive an Ultra 1-month free trial directly, and one was successfully claimed during testing.
- Whether SKU string rewriting (the `g1-acq` technique) can actually *create* a free trial offer remains **unverified**; the successful claim during this test occurred without any rewrite firing.

## Timeline of What Was Tested

1. Started `mitmproxy` over Tailscale and decrypted iPhone HTTPS traffic. Google hosts had no certificate pinning and decrypted cleanly.
2. Observed `one.google.com/ai` in passive mode and extracted 11 SKU entries.
3. Implemented and enabled the same rewrite mapping used by `g1-acq` v7.6.0.
4. Attempting a purchase failed because Apple payment endpoints (`bag.itunes.apple.com`, `gateway.icloud.com`) enforce certificate pinning and reject MITM, breaking checkout through the proxy.
5. Excluded Apple domains from MITM (`--ignore-hosts`).
6. Checkout succeeded and claimed an **Ultra 1-month free trial**.

## Key Isolation Finding

**Zero rewrites actually executed during the successful claim.**

- Rewrites only trigger when `one.google.com/ai` is freshly fetched over the network.
- After app restart, `/ai` was served from WebView cache without hitting the network.
- `REWRITE` entries in the proxy log: `0`.

Therefore, the Ultra 1-month free trial was **already offered to that account server-side** and simply went through once Apple payment domains were excluded from MITM. This does not prove that `g1-acq` rewriting works.

## SKUs Observed Live on iOS

| SKU | Billing Cycle | Description |
|---|---|---|
| `g1.ios.ai.2tb.m.ip_1mo_90p.acq` | Monthly | 90% off first month |
| `g1.ios.ai.2tb.a.ip_1yr_10p.acq` | Annual | 10% off 1 year |
| `g1.ios.helium.m.1month_ip_90p.acq` | Monthly | 90% off first month |
| `g1.ios.helium.a.ip_1yr_10p.acq` | Annual | 10% off 1 year |
| `g1.ios.2tb.m.3month_ip_75p` | Monthly | 75% off for 3 months |
| `g1.ios.100gb.m.3month_ip_75p` | Monthly | 75% off for 3 months |
| `g1.ios.2tb.a.12month_ip_15p` | Annual | 15% off for 12 months |
| `g1.ios.100gb.a.12month_ip_15p` | Annual | 15% off for 12 months |
| `g1.ios.20tb.m.v2` | Monthly | Ultra 20TB entry SKU |
| `g1.ios.30tb.ai.pro.m` | Monthly | Ultra 30TB entry SKU |
| `com.google.one.generic` | -- | Generic SKU |

Naming pattern: `g1.ios.<tier>.<m|a>.<duration>_ip_<discount>p[.acq]`.

- `m` / `a`: monthly / annual
- `ip_90p`: 90% off (`15p` = 15% off)
- `.acq`: acquisition promo marker
- `helium`, `ai.2tb`, and `100gb` are tiers not present in the Android scanner's candidate list

## Differences from Android

| | iOS | Android |
|---|---|---|
| Prefix | `g1.ios.` | `g1.` |
| Tier word order | `ai.2tb` (tier name first) | `2tb.ai` (capacity first) |
| Free trials | Absent from catalog (account-granted only) | `_eft` exists (e.g., `g1.2tb.ai.18months_eft`) |

iOS product IDs do not map 1:1 to Android SKU IDs; similar names refer to distinct StoreKit vs. Play Billing products.

## Claims in `g1-acq` v7.6.0

Upstream repo: <https://github.com/crhywun/g1-acq>

Module description (translated):

```text
v7.6.0: Supports Ultra 1-month free trial (20TB: g1.20tb.1month_eft.acq,
        30TB: g1.ai.ultra.1month_eft.acq), with each tier independently falling back to 3-month 90% off.
```

| Source | Rewritten Target |
|---|---|
| IDs starting with `g1.ios.2tb.` | `g1.2tb.ai.12months_ip_90p` |
| `g1.ios.20tb.m.v2` | `g1.20tb.1month_eft.acq` |
| `g1.ios.30tb.ai.pro.m` | `g1.ai.ultra.1month_eft.acq` |

The same README notes the caveat that whether a trial appears on the current account depends on App Store history.

### Open Issues Upstream

- issue #2 (open): Rewrite executes, but Apple checkout fails with `"Unable to Complete Purchase"`. Disabling the module allows normal purchase to succeed.
- issue #1 (open): Empty report; the Google app continues showing full price.

No verified report confirms a successful purchase completed via SKU rewriting.

## Reproduction Setup

Stored outside this repository at `~/Documents/repos/g1-mitm-observe/`:

- `addon.py`: `mitmproxy` addon for observation and optional SKU rewriting
- `g1_observe.py`: Pure logic for SKU extraction, rewriting, and aggregation
- `report.py`: CLI report summarizer
- `docs/ios-sku-naming.md`: Naming convention reference
- 47 unit tests (`python3 -m unittest discover -s tests`)

Launch example:

```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 -s addon.py \
  --set g1_report=./g1-observe.json \
  --set block_global=false \
  --set g1_hosts='*.google.com,*.googleapis.com' \
  --ignore-hosts '^(.*\.)?apple\.com:443|^(.*\.)?icloud\.com:443'
```

Without `--ignore-hosts`, Apple checkout fails -- that exclusion was the critical fix.

To test rewriting, add `--set g1_rewrite=true` along with `g1_map` / `g1_prefix`. Because `/ai` will not be rewritten unless fetched fresh from the network, WebView cache must be cleared beforehand (untested).

## Untested Items & Warnings

- Whether rewriting can actually *produce* an Ultra free trial remains unverified; the claim during testing occurred without rewriting.
- `g1.20tb.1month_eft.acq` does not exist in the observed iOS catalog; rewriting to it likely requests a nonexistent product.
- iOS trials appear to be granted per account, so results are not guaranteed for every account.
- Free trials auto-renew upon expiration -- check your cancellation deadline.
- While the proxy is active, all device traffic routes through it; shut it down when finished.
