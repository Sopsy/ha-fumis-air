"""Tests for the temporary, device-scoped Senso BlueZ pairing agent."""

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest


PAIRING_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fumis_air"
    / "pairing.py"
)
PAIRING_SPEC = importlib.util.spec_from_file_location("fumis_air_pairing", PAIRING_PATH)
pairing = importlib.util.module_from_spec(PAIRING_SPEC)
sys.modules[PAIRING_SPEC.name] = pairing
PAIRING_SPEC.loader.exec_module(pairing)


class FakeDBusError(Exception):
    """Stand-in for dbus-fast's exported method error."""


class FakeServiceInterface:
    """Minimal dbus-fast service interface used by agent tests."""

    def __init__(self, name):
        self.name = name


class FakeAgentManager:
    """Track registration of the temporary agent."""

    def __init__(self, bus):
        self.bus = bus

    async def call_register_agent(self, path, capability):
        self.bus.registered_agent = (path, capability)

    async def call_unregister_agent(self, path):
        self.bus.unregistered_agent = path


class FakeObjectManager:
    """Return BlueZ devices for one local adapter."""

    def __init__(self, bus):
        self.bus = bus

    async def call_get_managed_objects(self):
        return self.bus.objects


class FakeDeviceInterface:
    """Simulate a BlueZ Device1.Pair call."""

    def __init__(self, bus, path):
        self.bus = bus
        self.path = path

    async def call_pair(self):
        agent = self.bus.exported_agent
        self.bus.received_pin = agent.RequestPinCode(self.path)
        self.bus.received_passkey = agent.RequestPasskey(self.path)
        if self.bus.pair_error:
            raise RuntimeError("pair failed")


class FakeProxyObject:
    """Resolve the three interfaces used by the pairing transaction."""

    def __init__(self, bus, path):
        self.bus = bus
        self.path = path

    def get_interface(self, interface):
        if interface == "org.freedesktop.DBus.ObjectManager":
            return FakeObjectManager(self.bus)
        if interface == "org.bluez.AgentManager1":
            return FakeAgentManager(self.bus)
        if interface == "org.bluez.Device1":
            return FakeDeviceInterface(self.bus, self.path)
        raise AssertionError(f"Unexpected interface: {interface}")


class FakeBus:
    """Track bus exports, calls, and cleanup."""

    def __init__(self, address, path, paired=False, pair_error=False):
        self.objects = {
            path: {
                "org.bluez.Device1": {
                    "Address": address,
                    "Paired": paired,
                }
            }
        }
        self.pair_error = pair_error
        self.exported_agent = None
        self.registered_agent = None
        self.unregistered_agent = None
        self.received_pin = None
        self.received_passkey = None
        self.disconnected = False
        self.unexported = None

    async def introspect(self, service, path):
        return (service, path)

    def get_proxy_object(self, service, path, introspection):
        return FakeProxyObject(self, path)

    def export(self, path, agent):
        self.exported_agent = agent

    def unexport(self, path):
        self.unexported = path
        self.exported_agent = None

    def disconnect(self):
        self.disconnected = True


class FakeMessageBus:
    """Factory matching dbus-fast's system MessageBus constructor."""

    bus = None

    def __init__(self, *, bus_type):
        self.bus_type = bus_type

    async def connect(self):
        return self.bus


class SensoPairingTest(unittest.TestCase):
    """Verify PIN scoping, paired-device handling, and cleanup."""

    def setUp(self):
        self.address = "AA:BB:CC:DD:EE:FF"
        self.device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
        self.bus = FakeBus(self.address, self.device_path)
        FakeMessageBus.bus = self.bus
        self._install_import_stubs()

    def tearDown(self):
        for name, previous in self._original_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def _install_import_stubs(self):
        names = (
            "homeassistant",
            "homeassistant.components",
            "homeassistant.components.bluetooth",
            "dbus_fast",
            "dbus_fast.aio",
            "dbus_fast.constants",
            "dbus_fast.service",
        )
        self._original_modules = {name: sys.modules.get(name) for name in names}

        homeassistant = types.ModuleType("homeassistant")
        components = types.ModuleType("homeassistant.components")
        bluetooth = types.ModuleType("homeassistant.components.bluetooth")
        bluetooth.async_ble_device_from_address = lambda hass, address, connectable: (
            types.SimpleNamespace(details={"path": self.device_path})
        )
        homeassistant.components = components
        components.bluetooth = bluetooth

        dbus_fast = types.ModuleType("dbus_fast")
        dbus_fast.__path__ = []
        dbus_fast.DBusError = FakeDBusError
        dbus_fast_aio = types.ModuleType("dbus_fast.aio")
        dbus_fast_aio.MessageBus = FakeMessageBus
        dbus_fast_constants = types.ModuleType("dbus_fast.constants")
        dbus_fast_constants.BusType = types.SimpleNamespace(SYSTEM="system")
        dbus_fast_service = types.ModuleType("dbus_fast.service")
        dbus_fast_service.ServiceInterface = FakeServiceInterface
        dbus_fast_service.dbus_method = lambda: (lambda method: method)

        sys.modules.update(
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.bluetooth": bluetooth,
                "dbus_fast": dbus_fast,
                "dbus_fast.aio": dbus_fast_aio,
                "dbus_fast.constants": dbus_fast_constants,
                "dbus_fast.service": dbus_fast_service,
            }
        )

    def test_pin_validation_preserves_leading_zeroes(self):
        self.assertTrue(pairing.valid_pin("000123"))
        self.assertFalse(pairing.valid_pin("123"))
        self.assertFalse(pairing.valid_pin("1234567"))
        self.assertFalse(pairing.valid_pin("12a456"))

    def test_agent_rejects_a_different_device(self):
        agent = pairing._make_pairing_agent(
            FakeServiceInterface,
            lambda: lambda method: method,
            FakeDBusError,
            self.device_path,
            "000123",
        )

        self.assertEqual(agent.RequestPinCode(self.device_path), "000123")
        self.assertEqual(agent.RequestPasskey(self.device_path), 123)
        with self.assertRaises(FakeDBusError):
            agent.RequestPinCode("/org/bluez/hci0/dev_00_00_00_00_00_00")

    def test_pairing_uses_the_selected_device_and_cleans_up(self):
        asyncio.run(pairing.async_pair_device(object(), self.address, "000123"))

        self.assertEqual(self.bus.received_pin, "000123")
        self.assertEqual(self.bus.received_passkey, 123)
        self.assertEqual(
            self.bus.registered_agent,
            (pairing.AGENT_PATH, "KeyboardOnly"),
        )
        self.assertEqual(self.bus.unregistered_agent, pairing.AGENT_PATH)
        self.assertEqual(self.bus.unexported, pairing.AGENT_PATH)
        self.assertTrue(self.bus.disconnected)

    def test_existing_bond_skips_temporary_agent(self):
        self.bus.objects[self.device_path]["org.bluez.Device1"]["Paired"] = True

        asyncio.run(pairing.async_pair_device(object(), self.address, "000123"))

        self.assertIsNone(self.bus.registered_agent)
        self.assertTrue(self.bus.disconnected)

    def test_failed_pairing_still_unregisters_and_disconnects(self):
        self.bus.pair_error = True

        with self.assertRaises(pairing.PairingError):
            asyncio.run(pairing.async_pair_device(object(), self.address, "000123"))

        self.assertEqual(self.bus.unregistered_agent, pairing.AGENT_PATH)
        self.assertEqual(self.bus.unexported, pairing.AGENT_PATH)
        self.assertTrue(self.bus.disconnected)
