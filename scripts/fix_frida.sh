#!/usr/bin/env bash
# Resolve version mismatch between local frida client (17.x) and device frida-server.
# Backs up /data/local/tmp/frida-server on the device so 17.22.2 is auto-deployed on next run.
set -euo pipefail
ADB_BIN="${ADB_PATH:-$(command -v adb || echo "$(cd "$(dirname "$0")/.." && pwd)/tools/platform-tools/adb")}"

echo "▶ Current frida-server"
"$ADB_BIN" shell 'ls -l /data/local/tmp/frida-server 2>/dev/null || echo none'
"$ADB_BIN" shell 'pidof frida-server >/dev/null && /proc/$(pidof frida-server)/exe --version 2>/dev/null || true'

echo "▶ Stopping and backing up"
"$ADB_BIN" shell 'pkill -x frida-server 2>/dev/null || true'
"$ADB_BIN" shell 'mv /data/local/tmp/frida-server /data/local/tmp/frida-server.bak.$(date +%s) 2>/dev/null || true'
"$ADB_BIN" shell 'ls /data/local/tmp/ | grep frida || echo "(no frida-server = auto-download on next run)"'

cat <<'EOS'

If a KernelSU/Magisk module (such as magisk-frida) launches a different
version at boot, replace that module's bin/ binary with 17.22.2 or
disable the module (/data/adb/modules/magisk-frida).
EOS
