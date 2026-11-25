"""frida_conn.py -- Lazy Frida device connector supporting local USB/ADB or FRIDA_HOST."""

from __future__ import annotations

import os


def connect_device(frida_mod, timeout: float = 10.0):
    """Return remote FRIDA_HOST device when configured, else default USB/ADB device."""
    host = os.environ.get("FRIDA_HOST", "").strip()
    if host:
        return frida_mod.get_device_manager().add_remote_device(host)
    return frida_mod.get_usb_device(timeout=timeout)
