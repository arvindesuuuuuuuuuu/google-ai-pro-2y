# google-ai-pro-2y

A toolkit for retrieving **Google AI Pro (5TB) free trial offers via Google Play Billing** and stacking 6-month offers to extend validity up to **2 years (or 30 months with the backup offer)**.

Because Google Play's `BillingClient` only returns free trial offers **through in-app billing**, the scripts inject Frida into the Google One process, load Play's billing module, enumerate offers, and open the checkout sheet only when an offer is priced at 0.

- The original script (`scripts/ultra0.py`) targets **Android 12/13 emulators + frida 16.5.9 + Windows**.
- This repository adds `scripts/ultra0-17.py` and its bundled Frida agent (`agent/`) so the workflow also runs on **rooted Android 14/15/16 physical devices + frida 17**.

## What This Is

A toolkit for inspecting and stacking Google AI Pro (5TB) free trials through Play Billing on rooted Android devices, structured in three layers:

1. Read-only SKU scanner (`scripts/sku_scanner*.py` / `./scripts/run.sh scan ...`).
   Queries available SKUs and offers on the device without subscribing or triggering charges.
2. Combo planner and runner (`scripts/combo*.py` / `./scripts/run.sh combo plan|run`).
   Inspects current account state and builds a stacking plan. Defaults to dry-run; executing requires `--execute --yes`. Never auto-taps "Subscribe" -- payment confirmation stays manual.
3. Legacy fixed step flow (`scripts/ultra0-17.py` + `./scripts/run.sh step1` through `step4`).
   Assumes an active subscription and stacks sequentially: 12 months -> +6 months -> +6 months -> backup.

## Disclaimer & Security

- This repository is unofficial. See [DISCLAIMER.md](DISCLAIMER.md) before running anything to understand potential real charges, what `micros=0` means, and risk boundaries.
- See [SECURITY.md](SECURITY.md) for how purchase tokens and account info are handled, pre-run checks, and vulnerability reporting.
- See [NOTICE.md](NOTICE.md) for licensing and provenance. Upstream code carries no license grant; newly authored code in this repository is dedicated under CC0 1.0.

## Measured Results (2026-11-19 / Pixel 7a / Android 16 / KernelSU)

| Step | Applied Offer | Expiration Date |
|---|---|---|
| Initial | Student plan `g1.play.2tb.ai.student` (¥720/mo) | 2026-12-18 |
| step1 | `12months-eft` (12 months free) | 2027-11-19 |
| step2 | `6months-eft` (+6 months) | 2028-05-19 (546 days) |
| step3 | `6months-eft-2` (+6 months) | **2028-11-19 (730 days = 2 years)** |
| step4 | `6months-eft-ss-2026` (backup +6 months) | Immediate Play error (ineligible on this account) |

Confirmed on Google Play's Subscriptions screen: `Active / Google AI Pro (5 TB) / Next payment: ¥2,900 (2028/11/19)`.

## Proof Screenshots (Author's Posts)

Captured from the same run reaching 24 months (posted by the author with account name and email redacted):

- <https://x.com/lvz3s/status/2123112734460170354>
- <https://x.com/lvz3s/status/2123109098443121001>

![Google Play Subscriptions Screen (Next Payment)](docs/images/result-subscription.jpg)

![Result Summary](docs/images/result-summary.jpg)

![Session Log](docs/images/result-session-log.jpg)

## Two Execution Paths

| | Path A (Legacy / frida 16) | Path B (Added in This Repo / frida 17) |
|---|---|---|
| Device | Android 12/13 emulator (MuMu, etc.) | Rooted Android 14/15/16 physical device |
| Host | Windows (double-click `.bat`) | Linux / macOS / Windows |
| frida | 16.5.9 (built-in Java bridge) | 17.22.2 (Java bridge bundled into agent) |
| Script | `scripts/ultra0.py` | `scripts/ultra0-17.py` |
| Launcher | `windows/frida16/*.bat` | `./scripts/run.sh` or `windows/frida17/*.bat` |

Android 14+ **blocks writable dex files**, **frida 16 hangs on `spawn()`**, and **frida 17 omits the Java bridge by default**. Path B addresses all of these (details: [docs/ANDROID16-NOTES.md](docs/ANDROID16-NOTES.md)).

## Quick Start (Linux / macOS + Android 14+ Device)

Prerequisites: Python 3.9+, node/npm (optional if using the prebuilt bundle), adb, a rooted device with USB debugging enabled, signed into Google Play with a payment method on file.

```bash
./scripts/setup.sh               # venv + frida 17.22.2 + agent build + adb
./scripts/run.sh check           # Self-check device / root / frida / billing module
./scripts/run.sh list            # List offers (no billing, zero risk)

# 1) Read-only SKU / offer inspection (no subscription or charges)
./scripts/run.sh scan generate                 # Generate candidate SKUs offline
./scripts/run.sh scan query --candidates       # Query offers from the device
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-YYYYMMDD-HHMMSS.json

# 2) State-aware combo planner (dry-run by default)
./scripts/run.sh combo plan --device           # Show plan only (no execution)
./scripts/run.sh combo run --device --account you@example.com --execute --yes
                                               # Run step-by-step (user completes payment)

# 3) Unit tests (no device or frida required)
./scripts/run.sh test
```

Use `scan` if you only want to **read** available SKUs and offers without purchasing.
See [docs/SKU-SCANNER.md](docs/SKU-SCANNER.md) for `scan` subcommands and flags (`--candidates`, `--skus`, `--timeout`).
See [docs/COMBO.md](docs/COMBO.md) for `combo` decision logic and caveats.

If `list` outputs lines like the following, prerequisites are satisfied (numbers are examples):

```
offer 12months-eft -> 0 (P1Y)
offer 6months-eft -> 0 (P6M)
offer 6months-eft-2 -> 0 (P6M)
offer 6months-eft-ss-2026 -> 0 (P6M)
offer null -> 2900000000 (P1M)     # Standard price (¥2,900/mo)
```

Then execute the steps in order:

```bash
./scripts/run.sh step1      # 12-month free trial
./scripts/run.sh step2      # +6 months (stacked via subscription upgrade)
./scripts/run.sh step3      # +6 months
./scripts/run.sh step4      # Backup +6 months (may return an error)
```

After each step, verify in Google Play's Subscriptions screen that the **Next payment** date has moved forward before continuing.

### Combo Planner (Account-State-Aware Ordering)

`step1` through `step4` assume an already-active subscription.
For fresh or expired accounts, the optimal starting offer differs; use `combo` to inspect state first:

```bash
./scripts/run.sh combo plan --device                     # Show plan only (no execution)
./scripts/run.sh combo run --device --execute --yes      # Execute step by step
```

- `fresh` (no subscription): Starts with the longest standalone free offer `18months_eft` (+18 months)
- `active` (valid subscription): Checks stacking eligibility and stacks only `subs_variant` free offers
- `expired` (expired subscription): Treats as a new subscription starting from `18months_eft`

Payment confirmation is always performed by a human. The script only opens the billing sheet and waits; it never auto-taps "Subscribe" or calls `confirm_zero()`.
Execution requires both `--execute` and `--yes`, and the device is never touched unless `--device` is passed.
See [docs/COMBO.md](docs/COMBO.md) for full details and risks.

### Windows / MuMu (Path A)

```bat
pip install frida==16.5.9
python scripts\setup_env_frida16.py
windows\frida16\0_check_env.bat
windows\frida16\1_list_offers.bat
windows\frida16\2_Pro_12m_step1.bat
...
```

### Windows / Physical Android 14+ (Path B)

```bat
:: 1. Setup
.venv\Scripts\python.exe -m pip install frida==17.22.2
cd agent && npm install && cd ..
.venv\Scripts\python.exe scripts\apply_bridge_patch.py
.venv\Scripts\python.exe scripts\build_agent.py
:: 2. Run
windows\frida17\0_check_env.bat
```

## Repository Structure

```
scripts/
  setup.sh              Linux/macOS environment setup
  run.sh                Launcher for check / list / scan / test / step1-4
  sku_scanner.py        Read-only SKU scanner CLI
  sku_scanner_core.py   Pure logic for SKU candidate generation and offer formatting
  combo.py              Combo planner CLI (dry-run by default; human payment)
  combo_core.py         Pure logic for state detection and stacking plans
  build_agent.py        Builds agent.iife.js bundle
  apply_bridge_patch.py Android 16 patch for frida-java-bridge
  fix_frida.sh          Fixes frida-server version mismatches
  ultra0.py             Original script (unmodified)
  ultra0-17.py          Android 14+ / frida 17 port
  setup_env_frida16.py  Original zip setup helper (frida 16.5.9)
  query_sku.py          Original zip SKU query helper
agent/
  injected.js           Original FRIDA_JS with registerClass patch applied
  agent.js              Generated entrypoint (bridge import + injected.js)
  agent.iife.js         Prebuilt bundle (committed)
  package.json          Pins frida-java-bridge 7.1.0
windows/
  frida16/              Path A .bat launchers
  frida17/              Path B .bat launchers
docs/
  SETUP.md              Detailed setup guide
  README.md             Documentation index (reading order)
  SKU-SCANNER.md        Read-only SKU scanner guide
  COMBO.md              Combo planner usage and caveats
  ANDROID16-NOTES.md    Technical notes on Android 16 / frida 17 fixes
  TROUBLESHOOTING.md    Error reference and fixes
  RESULTS.md            Measured run logs
tests/
  test_sku_*.py         Scanner unit tests (no device or frida required)
  test_combo_*.py       Combo planner/runner unit tests (no device or frida required)
```

## Tests

```bash
./scripts/run.sh test        # python3 -m unittest discover -s tests -v
```

- Runs purely with Python's standard `unittest` module -- no extra dependencies.
- Requires neither a device nor frida; runs even on hosts without `adb`.
- Contains 118 unit tests; prints `OK` when all pass.
- `tests/` is organized into scanner tests (`test_sku_*.py`) and combo tests (`test_combo_*.py`).

## Common Pitfalls (See docs/TROUBLESHOOTING.md)

| Symptom | Cause | Fix |
|---|---|---|
| `unable to communicate with remote frida-server` | Client/server major version mismatch | `./scripts/run.sh fix-frida` |
| `malformed package` | Agent built in `esm` format | Rebuild with `bundle_format="iife"` |
| `'Java' is not defined` | frida 17 excludes Java bridge | Use bundled `agent/agent.iife.js` |
| No response after injection | `Java.registerClass` hangs on Android 16 | `skipInterfaceExpansion:true` in `injected.js` |
| `Writable dex file ... is not allowed` | Android 14+ dex permission enforcement | `chmod 444` on `module_521.apk` |
| `app_not_started` | Fixed 7-second app startup wait | `ultra0-17.py` polls for 20 seconds |
| Checkout text not captured | Device set to Japanese locale | Added ja/en/zh + `¥` matching (`ultra0-17.py`) |

## Notes

- **Automatic billing begins on the trial expiration date** (in this test run, ¥2,900/mo starting 2028-11-19).
  Cancel in Play's Subscriptions screen beforehand to avoid unwanted charges (access remains until expiration).
- Stacking requires an **active subscription (auto-renew ON)**. Canceling early prevents stacking.
- Repeating the same Play action too quickly can trigger temporary errors; wait briefly and retry.
- On physical KernelSU + LSPosed + Android 16 devices, keeping Frida active for extended periods rarely caused boot loops.
  If this happens, hold the power button to force-reboot (subscription state is stored server-side).
- Use strictly at your own risk.
