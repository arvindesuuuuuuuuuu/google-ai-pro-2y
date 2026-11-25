# Security Policy (SECURITY)

This document summarizes what this repository treats as sensitive data, the safeguards enforced by the code, and operational practices left to the user.

## 1. Sensitive Data Handled

| Data | Where It Lives | How This Repository Handles It |
|---|---|---|
| `purchaseToken` | Play `library.db` (`ownership` table) | Read only during stacking; never written to logs or reports |
| `offerToken` | In-device billing flow | Kept strictly on-device; never saved on the host |
| Play account (email) | `account` column in `library.db` | Used solely for `--account` matching; excluded from reports |
| `library.db` | `/data/data/com.android.vending/databases/library.db` | Copied temporarily to read, then deleted from both device and host |

## 2. Design That Avoids Displaying or Saving Tokens

- The scanner (`scripts/sku_scanner*.py`) filters incoming events down to a strict 5-field allowlist (`sku` / `offer_id` / `period` / `micros` / `title`) before writing output.
  All other keys are dropped from reports, and `summarize` validates against the same allowlist.
- The combo runner (`scripts/combo*.py`) never logs purchase tokens.
  Any `purchaseToken` needed for stacking is read from `library.db` and passed directly to the Frida agent without writing it to stdout or disk. Both the temporary device copy and host temp file are removed in a `finally` block.
- Both workflows communicate with the device exclusively over `adb` and Frida. Nothing is sent to external servers.

## 3. Generated Artifacts Are Not Committed

The following paths are listed in `.gitignore` and excluded from Git:

- `ultra0-report/` stores scanner reports (default output directory) and `ultra0-17.py` execution logs.
- `.yomiyasu/` and `*.final.md` store working files and final drafts from proofreading tools.

If you save a report to a custom path outside `.gitignore`, check `git status` before sharing to ensure nothing sensitive is included.

## 4. Pre-Execution Checklist

- Use your rooted device only on a trusted network. Do not run on public Wi-Fi or shared machines.
- Verify the `adb` target before running commands. Run `adb devices` to confirm only your intended device is connected.
- Do not share a rooted device containing your Play account and purchase history.
- Before running, open Google Play > "Payments & subscriptions" and visually confirm the active signed-in account.
- Before lending or selling a device, remove your Play account and unroot the device.

## 5. Reporting Vulnerabilities

If you find a vulnerability or a code path that could leak tokens or account data, please report it via GitHub Issues.

- Do not include real secrets. Replace real email addresses, purchase tokens, device serial numbers, and account names with placeholders such as `you@example.com`.
- Provide reproduction steps with values redacted, showing the exact command run and the observed output format.
- Do not publish exploitable details until a fix is available.

## 6. Known Operational Notes

- Do not publish generated reports verbatim without checking filenames. While scanner reports use an allowlist, avoid putting account names in output paths or filenames.
- Mask screenshots before sharing. Play screens display account names, email addresses, purchase dates, and device status bars. Crop and redact sensitive areas before posting.
- Screen recordings and device logs capture the same details; inspect them before sharing.
