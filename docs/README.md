# Docs Index

Index of documentation in `docs/`. Open each file for full details.

| File | Description |
|---|---|
| [SETUP.md](SETUP.md) | Environment setup and execution order (PC, device, and frida preparation) |
| [SKU-SCANNER.md](SKU-SCANNER.md) | Read-only SKU scanner usage and output interpretation |
| [COMBO.md](COMBO.md) | Combo planner/runner usage, state logic, and caveats |
| [ANDROID16-NOTES.md](ANDROID16-NOTES.md) | Technical notes on fixes required for Android 16 / frida 17 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Observed errors and symptom-based remedies |
| [RESULTS.md](RESULTS.md) | Measured run logs (offers applied and expiration changes) |
| [IOS-EXPERIMENTS.md](IOS-EXPERIMENTS.md) | iOS experiment notes (separating observations, rewrites, confirmed facts, and untested items) |

## Reading Order

If this is your first time using the toolkit, follow this order:

1. Start with [SETUP.md](SETUP.md) to build your environment. Nothing else works until setup passes.
2. Inspect current state. Read [SKU-SCANNER.md](SKU-SCANNER.md) to check available offers, then run `combo plan --device` per [COMBO.md](COMBO.md) to inspect your account state and recommended plan. Both steps are read-only and trigger no charges.
3. Execute. Once satisfied with the plan, run `combo run` from [COMBO.md](COMBO.md) or `step1` through `step4`. Always verify pricing and duration directly on your device screen.

Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) when hitting issues, and [ANDROID16-NOTES.md](ANDROID16-NOTES.md) for why the Android 16 / frida 17 changes were necessary.

Be sure to read [../DISCLAIMER.md](../DISCLAIMER.md) and [../SECURITY.md](../SECURITY.md) before running anything.
