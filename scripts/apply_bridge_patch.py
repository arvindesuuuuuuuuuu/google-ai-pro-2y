#!/usr/bin/env python3
"""Patch frida-java-bridge to support skipping base interface expansion.

On Android 16, JNI calls returning Object[] such as Class.getInterfaces() block
and cause Java.registerClass() to hang indefinitely. Billing listeners
(such as PurchasesUpdatedListener) have no base interfaces, so skipping this
expansion is safe.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "agent" / "node_modules" / "frida-java-bridge" / "lib" / "class-factory.js"

ORIGINAL = """      const allInterfaces = interfaces.slice();
      interfaces.forEach(iface => {
        Array.prototype.slice.call(iface.class.getInterfaces())
          .forEach(baseIface => {
            const baseIfaceName = this.cast(baseIface, Class).getCanonicalName();
            allInterfaces.push(this.use(baseIfaceName));
          });
      });"""

PATCHED = """      const allInterfaces = interfaces.slice();
      // --- patch: skipInterfaceExpansion ---
      // On Android 16, Class.getInterfaces() blocks, so skip expansion
      // when spec.skipInterfaceExpansion === true.
      if (spec.skipInterfaceExpansion !== true) {
        interfaces.forEach(iface => {
          Array.prototype.slice.call(iface.class.getInterfaces())
            .forEach(baseIface => {
              const baseIfaceName = this.cast(baseIface, Class).getCanonicalName();
              allInterfaces.push(this.use(baseIfaceName));
            });
        });
      }"""


def main() -> int:
    if not TARGET.exists():
        print(f"not found: {TARGET} (run npm install in agent/ first)", file=sys.stderr)
        return 1
    src = TARGET.read_text(encoding="utf-8")
    if "skipInterfaceExpansion" in src:
        print("already patched")
        return 0
    if ORIGINAL not in src:
        print("patch target not found (frida-java-bridge version mismatch?)", file=sys.stderr)
        return 1
    TARGET.write_text(src.replace(ORIGINAL, PATCHED, 1), encoding="utf-8")
    print(f"patched {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
