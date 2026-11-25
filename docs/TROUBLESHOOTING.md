# Troubleshooting

Summary of observed errors and symptom-based fixes.

## Environment & Connection

### No device detected / `adb devices` is empty

- Confirm you accepted the USB debugging authorization prompt on the device
- Make sure the USB cable supports data (not charge-only)
- USB hubs can be unstable -> plug directly into the host or use Wi-Fi ADB:
  ```bash
  adb tcpip 5555
  adb connect <device-ip>:5555
  ```

### Device does not have root privileges

- Requires KernelSU, Magisk, or APatch
- Check that `adb shell id` returns `uid=0`
- On emulators, enable "ROOT permission" and restart, or run `adb root`

### Device stuck on boot animation (physical devices)

Observed occasionally after long or repeated Frida sessions:

1. Force reboot by holding the Power button for 10 seconds
2. If still looping: hold Power + Volume Down to enter fastboot -> Start
3. Boot into Safe Mode (hold Volume Down at the Google logo) and disable modules to recover

Subscription state is stored server-side, so claimed extensions are not lost.

## Frida

### `unable to communicate with remote frida-server; please ensure that major versions match`

Client and device `frida-server` have a major-version mismatch.

```bash
./scripts/run.sh fix-frida
```

This moves `/data/local/tmp/frida-server` aside so the next run downloads `17.22.2` automatically.
If a `magisk-frida` module is resident, replace its `bin/frida-server` with 17.x or disable the module.

### `malformed package`

The bundle passed to `Script.create_script()` was built in Frida's `esm` format.
`scripts/build_agent.py` must use `bundle_format="iife"`; rebuild the agent.

### `ReferenceError: 'Java' is not defined`

frida 17 does not bundle the Java bridge. Use `agent/agent.iife.js` (with bridge bundled) rather than injecting `agent/agent.js` directly.

### Nothing happens after injection (stalls right after `boot_start`)

Android 16 `Java.registerClass` hang. Ensure every `Java.registerClass` call in `agent/injected.js` includes `skipInterfaceExpansion: true`, then rebuild the agent.

### `Writable dex file '/data/local/tmp/module_521.apk' is not allowed.`

```bash
adb shell chmod 444 /data/local/tmp/module_521.apk
```

`ultra0-17.py` runs this automatically after pushing; run manually if you pushed the APK by hand.

## Billing & Offers

### `app_not_started`

Google One did not launch within 7 seconds. `ultra0-17.py` fixes this by polling for 20 seconds.
If it still occurs, a dialog or heavy load may be blocking launch -- send `adb shell input keyevent BACK` to dismiss foreground dialogs and retry.

### Checkout screen could not be detected

- Unsupported device locale -> `ultra0-17.py` handles `ja/en/zh`. For other languages, add strings to `SUB_NEEDLES`, `INELIGIBLE_NEEDLES`, and `PRICE_PAT`.
- `uiautomator dump` can return empty mid-animation -> retry after a few seconds.
- If already subscribed to that exact offer, the sheet will not open.

### Play: This account is not eligible for a free trial

The account has already used that trial or is ineligible.
Run `--list` first to see which offers Play returns for the account.

### Error dialog on the 4th stack (`6months-eft-ss-2026`)

Play immediately rejected this upgrade offer on the test account.
If steps 1-3 succeeded, 24 months are already secured; step 4 is optional backup only.

### Date does not extend after purchase

- Verify the subscription is **Active (auto-renew ON)**; canceled subscriptions often fail to apply trials.
- If checkout displays "Starts on [current expiration date]", Play treated it as a scheduled switch rather than a subscription upgrade -> re-run with `--stack`.

## Verifying Results

```bash
# Play UI check
adb shell "am start -a android.intent.action.VIEW -d 'https://play.google.com/store/account/subscriptions'"
adb shell uiautomator dump /sdcard/s.xml && adb shell cat /sdcard/s.xml | grep -o 'text="[^"]*"'

# Internal Play DB (requires root)
adb shell "cp /data/data/com.android.vending/databases/library.db /data/local/tmp/lib.db; chmod 666 /data/local/tmp/lib.db"
adb pull /data/local/tmp/lib.db /tmp/lib.db
sqlite3 /tmp/lib.db "select doc_id, datetime(subs_valid_until_time/1000,'unixepoch') from ownership where doc_id like 'subs:%'"
```
