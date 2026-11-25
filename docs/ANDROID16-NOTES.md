# Fixes Required for Android 16 / frida 17

The original `ultra0.py` was written for **Android 12/13 emulators + root + frida 16.5.9**.
On a physical Android 16 device (Pixel 7a, KernelSU, frida 17.22.2), five issues blocked execution and were worked around in `ultra0-17.py` and `agent/`. The original `scripts/ultra0.py` remains untouched.

---

## 1. frida 16 Is Unusable on Android 16

Observed failures:

- Starting `frida-server` 16.5.9 / 16.7.19 fails during `device.spawn()` with `TimedOutError: unexpectedly timed out while waiting for signal from process`.
- `device.enumerate_processes()` fails with `ProcessNotFoundError: unable to find process with name 'system_server'`.
- Devices with `magisk-frida` installed run 17.22.2 in the background, causing major-version mismatches with a frida 16 client.

Upstream changes in frida 17 (per official release notes):

- `frida-java-bridge` and `frida-objc-bridge` were split out of GumJS and **are no longer bundled by default**, causing `ReferenceError: 'Java' is not defined` if raw JS is passed to `create_script()`.
- CLI tools (`frida`, `frida-trace`) bundle the bridge automatically, whereas the Python API requires bundling it yourself.

### Fix: Build an IIFE Agent Bundling `frida-java-bridge`

```js
// agent/agent.js (auto-generated)
import Java from 'frida-java-bridge';
globalThis.Java = Java;
// followed by the original FRIDA_JS code
```

```python
# scripts/build_agent.py
bundle = frida.Compiler().build("agent.js", project_root=AGENT, bundle_format="iife")
```

**`bundle_format="iife"` is mandatory.** The default `esm` output uses Frida's custom package format (with `📦 ... ✄` headers), which triggers `frida.InvalidArgumentError: malformed package` when passed as a raw script string from Python.

---

## 2. JNI Calls Returning `Object[]` Hang on Android 16

Symptom: Script injection succeeds, but `Java.registerClass()` never returns (hangs indefinitely).

Isolated test calls inside the same process:

| Call | Return Type | Result |
|---|---|---|
| `Class.getName()` | `String` | OK |
| `Class.isInterface()` | `boolean` | OK |
| `Class.getSuperclass()` | `Class` | OK |
| `String.toCharArray()` | `char[]` (primitive) | OK |
| **`Class.getInterfaces()`** | **`Class[]`** | **Hangs** |
| **`ArrayList.toArray()`** | **`Object[]`** | **Hangs** |
| **`Class.getDeclaredMethods()`** | **`Method[]`** | **Hangs** |

Only **JNI calls returning object reference arrays** hang (primitive arrays work normally). `frida-java-bridge` uses `GetArrayLength` / `GetObjectArrayElement` (JNIEnv table offsets 171 / 173) to convert arrays into JS arrays, which appears incompatible with Android 16 ART.

### Fix: Skip Base Interface Expansion in `Java.registerClass`

Before generating a proxy class, `registerClass` normally walks parent interfaces via `Class.getInterfaces()`. Since the Billing listeners (`PurchasesUpdatedListener`, `BillingClientStateListener`, `ProductDetailsResponseListener`, `PurchasesResponseListener`) declare no super-interfaces, expanding them is unnecessary.

`agent/injected.js` passes `skipInterfaceExpansion: true`, and the bridge (`agent/node_modules/frida-java-bridge/lib/class-factory.js`) skips the traversal:

```js
      const allInterfaces = interfaces.slice();
      if (spec.skipInterfaceExpansion !== true) {
        interfaces.forEach(iface => { ... });   // existing behavior only when flag is unset
      }
```

`scripts/apply_bridge_patch.py` applies this patch idempotently (`frida-java-bridge` version is pinned to `7.1.0` in `agent/package.json`).

---

## 3. Android 14+ Rejects Writable Dex Files

```
java.lang.SecurityException: Writable dex file '/data/local/tmp/module_521.apk' is not allowed.
```

Loading the billing module APK via `DexClassLoader` fails if the file is writable. Because `adb push` leaves permissions at `0666`, it must be locked down to `444`.

`ultra0-17.py` automatically runs `chmod 444` after pushing inside `ensure_module_apk()`.

---

## 4. Non-English Checkout UI Strings Needed Locale Expansion

The original `confirm_zero()` only checked Chinese and English strings (`Subscribe`, plus Chinese subscribe/free-trial strings). Under a Japanese locale, Google Play displays:

- Action buttons: localized Japanese labels for "Subscribe" / "Sign up"
- Currency: `¥0` / `JP¥0` (the original regex only matched `$`, `€`, `£`, etc., missing `¥`)
- Duration text: Japanese free-trial phrasing (e.g., "12-month free trial")

`ultra0-17.py` expands matching to `ja/en/zh` needles plus `¥` currency detection, adds `tap_button()` to prioritize `Button` widgets, and logs screen text on detection failures.

---

## 5. Other Practical Fixes

- **App startup wait**: Replaced fixed 7-second sleep with 20-second `pidof` polling (prevents `app_not_started`).
- **Message payload guard**: Guarded `on_msg` against raw string payloads (such as bridge debug `send("reg:...")` events) that previously threw `AttributeError: 'str' object has no attribute 'get'`.
- **frida-server version alignment**: Matched server to client `17.22.2`; added `scripts/fix_frida.sh` to resolve conflicts with resident root modules.

---

## Device-Side Side Effects Observed

- On a physical KernelSU + LSPosed + Android 16 device, repeated Frida injections occasionally left the phone **stuck on the boot animation** (recovered via long-press power reboot; subscription state stayed intact).
- USB connections intermittently dropped through hubs; pairing with Wi-Fi ADB (`adb tcpip 5555`) improved reliability.

For stability, stop `frida-server` (`pkill -x frida-server`) and disable `magisk-frida` once you are done.
