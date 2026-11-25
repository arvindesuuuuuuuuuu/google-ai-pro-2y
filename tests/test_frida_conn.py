"""Tests for frida_conn remote FRIDA_HOST vs default USB device selection."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import frida_conn


class _FakeManager:
    def __init__(self):
        self.addresses = []

    def add_remote_device(self, address):
        self.addresses.append(address)
        return ("remote", address)


class _FakeFrida:
    def __init__(self):
        self.usb_timeouts = []
        self.manager = _FakeManager()

    def get_usb_device(self, timeout=10):
        self.usb_timeouts.append(timeout)
        return ("usb", timeout)

    def get_device_manager(self):
        return self.manager


class FridaConnTests(unittest.TestCase):
    def test_defaults_to_usb_when_frida_host_unset(self):
        fake = _FakeFrida()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FRIDA_HOST", None)
            dev = frida_conn.connect_device(fake, timeout=7.5)
        self.assertEqual(dev, ("usb", 7.5))
        self.assertEqual(fake.usb_timeouts, [7.5])
        self.assertEqual(fake.manager.addresses, [])

    def test_uses_remote_device_when_frida_host_set(self):
        fake = _FakeFrida()
        with mock.patch.dict(os.environ, {"FRIDA_HOST": " 127.0.0.1:27042 "}, clear=False):
            dev = frida_conn.connect_device(fake, timeout=10)
        self.assertEqual(dev, ("remote", "127.0.0.1:27042"))
        self.assertEqual(fake.manager.addresses, ["127.0.0.1:27042"])
        self.assertEqual(fake.usb_timeouts, [])


if __name__ == "__main__":
    unittest.main()
