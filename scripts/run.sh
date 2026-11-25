#!/usr/bin/env bash
# Usage: ./scripts/run.sh <command>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "Run ./scripts/setup.sh first" >&2; exit 1; }
PY=.venv/bin/python

if [ -z "${ADB_PATH:-}" ]; then
  if [ -x "$ROOT/tools/platform-tools/adb" ]; then
    export ADB_PATH="$ROOT/tools/platform-tools/adb"
  elif command -v adb >/dev/null 2>&1; then
    export ADB_PATH="$(command -v adb)"
  elif [ -x "$HOME/Android/Sdk/platform-tools/adb" ]; then
    export ADB_PATH="$HOME/Android/Sdk/platform-tools/adb"
  fi
fi
export PYTHONIOENCODING=utf-8

ACC=("--account" "current@device" "--no-add" "--force")
run17() { exec "$PY" scripts/ultra0-17.py "${ACC[@]}" "$@"; }

case "${1:-}" in
  check)  exec "$PY" scripts/ultra0-17.py --check ;;
  list)   shift; run17 --list --plan "${1:-pro12}" ;;
  scan)   shift; exec "$PY" scripts/sku_scanner.py "$@" ;;
  combo)  shift; exec "$PY" scripts/combo.py "$@" ;;
  test)   exec "$PY" -m unittest discover -s tests -v ;;
  step1)  run17 --plan pro12 ;;
  step2)  run17 --stack --plan pro6 ;;
  step3)  run17 --stack --plan pro6b ;;
  step4)  run17 --stack --plan pro6c ;;
  ultra)  shift; run17 --plan "${1:-auto}" ;;
  build-agent) exec "$PY" scripts/build_agent.py ;;
  fix-frida)   exec ./scripts/fix_frida.sh ;;
  setup)  exec ./scripts/setup.sh ;;
  *)
    cat <<'EOS'
Usage: ./scripts/run.sh <command>

  setup        Set up environment (first run only)
  check        Run environment self-check (device/root/frida/billing module)
  list         List offers (read-only, zero billing risk)
  scan         SKU scanner (read-only, no billing)
                 scan generate   Generate candidate SKUs
                 scan query      Query offers from live device
                 scan summarize  Format saved report
  combo        Free-offer combo planner (dry-run by default)
                 combo plan                 Show strategy and steps (use --device to read device)
                 combo run --execute --yes  Run step by step (human completes payment)
  test         Run unit tests
  step1        12-month free trial
  step2        +6 months (after step1 succeeds)
  step3        +6 months (after step2 succeeds)
  step4        Backup +6 months (may error)
  build-agent  Rebuild Frida agent
  fix-frida    Resolve frida-server version mismatch
EOS
    exit 1 ;;
esac
