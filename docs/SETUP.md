# Setup

## 1. Host (PC) Side

| Requirement | Purpose | Verification |
|---|---|---|
| Python 3.9+ | Run scripts | `python3 -V` |
| adb (platform-tools) | Device communication | `adb version` |
| node / npm (optional) | Build Frida agent | `node -v` |
| uv (optional) | Faster venv creation | `uv --version` |

If `adb` is missing, `./scripts/setup.sh` downloads `tools/platform-tools/` automatically (Linux build; on macOS, download from <https://developer.android.com/tools/releases/platform-tools>).

## 2. Device Side

### 2-1. Prerequisites

```bash
adb devices                            # Device appears
adb shell id                           # Must report uid=0(root)
adb shell getprop ro.product.cpu.abi   # arm64-v8a / x86_64
adb shell getprop ro.build.version.release
```

Any root solution (**KernelSU / Magisk / APatch**) works.
If `adb shell id` returns `shell`, try:

```bash
adb root                        # Emulators
adb shell su -c id              # Physical devices (KernelSU often grants adb shell root by default)
```

### 2-2. Google Play State

- Signed into Google Play with the **target account**
- **Payment method** (credit card, etc.) registered on the account
- Google One (`com.google.android.apps.subscriptions.red`) installed
- Subscription status:
  - A fresh 12-month trial works even with no active subscription
  - **Stacking (`step2` and onward) requires an active subscription (auto-renew ON)**
  - If already canceled, a new purchase may convert at period end instead of granting a trial

### 2-3. frida-server

Path B (frida 17) requires a `frida-server` matching the **same major version** as the `17.22.2` client.

- If `/data/local/tmp/frida-server` is absent on the device, `ultra0-17.py` downloads and starts `17.22.2` automatically.
- If a KernelSU/Magisk module (such as `magisk-frida`) launches a different version on boot, either **replace its `bin/frida-server` with 17.x** or disable the module.
- If a mismatch occurs:

```bash
./scripts/run.sh fix-frida
```

## 3. Connection (USB / Wi-Fi)

Where USB connections are unstable, Wi-Fi ADB can be used alongside or instead of USB:

```bash
adb tcpip 5555
adb connect <device-ip>:5555     # Device: Settings > Network > IP address
adb devices                      # Two entries are fine (first one is used)
```

`ultra0-17.py` uses the first device listed by `adb`. To target a specific device:

```bash
python scripts/ultra0-17.py --check --serial <serial>
```

## 4. Path A: Android 12/13 Emulator (MuMu, etc. / frida 16.5.9)

Windows-focused, following the original zip package workflow:

```bat
pip install frida==16.5.9
python scripts\setup_env_frida16.py
windows\frida16\0_check_env.bat
windows\frida16\1_list_offers.bat
```

Enable "ROOT permission" and ADB debugging in the emulator settings.
MuMu uses ADB ports such as `16384` or `7555`; `setup_env_frida16.py` runs `adb connect` automatically.

## 5. Path B: Rooted Android 14+ Physical Device (Added in This Repo)

```bash
./scripts/setup.sh
```

What this does:

1. Creates `.venv` and installs `frida==17.22.2`
2. Uses the prebuilt `agent/agent.iife.js` (verified working bundle)
3. Downloads `platform-tools` into `tools/` if `adb` is not installed
4. Verifies device connection and root access

Only rebuild if you modify `agent/injected.js`:

```bash
./scripts/setup.sh --rebuild   # npm install -> patch -> build
```

(Do not change `"type": "commonjs"` in `agent/package.json` -- Frida's compiler relies on it; `esm` output fails with `malformed package`.)

```bash
./scripts/run.sh check    # Environment self-check
./scripts/run.sh list     # List offers (no charges)
```

Setup is good once `check` finishes with a green `✅` pass line.

To inspect SKUs and offers without purchasing, use the SKU scanner (read-only):

```bash
./scripts/run.sh scan generate --tiers 2tb.ai --periods 18months,12months --suffixes _eft --format table
./scripts/run.sh scan query --candidates --batch-size 20 --timeout 8
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-<timestamp>.json
./scripts/run.sh test     # Unit tests
```

To plan purchase order based on account state (`fresh` / `active` / `expired`), use the combo planner. It defaults to dry-run and requires `--device` to query the device:

```bash
./scripts/run.sh combo plan --device                  # Show state check and plan only
./scripts/run.sh combo run --device --execute --yes   # Run step-by-step (user completes payment)
```

`run` opens the billing sheet and waits for Enter -- it never auto-taps "Subscribe".
See [COMBO.md](COMBO.md) for caveats.

See [SKU-SCANNER.md](SKU-SCANNER.md) for full details. Remember that `micros=0` only indicates the initial charge and does not prove eligibility or renewal price.

## 6. Execution Order

```bash
./scripts/run.sh step1    # 12 months free (offer: 12months-eft)
./scripts/run.sh step2    # +6 months (6months-eft, subscription upgrade)
./scripts/run.sh step3    # +6 months (6months-eft-2)
./scripts/run.sh step4    # Backup +6 months (6months-eft-ss-2026; may return an error)
```

After each step, verify in Google Play's Subscriptions screen (`https://play.google.com/store/account/subscriptions`) that **Next payment** has shifted forward before proceeding to the next step.

| After Step | Expected Expiration |
|---|---|
| step1 | +12 months |
| step2 | +18 months |
| step3 | +24 months |
| step4 | +30 months (if accepted) |

## 7. Before Running Combo

Stacking via `combo` also requires an **active subscription (auto-renew ON)**.
Do not cancel early -- canceling beforehand can cause subsequent purchases to convert at period end without applying a trial.

```bash
./scripts/run.sh combo plan --device    # Inspect state and judgment first
```

Even when `plan` reports that stacking is possible, note that `18months_eft` is a different SKU from `subs_variant`.
Purchasing it while an active subscription exists may replace the subscription instead of stacking, so `combo` never generates a `new_subscription` step when state is `active` (see "Risks and Caveats" in [COMBO.md](COMBO.md)).

## 8. Cleanup / Post-Run

```bash
# Cancel before the trial ends to avoid recurring charges:
# Google Play Store > Payments & subscriptions > Subscriptions > Google AI Pro > Cancel subscription
```

Access remains active until the end of the prepaid/trial period even after canceling.
When Frida is no longer needed, stopping the daemon improves device stability:

```bash
adb shell su -c "pkill -x frida-server"
# If using the magisk-frida module:
adb shell su -c "touch /data/adb/modules/magisk-frida/disable"   # Disable on next boot
```
