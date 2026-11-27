"""Tests for the Senso BLE connection helper."""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "fumis_air"
)
PACKAGE_NAME = "fumis_air_ble_test_package"


class FakeBleakError(Exception):
    pass


class FakeBleakClient:
    pass


class FakeBleakClientWithServiceCache:
    pass


def _load_ble_module():
    """Load ble.py with lightweight dependency facades."""
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(INTEGRATION_PATH)]
    protocol = types.ModuleType(f"{PACKAGE_NAME}.protocol")
    protocol.CHARACTERISTIC_ADDRESS_UUID = "address-uuid"
    protocol.CHARACTERISTIC_DATA_UUID = "data-uuid"
    protocol.SERVICE_UUID = "service-uuid"
    protocol.FumisAirProtocolError = type("FumisAirProtocolError", (Exception,), {})
    sys.modules[PACKAGE_NAME] = package
    sys.modules[protocol.__name__] = protocol

    bleak = types.ModuleType("bleak")
    bleak.__path__ = []
    bleak.BleakClient = FakeBleakClient
    bleak_exc = types.ModuleType("bleak.exc")
    bleak_exc.BleakError = FakeBleakError
    connector = types.ModuleType("bleak_retry_connector")
    connector.BleakClientWithServiceCache = FakeBleakClientWithServiceCache
    connector.establish_connection = None

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.async_ble_device_from_address = None
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    dependencies = {
        "bleak": bleak,
        "bleak.exc": bleak_exc,
        "bleak_retry_connector": connector,
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.bluetooth": bluetooth,
        "homeassistant.core": core,
    }
    previous = {name: sys.modules.get(name) for name in dependencies}
    sys.modules.update(dependencies)
    try:
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE_NAME}.ble", INTEGRATION_PATH / "ble.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module
    return module


ble_module = _load_ble_module()


class FakeService:
    def __init__(self, characteristics=None):
        self.characteristics = set(
            characteristics
            if characteristics is not None
            else ("address-uuid", "data-uuid")
        )

    def get_characteristic(self, uuid):
        return uuid if uuid in self.characteristics else None


class FakeServices:
    def __init__(self, service=True):
        self.service = FakeService() if service is True else service

    def get_service(self, uuid):
        return self.service if uuid == "service-uuid" else None


class FakeConnectedClient:
    def __init__(self, service=True, pair_error=None):
        self.is_connected = True
        self.services = FakeServices(service)
        self.pair_error = pair_error
        self.pair_called = False
        self.disconnect_count = 0

    async def pair(self):
        self.pair_called = True
        if self.pair_error is not None:
            raise self.pair_error

    async def disconnect(self):
        self.disconnect_count += 1
        self.is_connected = False


class FumisAirBleConnectionTest(unittest.TestCase):
    def setUp(self):
        self.device = object()
        self.address = "AA:BB:CC:DD:EE:FF"
        self.discovery_result = self.device
        self.connector_calls = []
        ble_module.async_ble_device_from_address = (
            lambda hass, address, connectable: self.discovery_result
        )

    def _set_connector(self, result=None, error=None):
        async def fake_establish_connection(client_class, device, name, **kwargs):
            self.connector_calls.append((client_class, device, name, kwargs))
            if error is not None:
                raise error
            return result

        ble_module.establish_connection = fake_establish_connection

    def test_connect_delegates_to_retry_connector(self):
        client = FakeConnectedClient()
        self._set_connector(result=client)

        connected = asyncio.run(
            ble_module.async_connect(object(), self.address)
        )

        self.assertIs(connected, client)
        self.assertTrue(client.pair_called)
        self.assertEqual(
            self.connector_calls,
            [
                (
                    FakeBleakClientWithServiceCache,
                    self.device,
                    self.address,
                    {"timeout": 15},
                )
            ],
        )

    def test_missing_connectable_device_is_reported_without_raw_address_fallback(self):
        self.discovery_result = None
        self._set_connector(result=FakeConnectedClient())

        with self.assertRaises(ble_module.FumisAirConnectionError):
            asyncio.run(ble_module.async_connect(object(), self.address))

        self.assertEqual(self.connector_calls, [])

    def test_connector_failure_is_propagated(self):
        error = FakeBleakError("connection failed")
        self._set_connector(error=error)

        with self.assertRaises(FakeBleakError) as raised:
            asyncio.run(ble_module.async_connect(object(), self.address))

        self.assertIs(raised.exception, error)

    def test_profile_validation_failure_disconnects_client(self):
        client = FakeConnectedClient(service=None)
        self._set_connector(result=client)

        with self.assertRaises(ble_module.FumisAirProtocolError):
            asyncio.run(ble_module.async_connect(object(), self.address))

        self.assertEqual(client.disconnect_count, 1)

    def test_pairing_failure_disconnects_client(self):
        error = RuntimeError("pairing failed")
        client = FakeConnectedClient(pair_error=error)
        self._set_connector(result=client)

        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(ble_module.async_connect(object(), self.address))

        self.assertIs(raised.exception, error)
        self.assertEqual(client.disconnect_count, 1)


if __name__ == "__main__":
    unittest.main()