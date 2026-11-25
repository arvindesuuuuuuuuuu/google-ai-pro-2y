#!/usr/bin/env bash
# Environment setup (Linux / macOS). On Windows, see the frida16 instructions in docs/SETUP.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FRIDA_VERSION="17.22.2"

log() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
ok()  { printf '   ✅ %s\n' "$*"; }
warn(){ printf '   ⚠️  %s\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

log "Python virtualenv"
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.12 .venv >/dev/null 2>&1 || uv venv .venv
  uv pip install -p .venv/bin/python -q "frida==${FRIDA_VERSION}"
else
  PY=$(command -v python3 || true)
  [ -n "$PY" ] || die "python3 not found (requires 3.9+)"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q "frida==${FRIDA_VERSION}"
fi
.venv/bin/python -c "import frida; print('   frida', frida.__version__)"
ok "frida ${FRIDA_VERSION} (client)"

log "Frida agent"
# A known-working prebuilt bundle is included; use it by default.
# Only rebuild with `./scripts/setup.sh --rebuild` after editing injected.js.
if [ -f agent/agent.iife.js ] && [ "${1:-}" != "--rebuild" ]; then
  ok "Using bundled agent/agent.iife.js (rebuild with ./scripts/setup.sh --rebuild)"
else
  command -v npm >/dev/null 2>&1 || die "--rebuild requires npm"
  ( cd agent && npm install --no-audit --no-fund --silent )
  .venv/bin/python scripts/apply_bridge_patch.py
  .venv/bin/python scripts/build_agent.py
fi
[ -f agent/agent.iife.js ] || die "agent/agent.iife.js not found"

log "adb"
if [ -n "${ADB_PATH:-}" ] || command -v adb >/dev/null 2>&1 || [ -x "$HOME/Android/Sdk/platform-tools/adb" ]; then
  ok "adb: $(command -v adb || echo "${ADB_PATH:-$HOME/Android/Sdk/platform-tools/adb}")"
else
  warn "adb not found; downloading platform-tools into tools/"
  mkdir -p tools
  curl -sSL -o /tmp/platform-tools.zip https://dl.google.com/android/repository/platform-tools-latest-linux.zip
  ( cd tools && rm -rf platform-tools && unzip -q /tmp/platform-tools.zip )
  ok "tools/platform-tools/adb"
fi

log "Device check"
ADB_BIN="${ADB_PATH:-$(command -v adb || ([ -x "$HOME/Android/Sdk/platform-tools/adb" ] && echo "$HOME/Android/Sdk/platform-tools/adb") || echo "$ROOT/tools/platform-tools/adb")}"
if "$ADB_BIN" devices | tail -n +2 | grep -q "device"; then
  ok "Device connected"
  "$ADB_BIN" shell id | grep -q "uid=0" \
    && ok "adb shell has root (KernelSU / Magisk)" \
    || warn "adb shell is not root; ensure_root may fail"
else
  warn "No device found. Enable USB debugging or connect via adb connect <ip>:5555"
fi

cat <<'EOS'

────────────────────────────────────────
 Setup complete

  ./scripts/run.sh check     # Environment self-check
  ./scripts/run.sh list      # List offers (no billing)
  ./scripts/run.sh step1     # 12-month trial
  ./scripts/run.sh step2     # +6 months
  ./scripts/run.sh step3     # +6 months
  ./scripts/run.sh step4     # Backup +6 months
────────────────────────────────────────
EOS
