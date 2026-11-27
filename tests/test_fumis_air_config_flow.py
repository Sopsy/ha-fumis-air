"""Tests for Fumis AIR and Tulikivi Senso setup and pairing PIN handling."""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "fumis_air"
)
PACKAGE_NAME = "fumis_air_config_flow_test_package"
PACKAGE = types.ModuleType(PACKAGE_NAME)
PACKAGE.__path__ = [str(INTEGRATION_PATH)]
sys.modules[PACKAGE_NAME] = PACKAGE


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKAGE.DOMAIN = "fumis_air"
_load_module(f"{PACKAGE_NAME}.const", INTEGRATION_PATH / "const.py")
protocol = _load_module(f"{PACKAGE_NAME}.protocol", INTEGRATION_PATH / "protocol.py")
_load_module(f"{PACKAGE_NAME}.pairing", INTEGRATION_PATH / "pairing.py")


class FakeBleakError(Exception):
    """Stand-in for Bleak's connection exception."""


class FakeConfigFlow:
    """Small Home Assistant config-flow facade for unit tests."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def __init__(self):
        self.hass = object()

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


class FakeOptionsFlow:
    """Base options flow with result helpers for config-flow tests."""

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


class FakeTextSelector:
    def __init__(self, config):
        self.config = config


class FakeSchema:
    def __init__(self, schema):
        self.schema = schema


def _install_import_stubs():
    names = (
        "bleak",
        "bleak.exc",
        "voluptuous",
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.components",
        "homeassistant.components.bluetooth",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.selector",
    )
    previous = {name: sys.modules.get(name) for name in names}

    bleak = types.ModuleType("bleak")
    bleak.__path__ = []
    bleak_exc = types.ModuleType("bleak.exc")
    bleak_exc.BleakError = FakeBleakError

    voluptuous = types.ModuleType("voluptuous")
    voluptuous.Required = lambda value: ("required", value)
    voluptuous.Optional = lambda value, **kwargs: (
        "optional",
        value,
        tuple(sorted(kwargs.items())),
    )
    voluptuous.In = lambda values: ("in", values)
    voluptuous.Schema = FakeSchema

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = FakeConfigFlow
    config_entries.OptionsFlow = FakeOptionsFlow
    config_entries.OptionsFlowWithReload = FakeOptionsFlow
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    homeassistant.config_entries = config_entries

    components = types.ModuleType("homeassistant.components")
    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.BluetoothServiceInfoBleak = type(
        "BluetoothServiceInfoBleak", (), {}
    )
    bluetooth.async_discovered_service_info = lambda hass, connectable=True: []
    const = types.ModuleType("homeassistant.const")
    const.CONF_ADDRESS = "address"
    const.CONF_NAME = "name"
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda function: function

    helpers = types.ModuleType("homeassistant.helpers")
    selector = types.ModuleType("homeassistant.helpers.selector")
    selector.TextSelector = FakeTextSelector
    selector.TextSelectorConfig = lambda **kwargs: kwargs
    selector.TextSelectorType = types.SimpleNamespace(PASSWORD="password")
    selector.NumberSelector = FakeTextSelector
    selector.NumberSelectorConfig = lambda **kwargs: kwargs
    selector.NumberSelectorMode = types.SimpleNamespace(BOX="box")
    homeassistant.components = components
    homeassistant.const = const
    homeassistant.core = core
    homeassistant.helpers = helpers
    helpers.selector = selector
    components.bluetooth = bluetooth

    sys.modules.update(
        {
            "bleak": bleak,
            "bleak.exc": bleak_exc,
            "voluptuous": voluptuous,
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.components": components,
            "homeassistant.components.bluetooth": bluetooth,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.selector": selector,
        }
    )
    return previous


_STUBBED_MODULES = _install_import_stubs()

ble_module = types.ModuleType(f"{PACKAGE_NAME}.ble")


class FakeFumisAirConnectionError(Exception):
    """Stand-in for a failed controller BLE connection."""


ble_module.FumisAirConnectionError = FakeFumisAirConnectionError
ble_module.async_connect = None
ble_module.async_disconnect = None
sys.modules[ble_module.__name__] = ble_module

config_flow = _load_module(
    f"{PACKAGE_NAME}.config_flow", INTEGRATION_PATH / "config_flow.py"
)

for _module_name, _previous in _STUBBED_MODULES.items():
    if _previous is None:
        sys.modules.pop(_module_name, None)
    else:
        sys.modules[_module_name] = _previous


class FumisAirConfigFlowTest(unittest.TestCase):
    """Exercise strict advertisement filtering and confirmed setup."""

    def setUp(self):
        self.flow = config_flow.FumisAirConfigFlow()
        self.address = "AA:BB:CC:DD:EE:FF"
        self.events = []
        self.discoveries = {
            self.address: self._service_info(self.address),
        }
        config_flow.async_discovered_service_info = (
            lambda hass, connectable=True: list(self.discoveries.values())
        )

        async def fake_connect(hass, address):
            self.events.append(("profile", address))
            return object()

        async def fake_disconnect(client):
            self.events.append(("disconnect",))

        async def fake_pair(hass, address, pin):
            self.events.append(("pair", address, pin))

        async def fake_probe(address):
            self.events.append(("probe", address))

        config_flow.async_connect = fake_connect
        config_flow.async_disconnect = fake_disconnect
        config_flow.async_pair_device = fake_pair
        self.flow._async_probe = fake_probe

    @staticmethod
    def _service_info(
        address,
        name="Senso",
        connectable=True,
        service_uuids=None,
    ):
        return types.SimpleNamespace(
            address=address,
            name=name,
            connectable=connectable,
            service_uuids=service_uuids or [config_flow.SERVICE_UUID],
        )

    def test_manual_picker_filters_devices_and_does_not_save_pin(self):
        async def run():
            second_address = "11:22:33:44:55:66"
            self.discoveries[second_address] = self._service_info(second_address)
            unsupported_address = "44:55:66:77:88:99"
            self.discoveries[unsupported_address] = self._service_info(
                unsupported_address, name="Fumis"
            )
            airmaster_address = "55:66:77:88:99:AA"
            self.discoveries[airmaster_address] = self._service_info(
                airmaster_address, name="AirMaster"
            )
            unrelated_address = "33:44:55:66:77:88"
            self.discoveries[unrelated_address] = self._service_info(
                unrelated_address, name="Other"
            )
            nonconnectable_address = "22:33:44:55:66:77"
            self.discoveries[nonconnectable_address] = self._service_info(
                nonconnectable_address,
                name=nonconnectable_address,
                connectable=False,
            )
            wrong_service_address = "66:77:88:99:AA:BB"
            self.discoveries[wrong_service_address] = self._service_info(
                wrong_service_address,
                name=wrong_service_address,
                service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"],
            )

            form = await self.flow.async_step_user()
            choices = form["data_schema"].schema[("required", "address")][1]
            self.assertEqual(
                choices,
                {
                    second_address: f"Tulikivi Senso ({second_address})",
                    airmaster_address: f"AirMaster ({airmaster_address})",
                    self.address: f"Tulikivi Senso ({self.address})",
                },
            )

            form = await self.flow.async_step_user({"address": self.address})
            self.assertEqual(form["step_id"], "confirm")
            self.assertEqual(self.events, [])

            result = await self.flow.async_step_confirm({"pin": "000123"})

            self.assertEqual(
                result["data"],
                {
                    "address": self.address,
                    "model": "Tulikivi Senso",
                },
            )
            self.assertEqual(result["title"], f"Tulikivi Senso {self.address[-5:]}")
            self.assertEqual(
                self.events,
                [
                    ("profile", self.address),
                    ("disconnect",),
                    ("pair", self.address, "000123"),
                    ("probe", self.address),
                ],
            )

        asyncio.run(run())

    def test_discovery_requires_exact_name_service_and_connectability(self):
        async def run():
            for discovery in (
                self._service_info(self.address, name="Senso living room"),
                self._service_info(self.address, name=self.address),
                self._service_info(self.address, name=""),
                self._service_info(self.address, name="Fumis"),
                self._service_info(self.address, name="Fumis living room"),
                self._service_info(self.address, name="Fumis AIR living room"),
                self._service_info(self.address, name="AirMaster living room"),
                self._service_info(
                    self.address,
                    service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"],
                ),
                self._service_info(self.address, connectable=False),
            ):
                result = await self.flow.async_step_bluetooth(discovery)
                self.assertEqual(result, {"type": "abort", "reason": "not_supported"})

            discovery = self._service_info(self.address)
            form = await self.flow.async_step_bluetooth(discovery)
            self.assertEqual(form["step_id"], "confirm")
            self.assertEqual(self.events, [])

            result = await self.flow.async_step_confirm({"pin": "000000"})

            self.assertEqual(result["data"], {
                "address": self.address,
                "model": "Tulikivi Senso",
            })
            self.assertEqual(result["title"], f"Tulikivi Senso {self.address[-5:]}")
            self.assertIn(("pair", self.address, "000000"), self.events)
            self.assertNotIn("pin", result["data"])

        asyncio.run(run())

    def test_manual_picker_accepts_unnamed_controller_by_address(self):
        async def run():
            unnamed_address = "3C:E0:64:FB:59:AF"
            self.discoveries[unnamed_address] = self._service_info(
                unnamed_address, name=unnamed_address
            )
            form = await self.flow.async_step_user()
            choices = form["data_schema"].schema[("required", "address")][1]
            self.assertEqual(
                choices[unnamed_address],
                f"Unnamed controller ({unnamed_address})",
            )

            form = await self.flow.async_step_user({"address": unnamed_address})
            self.assertEqual(form["step_id"], "model")
            form = await self.flow.async_step_model({"model": "Tulikivi Senso"})
            self.assertEqual(form["step_id"], "confirm")
            result = await self.flow.async_step_confirm({})

            self.assertEqual(
                result["data"],
                {"address": unnamed_address, "model": "Tulikivi Senso"},
            )
            self.assertEqual(result["title"], "Tulikivi Senso 59:AF")
            self.assertEqual(self.events, [("probe", unnamed_address)])

        asyncio.run(run())

    def test_fumis_air_advertisement_creates_fumis_air_device(self):
        async def run():
            discovery = self._service_info(self.address, name="Fumis AIR")
            form = await self.flow.async_step_bluetooth(discovery)
            self.assertEqual(form["step_id"], "confirm")

            result = await self.flow.async_step_confirm({})

            self.assertEqual(result["title"], f"Fumis AIR {self.address[-5:]}")
            self.assertEqual(
                result["data"],
                {
                    "address": self.address,
                    "model": "Fumis AIR",
                },
            )
            self.assertEqual(self.events, [("probe", self.address)])

        asyncio.run(run())

    def test_airmaster_advertisement_requires_explicit_profile(self):
        async def run(model):
            discovery = self._service_info(self.address, name="AirMaster")
            self.discoveries[self.address] = discovery
            manual = await self.flow.async_step_user({"address": self.address})
            self.assertEqual(manual["step_id"], "model")
            form = await self.flow.async_step_bluetooth(discovery)
            self.assertEqual(form["step_id"], "model")
            form = await self.flow.async_step_model({"model": model})
            self.assertEqual(form["step_id"], "confirm")

            result = await self.flow.async_step_confirm({})

            self.assertEqual(result["title"], f"{model} {self.address[-5:]}")
            self.assertEqual(
                result["data"],
                {
                    "address": self.address,
                    "model": model,
                },
            )
            self.assertEqual(self.events, [("probe", self.address)])

        for model in ("Fumis AIR", "Tulikivi Senso"):
            with self.subTest(model=model):
                self.setUp()
                asyncio.run(run(model))

    def test_manual_setup_rejects_a_device_not_in_the_picker(self):
        async def run():
            second_address = "11:22:33:44:55:66"
            self.discoveries[second_address] = self._service_info(second_address)
            await self.flow.async_step_user()
            self.discoveries.pop(self.address)

            result = await self.flow.async_step_user({"address": self.address})
            self.assertEqual(result["step_id"], "user")
            self.assertEqual(result["errors"], {"base": "device_not_found"})
            choices = result["data_schema"].schema[("required", "address")][1]
            self.assertEqual(
                choices, {second_address: f"Tulikivi Senso ({second_address})"}
            )
            self.assertEqual(self.events, [])

        asyncio.run(run())

    def test_setup_probe_uses_selected_controller_profile(self):
        async def run(name, expected_registers, status):
            self.flow.__dict__.pop("_async_probe")
            await self.flow.async_step_bluetooth(self._service_info(self.address, name=name))
            read_registers = config_flow.read_registers

            async def fake_read_registers(client, registers):
                self.assertEqual(registers, expected_registers)
                return {
                    protocol.REG_STATUS: status,
                    protocol.DISP_VAL_USER: 0,
                }

            config_flow.read_registers = fake_read_registers
            try:
                await self.flow._async_probe(self.address)
            finally:
                config_flow.read_registers = read_registers

            self.assertEqual(
                self.events,
                [("profile", self.address), ("disconnect",)],
            )

        asyncio.run(run("Senso", (protocol.REG_STATUS, protocol.REG_ADVICE, protocol.DISP_VAL_USER), 2))
        self.setUp()
        asyncio.run(run("Fumis AIR", (protocol.REG_STATUS, protocol.DISP_VAL_USER), 5))

    def test_manual_setup_aborts_when_no_matching_devices_are_discovered(self):
        async def run():
            self.discoveries = {}
            result = await self.flow.async_step_user()
            self.assertEqual(result, {"type": "abort", "reason": "no_devices_found"})

        asyncio.run(run())

    def test_global_advanced_options_flow_is_removed(self):
        self.assertFalse(hasattr(config_flow, "FumisAirOptionsFlow"))
        self.assertFalse(
            hasattr(config_flow.FumisAirConfigFlow, "async_get_options_flow")
        )

    def test_invalid_pin_is_rejected_without_connecting_or_pairing(self):
        async def run():
            await self.flow.async_step_user()
            await self.flow.async_step_user({"address": self.address})

            result = await self.flow.async_step_confirm({"pin": "123"})

            self.assertEqual(result["errors"], {"pin": "invalid_pin"})
            self.assertEqual(self.events, [])

        asyncio.run(run())

    def test_unsupported_adapter_keeps_the_ordinary_connection_path(self):
        async def run():
            await self.flow.async_step_user()
            await self.flow.async_step_user({"address": self.address})

            async def unsupported_pair(hass, address, pin):
                self.events.append(("pair_unsupported", address, pin))
                raise config_flow.PairingNotSupported("No local BlueZ device")

            config_flow.async_pair_device = unsupported_pair
            result = await self.flow.async_step_confirm({"pin": "000123"})

            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(
                self.events,
                [
                    ("profile", self.address),
                    ("disconnect",),
                    ("pair_unsupported", self.address, "000123"),
                    ("probe", self.address),
                ],
            )

        asyncio.run(run())

    def test_pairing_errors_remain_distinct_from_connection_errors(self):
        async def run():
            await self.flow.async_step_user()
            await self.flow.async_step_user({"address": self.address})

            async def failed_pair(hass, address, pin):
                raise config_flow.PairingError("Pairing rejected")

            config_flow.async_pair_device = failed_pair
            pairing = await self.flow.async_step_confirm({"pin": "000123"})
            self.assertEqual(pairing["errors"], {"base": "pairing_failed"})

            self.flow = config_flow.FumisAirConfigFlow()
            await self.flow.async_step_user()
            await self.flow.async_step_user({"address": self.address})

            async def unsupported_pair(hass, address, pin):
                raise config_flow.PairingNotSupported("No local BlueZ device")

            async def failed_probe(address):
                raise FakeFumisAirConnectionError("Link unavailable")

            config_flow.async_pair_device = unsupported_pair
            self.flow._async_probe = failed_probe
            unsupported = await self.flow.async_step_confirm({"pin": "000123"})
            self.assertEqual(unsupported["errors"], {"base": "pairing_unsupported"})

            self.flow = config_flow.FumisAirConfigFlow()
            await self.flow.async_step_user()
            await self.flow.async_step_user({"address": self.address})

            async def failed_connect(hass, address):
                raise FakeFumisAirConnectionError("Link unavailable")

            config_flow.async_connect = failed_connect
            connection = await self.flow.async_step_confirm({"pin": "000123"})
            self.assertEqual(connection["errors"], {"base": "cannot_connect"})

        asyncio.run(run())
