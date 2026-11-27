"""Tests for the Fumis AIR and Tulikivi Senso BLE register protocol."""

import asyncio
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fumis_air"
    / "protocol.py"
)
PACKAGE_NAME = "fumis_air_test_package"
integration_package = types.ModuleType(PACKAGE_NAME)
integration_package.__path__ = [str(MODULE_PATH.parent)]
integration_package.DOMAIN = "fumis_air"
integration_package.FumisAirConfigEntry = object
sys.modules[PACKAGE_NAME] = integration_package

CONST_SPEC = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.const", MODULE_PATH.with_name("const.py")
)
const = importlib.util.module_from_spec(CONST_SPEC)
sys.modules[CONST_SPEC.name] = const
CONST_SPEC.loader.exec_module(const)

SPEC = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.protocol", MODULE_PATH
)
protocol = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = protocol
SPEC.loader.exec_module(protocol)


def _load_module(name, path):
    """Load a package module directly from its integration source file."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parameters_module = sys.modules[f"{PACKAGE_NAME}.parameters"]
SENSO_CATALOG = parameters_module.ENTITY_CATALOG[const.SENSO_MODEL]
OVERVIEW_REGISTERS = (
    protocol.MIN_LIMIT_ROOM_TEMP,
    protocol.MAX_LIMIT_ROOM_TEMP,
    protocol.DESIRE_ROOM_TEMP,
    protocol.DISP_VAL_USER,
)
USER_SETTING_REGISTERS = (
    protocol.REG_ROOM_TEMP_EN,
    protocol.REG_LED_BRIGHTNESS,
    protocol.REG_SOUND_VOLUME,
    protocol.REG_FUEL,
    protocol.REG_FUEL_COUNT,
)


def _catalog_register(key):
    return SENSO_CATALOG[key].register


def _decode_request(payload):
    return tuple(
        int.from_bytes(payload[index : index + 2], "big")
        for index in range(0, len(payload), 2)
    )


# Keep readable fixture names while deriving every address from the catalog.
advanced = types.SimpleNamespace(
    REG_TTS=_catalog_register("time_to_service"),
    EVO_COUNT=_catalog_register("overheatings_count"),
    EVO_TIME=_catalog_register("overheating_time"),
    EVO_TEMP=_catalog_register("maximum_temperature"),
    EVM_COUNT=_catalog_register("misfires_count"),
    VAL_TEMP_BOARD=_catalog_register("board_temperature"),
    VAL_VOLTAGE_SUPPLY=_catalog_register("supply_voltage"),
    VAL_VOLTAGE_BATTERY=_catalog_register("battery_voltage"),
    REG_AIR_MAN_TIMEOUT=_catalog_register("air_management_timeout"),
    IN_CONFIG_1=_catalog_register("input_1_configuration"),
    IN_CONFIG_2=_catalog_register("input_2_configuration"),
    IN_CONFIG_3=_catalog_register("input_3_configuration"),
    IN_CONFIG_4=_catalog_register("input_4_configuration"),
    IN_CONFIG_5=_catalog_register("input_5_configuration"),
    IN_CONFIG_6=_catalog_register("input_6_configuration"),
    **{
        f"VAL_INPUT_{channel}": _catalog_register(f"input_{channel}_value")
        for channel in range(1, 7)
    },
    ADVANCED_REGISTERS={
        row.register
        for row in SENSO_CATALOG.values()
        if row.display_bit is None and row.polling_tier == "telemetry"
    },
)


def _load_coordinator():
    """Load the coordinator with minimal Home Assistant and BLE facades."""
    names = (
        "bleak",
        "bleak.exc",
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.binary_sensor",
        "homeassistant.components.number",
        "homeassistant.components.sensor",
        "homeassistant.components.select",
        "homeassistant.components.switch",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator",
    )
    previous = {name: sys.modules.get(name) for name in names}

    class FakeHomeAssistantError(Exception):
        pass

    class FakeUpdateFailed(Exception):
        pass

    class FakeDataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, **kwargs):
            self.hass = hass
            self.data = None
            self.available = True
            self.last_update_success = True
            self.update_interval = kwargs.get("update_interval")
            self.update_method = kwargs.get("update_method")
            self.contexts = set()
            self.refresh_count = 0
            self.request_refresh_count = 0

        async def _async_update_data(self):
            return await self.update_method()

        async def async_request_refresh(self):
            self.request_refresh_count += 1

        def async_contexts(self):
            return self.contexts

        def async_set_updated_data(self, data):
            self.data = data
            self.last_update_success = True

        async def async_refresh(self):
            self.refresh_count += 1
            try:
                self.data = await self._async_update_data()
            except FakeUpdateFailed:
                self.last_update_success = False
            else:
                self.last_update_success = True

        async def async_shutdown(self):
            return None

    bleak = types.ModuleType("bleak")
    bleak.__path__ = []
    bleak_exc = types.ModuleType("bleak.exc")
    bleak_exc.BleakError = type("BleakError", (Exception,), {})
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.HomeAssistantError = FakeHomeAssistantError
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.DataUpdateCoordinator = FakeDataUpdateCoordinator
    update_coordinator.UpdateFailed = FakeUpdateFailed
    bleak.exc = bleak_exc
    homeassistant.core = core
    homeassistant.exceptions = exceptions
    homeassistant.helpers = helpers
    helpers.update_coordinator = update_coordinator

    sys.modules.update(
        {
            "bleak": bleak,
            "bleak.exc": bleak_exc,
            "homeassistant": homeassistant,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )

    ble_module = types.ModuleType(f"{PACKAGE_NAME}.ble")
    ble_module.FumisAirConnectionError = type(
        "FumisAirConnectionError", (Exception,), {}
    )
    ble_module.async_connect = None
    ble_module.async_disconnect = None
    sys.modules[ble_module.__name__] = ble_module
    coordinator = _load_module(
        f"{PACKAGE_NAME}.coordinator", MODULE_PATH.with_name("coordinator.py")
    )
    platform_modules = _load_platforms(coordinator)

    for name, old_module in previous.items():
        if old_module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old_module
    return coordinator, FakeHomeAssistantError, platform_modules


def _load_platforms(coordinator_module):
    """Load entity platforms using the shared Home Assistant facade."""

    class FakeCoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator, context=None):
            self.coordinator = coordinator
            self.context = context

        @property
        def available(self):
            return self.coordinator.available

    class FakeNumberEntity:
        pass

    @dataclass(frozen=True)
    class FakeSensorEntityDescription:
        key: str
        name: str | None = None
        native_unit_of_measurement: str | None = None
        device_class: object | None = None
        state_class: object | None = None
        entity_category: object | None = None
        suggested_display_precision: int | None = None

    @dataclass(frozen=True)
    class FakeBinarySensorEntityDescription:
        key: str
        name: str | None = None
        device_class: object | None = None
        entity_category: object | None = None

    homeassistant = sys.modules["homeassistant"]
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    number = types.ModuleType("homeassistant.components.number")
    number.NumberDeviceClass = types.SimpleNamespace(TEMPERATURE="temperature")
    number.NumberEntity = FakeNumberEntity
    number.NumberMode = types.SimpleNamespace(BOX="box")
    switch = types.ModuleType("homeassistant.components.switch")
    switch.SwitchEntity = type("FakeSwitchEntity", (), {})
    select = types.ModuleType("homeassistant.components.select")
    select.SelectEntity = type("FakeSelectEntity", (), {})
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorDeviceClass = StrEnum("SensorDeviceClass", ("POWER", "TEMPERATURE"))
    sensor.SensorEntity = type("FakeSensorEntity", (), {})
    sensor.SensorEntityDescription = FakeSensorEntityDescription
    sensor.SensorStateClass = StrEnum("SensorStateClass", ("MEASUREMENT", "TOTAL_INCREASING"))
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorDeviceClass = StrEnum("BinarySensorDeviceClass", ("DOOR", "PROBLEM"))
    binary_sensor.BinarySensorEntity = type("FakeBinarySensorEntity", (), {})
    binary_sensor.BinarySensorEntityDescription = FakeBinarySensorEntityDescription
    const = types.ModuleType("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(
        CONFIG="config", DIAGNOSTIC="diagnostic"
    )
    const.UnitOfPower = types.SimpleNamespace(WATT="W")
    const.UnitOfPressure = types.SimpleNamespace(PA="Pa")
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
    core = sys.modules["homeassistant.core"]
    helpers = sys.modules["homeassistant.helpers"]
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = lambda **kwargs: kwargs
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    update_coordinator = sys.modules["homeassistant.helpers.update_coordinator"]
    update_coordinator.CoordinatorEntity = FakeCoordinatorEntity
    update_coordinator.DataUpdateCoordinator = (
        coordinator_module.DataUpdateCoordinator
    )
    components.sensor = sensor
    components.binary_sensor = binary_sensor
    components.number = number
    components.select = select
    components.switch = switch
    homeassistant.components = components
    homeassistant.const = const
    homeassistant.core = core
    homeassistant.helpers = helpers
    helpers.entity = entity
    helpers.entity_platform = entity_platform
    helpers.update_coordinator = update_coordinator

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.binary_sensor": binary_sensor,
            "homeassistant.components.number": number,
            "homeassistant.components.sensor": sensor,
            "homeassistant.components.select": select,
            "homeassistant.components.switch": switch,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.entity": entity,
            "homeassistant.helpers.entity_platform": entity_platform,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )
    number_module = _load_module(
        f"{PACKAGE_NAME}.number", MODULE_PATH.with_name("number.py")
    )
    switch_module = _load_module(
        f"{PACKAGE_NAME}.switch", MODULE_PATH.with_name("switch.py")
    )
    select_module = _load_module(
        f"{PACKAGE_NAME}.select", MODULE_PATH.with_name("select.py")
    )
    sensor_module = _load_module(
        f"{PACKAGE_NAME}.sensor", MODULE_PATH.with_name("sensor.py")
    )
    binary_sensor_module = _load_module(
        f"{PACKAGE_NAME}.binary_sensor",
        MODULE_PATH.with_name("binary_sensor.py"),
    )
    return (
        number_module,
        switch_module,
        sensor_module,
        binary_sensor_module,
        select_module,
    )


(
    coordinator_module,
    FakeHomeAssistantError,
    (
        number_module,
        switch_module,
        sensor_module,
        binary_sensor_module,
        select_module,
    ),
) = _load_coordinator()


class FakePeripheral:
    """Fake GATT peer with register-backed reads and writes."""

    def __init__(
        self,
        registers,
        fail_reads=(),
        ignored_write_registers=(),
        delayed_write_registers=(),
    ):
        self.registers = dict(registers)
        self.is_connected = True
        self.operations = []
        self.response_sizes = []
        self.pending_registers = []
        self.fail_reads = set(fail_reads)
        self.ignored_write_registers = set(ignored_write_registers)
        self.delayed_write_registers = set(delayed_write_registers)
        self.delayed_writes = {}

    async def write_gatt_char(self, characteristic, data, *, response=False):
        self.operations.append(("write", characteristic, bytes(data), response))
        words = [
            int.from_bytes(data[index : index + 2], "big")
            for index in range(0, len(data), 2)
        ]
        if characteristic == protocol.CHARACTERISTIC_ADDRESS_UUID:
            self.pending_registers = words
        elif characteristic == protocol.CHARACTERISTIC_DATA_UUID:
            for register, value in zip(self.pending_registers, words):
                if register in self.delayed_write_registers:
                    self.delayed_writes[register] = value
                elif register not in self.ignored_write_registers:
                    self.registers[register] = value
                    if register == protocol.REG_SET_FUEL:
                        current_fuel = self.registers.get(protocol.REG_FUEL, 0)
                        self.registers[protocol.REG_FUEL] = (
                            current_fuel & ~protocol.FUEL_TYPE_MASK
                        ) | value

    def apply_delayed_writes(self):
        """Apply controller writes whose values settle after a delay."""
        self.registers.update(self.delayed_writes)
        self.delayed_writes.clear()

    async def read_gatt_char(self, characteristic):
        self.operations.append(("read", characteristic))
        if self.fail_reads.intersection(self.pending_registers):
            raise OSError("simulated register read failure")
        response = b"".join(
            self.registers.get(register, 0).to_bytes(2, "big", signed=False)
            for register in self.pending_registers[: protocol.WORDS_PER_REQUEST]
        )
        self.response_sizes.append(len(response))
        return bytearray(response)


class FumisAirProtocolTest(unittest.TestCase):
    def tearDown(self):
        protocol.COMMAND_DELAY_SECONDS = 0.1

    def _make_coordinator(
        self, peripheral, connection_results=None, events=None, model=const.SENSO_MODEL
    ):
        entry = types.SimpleNamespace(
            data={
                "address": "AA:BB:CC:DD:EE:FF",
                "name": "Test Senso",
                "model": model,
            },
            title="Test Senso",
        )
        hass = types.SimpleNamespace(async_create_task=asyncio.create_task)
        coordinator = coordinator_module.FumisAirCoordinator(hass, entry)
        coordinator.contexts.update(
            item.register
            for item in parameters_module.ENTITY_CATALOG[model].values()
            if item.display_bit is not None
            and item.polling_tier == "telemetry"
            and item.register
            in protocol.displayed_registers(
                peripheral.registers.get(protocol.DISP_VAL_USER, 0), model
            )
        )
        coordinator.settings_coordinator.contexts.update(
            (
                protocol.DESIRE_ROOM_TEMP,
                protocol.REG_ROOM_TEMP_EN,
                protocol.REG_LED_BRIGHTNESS,
                protocol.REG_SOUND_VOLUME,
                protocol.REG_FUEL,
                protocol.REG_FUEL_COUNT,
            )
        )
        coordinator.fast_coordinator.contexts.update(
            item.register
            for item in parameters_module.ENTITY_CATALOG[model].values()
            if item.polling_tier == "fast"
            and (
                item.display_bit is None
                or item.register
                in protocol.displayed_registers(
                    peripheral.registers.get(protocol.DISP_VAL_USER, 0), model
                )
            )
        )
        remaining_connections = list(connection_results or ())

        async def fake_connect(hass, address):
            if events is not None:
                events.append(("connect", address))
            result = remaining_connections.pop(0) if remaining_connections else peripheral
            if isinstance(result, BaseException):
                raise result
            result.is_connected = True
            return result

        async def fake_disconnect(client):
            client.is_connected = False
            if events is not None:
                events.append(("disconnect", client))

        coordinator_module.async_connect = fake_connect
        coordinator_module.async_disconnect = fake_disconnect
        protocol.COMMAND_DELAY_SECONDS = 0
        return coordinator

    def test_poll_retries_connection_failure_after_delay(self):
        asyncio.run(self._test_poll_retries_connection_failure_after_delay())

    async def _test_poll_retries_connection_failure_after_delay(self):
        peripheral = FakePeripheral(self._overview_registers())
        events = []
        coordinator = self._make_coordinator(
            peripheral,
            connection_results=[coordinator_module.BleakError("connection lost"), peripheral],
            events=events,
        )
        original_sleep = coordinator_module.asyncio.sleep

        async def fake_sleep(delay):
            if delay == coordinator_module.POLL_RETRY_DELAY_SECONDS:
                events.append(("sleep", delay, coordinator._transaction_lock.locked()))

        coordinator_module.asyncio.sleep = fake_sleep
        try:
            data = await coordinator._async_update_data()
        finally:
            coordinator_module.asyncio.sleep = original_sleep

        self.assertEqual(data[protocol.DISP_VAL_USER], 1)
        self.assertEqual(
            events,
            [
                ("connect", coordinator.address),
                ("sleep", coordinator_module.POLL_RETRY_DELAY_SECONDS, False),
                ("connect", coordinator.address),
            ],
        )

    def test_fast_and_telemetry_polls_update_separately_and_reuse_client(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            events = []
            coordinator = self._make_coordinator(peripheral, events=events)

            self.assertEqual(
                coordinator.update_interval,
                coordinator_module.timedelta(seconds=30),
            )
            self.assertEqual(
                coordinator.fast_coordinator.update_interval,
                coordinator_module.timedelta(seconds=1),
            )
            data = await coordinator._async_update_data()
            await coordinator.fast_coordinator.async_refresh()
            self.assertFalse(
                protocol.is_door_open(
                    coordinator.fast_coordinator.data[protocol.REG_STATUS]
                )
            )
            self.assertNotIn(protocol.REG_STATUS, data)
            address_writes = [
                operation
                for operation in peripheral.operations
                if operation[0] == "write"
                and operation[1] == protocol.CHARACTERISTIC_ADDRESS_UUID
            ]
            self.assertGreater(len(address_writes), 1)
            self.assertTrue(
                all(
                    len(operation[2]) == protocol.FRAME_SIZE
                    for operation in address_writes
                )
            )
            peripheral.registers[protocol.REG_STATUS] = 4
            peripheral.registers[protocol.VAL_TEMP_AIR] = 247
            data = await coordinator._async_update_data()
            await coordinator.fast_coordinator.async_refresh()
            self.assertTrue(
                protocol.is_door_open(
                    coordinator.fast_coordinator.data[protocol.REG_STATUS]
                )
            )
            self.assertEqual(data[protocol.VAL_TEMP_AIR], 247)
            self.assertEqual([event[0] for event in events], ["connect"])

            peripheral.is_connected = False
            await coordinator._async_update_data()
            await coordinator.fast_coordinator.async_refresh()
            self.assertTrue(
                protocol.is_door_open(
                    coordinator.fast_coordinator.data[protocol.REG_STATUS]
                )
            )
            self.assertEqual(
                [event[0] for event in events],
                ["connect", "disconnect", "connect"],
            )

            await coordinator.async_shutdown()
            self.assertFalse(peripheral.is_connected)
            self.assertEqual(
                [event[0] for event in events],
                ["connect", "disconnect", "connect", "disconnect"],
            )

        asyncio.run(run())

    def test_fast_entities_register_before_first_poll_and_keep_updating(self):
        async def run():
            fast_display_rows = [
                row
                for row in parameters_module.ENTITY_CATALOG[const.SENSO_MODEL].values()
                if row.polling_tier == "fast" and row.display_bit is not None
            ]
            display_mask = sum(1 << row.display_bit for row in fast_display_rows)
            registers = self._overview_registers()
            registers.update(
                {
                    protocol.DISP_VAL_USER: display_mask,
                    protocol.REG_STATUS: 4,
                    protocol.REG_ADVICE: 1,
                    10243: 180,
                    10245: 200,
                    10246: 35,
                }
            )
            peripheral = FakePeripheral(registers)
            coordinator = self._make_coordinator(peripheral)
            coordinator.fast_coordinator.contexts.clear()
            coordinator.display_mask = display_mask
            entry = types.SimpleNamespace(
                runtime_data=coordinator,
                data={
                    "address": coordinator.address,
                    "model": const.SENSO_MODEL,
                },
                title=coordinator.name,
            )
            fast_entities = []

            def register_entities(entities):
                for entity in entities:
                    if entity.coordinator is not coordinator.fast_coordinator:
                        continue
                    fast_entities.append(entity)
                    coordinator.fast_coordinator.contexts.add(entity.context)
                    if isinstance(entity, sensor_module.FumisAirSensor):
                        self.assertIsNone(entity.native_value)
                    else:
                        self.assertFalse(entity.available)
                        self.assertFalse(entity.is_on)

            await sensor_module.async_setup_entry(
                object(), entry, register_entities
            )
            await binary_sensor_module.async_setup_entry(
                object(), entry, register_entities
            )

            self.assertEqual(
                coordinator.fast_coordinator.contexts,
                {10497, 10245, 10246, 10503, 10243},
            )
            await coordinator.fast_coordinator.async_refresh()

            self.assertEqual(
                self._read_frames(peripheral),
                [(10497, 10245, 10246, 10503, 10243)],
            )
            entities_by_key = {
                entity.metadata.entity_key: entity for entity in fast_entities
            }
            self.assertEqual(entities_by_key["status"].native_value, "Door open")
            self.assertEqual(entities_by_key["advice"].native_value, "Add wood")
            self.assertEqual(
                entities_by_key["chamber_temperature"].native_value, 180
            )
            self.assertEqual(entities_by_key["heat_temperature"].native_value, 200)
            self.assertEqual(entities_by_key["draft"].native_value, 35)
            self.assertTrue(entities_by_key["door_open"].is_on)

        asyncio.run(run())

    @staticmethod
    def _read_frames(peripheral):
        return [
            _decode_request(payload)
            for action, characteristic, *args in peripheral.operations
            if action == "write" and characteristic == protocol.CHARACTERISTIC_ADDRESS_UUID
            for payload in (args[0],)
        ]

    def test_settings_poll_is_separate_and_uses_five_minute_interval(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)
            self.assertEqual(
                coordinator.update_interval,
                coordinator_module.timedelta(seconds=30),
            )
            self.assertEqual(
                coordinator.fast_coordinator.update_interval,
                coordinator_module.timedelta(seconds=1),
            )
            self.assertEqual(
                coordinator.settings_coordinator.update_interval,
                coordinator_module.timedelta(seconds=300),
            )
            data = await coordinator._async_update_data()
            telemetry_frames = self._read_frames(peripheral)
            self.assertTrue(telemetry_frames)
            self.assertEqual(data[protocol.VAL_TEMP_AIR], 234)
            self.assertEqual(coordinator.display_mask, 1)
            self.assertTrue(
                all(
                    register not in frame
                    for frame in telemetry_frames
                    for register in (
                        *USER_SETTING_REGISTERS,
                        protocol.DESIRE_ROOM_TEMP,
                        protocol.REG_MAX_HEAT_CAP_TEMP,
                        protocol.REG_POWER,
                    )
                )
            )
            fast_registers = {
                item.register
                for item in parameters_module.ENTITY_CATALOG[const.SENSO_MODEL].values()
                if item.polling_tier == "fast"
            }
            self.assertTrue(
                all(register not in frame for frame in telemetry_frames for register in fast_registers)
            )
            peripheral.operations.clear()
            await coordinator.settings_coordinator.async_refresh()
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_FUEL_COUNT], 3
            )
            self.assertIn(protocol.DESIRE_ROOM_TEMP, coordinator.settings_coordinator.data)
            self.assertIn(protocol.MIN_LIMIT_ROOM_TEMP, coordinator.settings_coordinator.data)
            self.assertNotIn(protocol.DISP_VAL_USER, coordinator.settings_coordinator.data)
            requested = {
                register
                for frame in self._read_frames(peripheral)
                for register in frame
            }
            self.assertTrue(
                set(USER_SETTING_REGISTERS).issubset(requested)
            )
            self.assertIn(protocol.DESIRE_ROOM_TEMP, requested)
            self.assertNotIn(protocol.DISP_VAL_USER, requested)

            coordinator.settings_coordinator.contexts.clear()
            coordinator.settings_coordinator.contexts.add(
                protocol.REG_LED_BRIGHTNESS
            )
            peripheral.operations.clear()
            await coordinator.settings_coordinator.async_refresh()
            self.assertEqual(
                coordinator.settings_coordinator.data, {protocol.REG_LED_BRIGHTNESS: 45}
            )
            self.assertEqual(
                set().union(*map(set, self._read_frames(peripheral))),
                {protocol.REG_LED_BRIGHTNESS},
            )

            peripheral.operations.clear()
            peripheral.registers[protocol.REG_FUEL_COUNT] = 1
            coordinator.settings_coordinator.contexts.clear()
            coordinator.settings_coordinator.contexts.add(protocol.REG_FUEL_COUNT)
            await coordinator._async_update_data()
            telemetry_frames = self._read_frames(peripheral)
            self.assertEqual(len(telemetry_frames), 1)
            self.assertTrue(all(protocol.REG_FUEL_COUNT not in frame for frame in telemetry_frames))
            await coordinator.settings_coordinator.async_refresh()
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_FUEL_COUNT], 1
            )

        asyncio.run(run())

    def test_fast_profile_values_fit_one_frame_for_both_models(self):
        async def run():
            for model in (const.SENSO_MODEL, const.FUMIS_AIR_MODEL):
                catalog = parameters_module.ENTITY_CATALOG[model]
                fast_display = tuple(
                    item
                    for item in catalog.values()
                    if item.polling_tier == "fast" and item.display_bit is not None
                )
                display_mask = sum(1 << item.display_bit for item in fast_display)
                registers = self._overview_registers() | {
                    protocol.DISP_VAL_USER: display_mask,
                    **{item.register: index + 10 for index, item in enumerate(fast_display)},
                }
                peripheral = FakePeripheral(registers)
                coordinator = self._make_coordinator(peripheral, model=model)
                await coordinator._async_update_data()
                normal_registers = {
                    register
                    for frame in self._read_frames(peripheral)
                    for register in frame
                }
                expected = tuple(
                    dict.fromkeys(
                        item.register
                        for item in catalog.values()
                        if item.polling_tier == "fast"
                    )
                )
                self.assertLessEqual(len(expected), protocol.WORDS_PER_REQUEST)
                self.assertTrue(set(expected).isdisjoint(normal_registers))

                peripheral.operations.clear()
                data = await coordinator.fast_coordinator._async_update_data()
                frames = self._read_frames(peripheral)
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0][: len(expected)], expected)
                self.assertTrue(set(expected).issubset(data))

        asyncio.run(run())

    def test_parameter_reads_are_opt_in_and_failures_are_isolated(self):
        async def run():
            parameter = parameters_module.APK_PARAMETERS[const.SENSO_MODEL]["000-001"]
            peripheral = FakePeripheral({parameter.register: 0xFFFF})
            coordinator = self._make_coordinator(peripheral)
            coordinator.settings_coordinator.contexts.clear()

            self.assertEqual(
                await coordinator.settings_coordinator._async_update_data(), {}
            )
            self.assertEqual(peripheral.operations, [])

            coordinator.settings_coordinator.contexts.add(parameter.register)
            data = await coordinator.settings_coordinator._async_update_data()
            self.assertEqual(data, {parameter.register: 0xFFFF})
            self.assertEqual(len(self._read_frames(peripheral)), 1)
            self.assertEqual(
                self._read_frames(peripheral)[0][0], parameter.register
            )

            shared = SENSO_CATALOG["led_brightness"]
            shared_peripheral = FakePeripheral({protocol.REG_LED_BRIGHTNESS: 42})
            shared_coordinator = self._make_coordinator(shared_peripheral)
            shared_coordinator.settings_coordinator.contexts.clear()
            shared_coordinator.settings_coordinator.contexts.add(shared.register)
            data = await shared_coordinator.settings_coordinator._async_update_data()
            self.assertEqual(data, {protocol.REG_LED_BRIGHTNESS: 42})
            self.assertEqual(len(self._read_frames(shared_peripheral)), 1)

            failing = FakePeripheral(
                {protocol.REG_LED_BRIGHTNESS: 37, parameter.register: 0xFFFF},
                fail_reads=(parameter.register,),
            )
            failing_coordinator = self._make_coordinator(failing)
            failing_coordinator.settings_coordinator.contexts.clear()
            failing_coordinator.settings_coordinator.contexts.update(
                (protocol.REG_LED_BRIGHTNESS, parameter.register)
            )
            data = await failing_coordinator.settings_coordinator._async_update_data()
            self.assertEqual(data, {protocol.REG_LED_BRIGHTNESS: 37})

        asyncio.run(run())

    def test_nominal_power_write_uses_raw_tenths_and_is_senso_only(self):
        async def run():
            peripheral = FakePeripheral({protocol.REG_NOMINAL_POWER: 70})
            coordinator = self._make_coordinator(peripheral)
            coordinator.settings_coordinator.contexts.clear()
            self.assertEqual(
                await coordinator.settings_coordinator._async_update_data(), {}
            )
            self.assertEqual(peripheral.operations, [])
            coordinator.settings_coordinator.contexts.add(protocol.REG_NOMINAL_POWER)
            self.assertEqual(
                await coordinator.settings_coordinator._async_update_data(),
                {protocol.REG_NOMINAL_POWER: 70},
            )
            operation_start = len(peripheral.operations)
            await coordinator.async_set_number(protocol.REG_NOMINAL_POWER, 7.3)
            self.assertEqual(peripheral.registers[protocol.REG_NOMINAL_POWER], 73)
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_NOMINAL_POWER],
                73,
            )
            write_operations = peripheral.operations[operation_start:]
            address_frame, value_frame = protocol.encode_write(
                protocol.REG_NOMINAL_POWER, 73
            )
            read_frame = protocol.encode_read_request((protocol.REG_NOMINAL_POWER,))
            self.assertEqual(
                write_operations,
                [
                    ("write", protocol.CHARACTERISTIC_ADDRESS_UUID, read_frame, True),
                    ("read", protocol.CHARACTERISTIC_DATA_UUID),
                    ("write", protocol.CHARACTERISTIC_ADDRESS_UUID, address_frame, True),
                    ("write", protocol.CHARACTERISTIC_DATA_UUID, value_frame, True),
                    ("write", protocol.CHARACTERISTIC_ADDRESS_UUID, read_frame, True),
                    ("read", protocol.CHARACTERISTIC_DATA_UUID),
                ],
            )

            operation_count = len(peripheral.operations)
            for value in (7.35, 50.1):
                with self.assertRaises(FakeHomeAssistantError):
                    await coordinator.async_set_number(protocol.REG_NOMINAL_POWER, value)
            self.assertEqual(len(peripheral.operations), operation_count)

            unsupported = FakePeripheral({protocol.REG_NOMINAL_POWER: 501})
            coordinator = self._make_coordinator(unsupported)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_number(protocol.REG_NOMINAL_POWER, 7.3)
            self.assertEqual(
                unsupported.operations,
                [
                    (
                        "write",
                        protocol.CHARACTERISTIC_ADDRESS_UUID,
                        protocol.encode_read_request((protocol.REG_NOMINAL_POWER,)),
                        True,
                    ),
                    ("read", protocol.CHARACTERISTIC_DATA_UUID),
                ],
            )

            fumis = FakePeripheral({protocol.REG_NOMINAL_POWER: 70})
            coordinator = self._make_coordinator(fumis, model=const.FUMIS_AIR_MODEL)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_number(protocol.REG_NOMINAL_POWER, 7.3)
            self.assertEqual(fumis.operations, [])

        asyncio.run(run())

    def test_firmware_read_is_setup_only_and_optional(self):
        async def run():
            high, low = protocol.FIRMWARE_VERSION_REGISTERS
            registers = self._overview_registers() | {high: 0x8402, low: 3 << 7 | 5}
            for fail in (False, True):
                peripheral = FakePeripheral(registers, fail_reads=(high,) if fail else ())
                coordinator = self._make_coordinator(peripheral)
                await coordinator.async_refresh()
                await coordinator.async_read_firmware_version()
                await coordinator.async_refresh()
                self.assertEqual(coordinator.firmware_version, None if fail else "2.3.5-a.4")
                self.assertEqual(sum(high in frame for frame in self._read_frames(peripheral)), 1)
                entry = coordinator.config_entry
                entry.runtime_data = coordinator
                sensors = []
                await sensor_module.async_setup_entry(object(), entry, sensors.extend)
                self.assertEqual(
                    sensors[0]._attr_device_info.get("sw_version"),
                    coordinator.firmware_version,
                )

        asyncio.run(run())

    def test_fumis_profile_exposes_read_only_telemetry_and_correct_door(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers() | {
                protocol.DISP_VAL_USER: (1 << 2) | (1 << 9),
                protocol.REG_STATUS: 4,
                protocol.VAL_TEMP_CHAMBER: 203,
                protocol.ERR_SENSOR: (1 << 8) | (1 << 15),
            })
            coordinator = self._make_coordinator(peripheral, model=const.FUMIS_AIR_MODEL)
            coordinator.contexts.add(protocol.ERR_SENSOR)
            await coordinator.async_refresh()
            await coordinator.fast_coordinator.async_refresh()
            entry = coordinator.config_entry
            entry.runtime_data = coordinator
            sensors, binary_sensors, numbers, switches = [], [], [], []
            await sensor_module.async_setup_entry(object(), entry, sensors.extend)
            await binary_sensor_module.async_setup_entry(object(), entry, binary_sensors.extend)
            await number_module.async_setup_entry(object(), entry, numbers.extend)
            await switch_module.async_setup_entry(object(), entry, switches.extend)
            by_key = {entity.entity_description.key: entity for entity in sensors + binary_sensors}
            self.assertEqual(len(numbers), 1)
            self.assertEqual(numbers[0]._attr_name, "Manual air intake")
            self.assertEqual(len(switches), 1)
            self.assertEqual(switches[0]._attr_name, "Automatic air intake")
            self.assertNotIn("advice", by_key)
            self.assertEqual(by_key["flue_gas_temperature"].native_value, 203)
            self.assertEqual(by_key["status"].native_value, "Cooling")
            self.assertFalse(by_key["door_open"].is_on)
            fault = by_key["sensor_error"]
            self.assertTrue(fault.is_on)
            self.assertEqual(fault.extra_state_attributes["active_errors"], [
                "Input 1 sensor short", "Unknown error bit 15",
            ])
            for raw, available in ((5, True), (9, False)):
                peripheral.registers[protocol.REG_STATUS] = raw
                await coordinator.fast_coordinator.async_refresh()
                self.assertEqual(by_key["door_open"].available, available)
                if available:
                    self.assertTrue(by_key["door_open"].is_on)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_number(protocol.REG_LED_BRIGHTNESS, 1)
            requested = {register for frame in self._read_frames(peripheral) for register in frame}
            self.assertTrue(requested.isdisjoint((*USER_SETTING_REGISTERS, protocol.REG_ADVICE)))

        asyncio.run(run())

    def test_firmware_and_senso_fault_decoding(self):
        self.assertEqual(protocol.decode_firmware_version(2, 3 << 7 | 5), "2.3.5")
        self.assertEqual(protocol.decode_firmware_version(0x4402, 3 << 7 | 5), "2.3.5-b.4")
        self.assertIsNone(protocol.decode_firmware_version(0, 0))
        self.assertIsNone(protocol.is_door_open(5, const.SENSO_MODEL))
        self.assertEqual(protocol.error_details(protocol.ERR_SENSOR, (1 << 3) | (1 << 10)), [
            "Door sensor open", "Room sensor short",
        ])

    def test_enabled_advanced_entities_request_only_their_registers(self):
        async def run():
            peripheral = FakePeripheral(
                {
                    **self._overview_registers(),
                    advanced.IN_CONFIG_5: 10,
                    advanced.IN_CONFIG_6: 8,
                    advanced.VAL_INPUT_5: 99,
                    advanced.VAL_INPUT_6: 42,
                }
            )
            coordinator = self._make_coordinator(peripheral)
            coordinator.contexts.clear()
            coordinator.settings_coordinator.contexts.clear()
            coordinator.fast_coordinator.contexts.clear()
            coordinator.contexts.update(
                (
                    advanced.VAL_TEMP_BOARD,
                    advanced.VAL_INPUT_5,
                    advanced.VAL_INPUT_6,
                )
            )
            coordinator.settings_coordinator.contexts.add(protocol.REG_POWER)
            self.assertEqual(
                coordinator.update_interval,
                coordinator_module.timedelta(seconds=30),
            )

            data = await coordinator._async_update_data()
            await coordinator.settings_coordinator.async_refresh()

            self.assertEqual(data[advanced.IN_CONFIG_5], 10)
            self.assertEqual(data[advanced.IN_CONFIG_6], 8)
            self.assertEqual(data[advanced.VAL_INPUT_6], 42)
            self.assertNotIn(advanced.VAL_INPUT_5, data)
            self.assertNotIn(advanced.IN_CONFIG_1, data)
            self.assertNotIn(protocol.REG_MAX_HEAT_CAP_TEMP, data)
            self.assertEqual(coordinator.settings_coordinator.data[protocol.REG_POWER], 0x10)
            requested_registers = set().union(*map(set, self._read_frames(peripheral)))
            self.assertIn(advanced.VAL_TEMP_BOARD, requested_registers)
            self.assertIn(advanced.IN_CONFIG_5, requested_registers)
            self.assertIn(advanced.IN_CONFIG_6, requested_registers)
            self.assertIn(protocol.REG_POWER, requested_registers)
            self.assertNotIn(advanced.VAL_VOLTAGE_BATTERY, requested_registers)
            self.assertTrue(set(range(10257, 10262)).isdisjoint(requested_registers))
            self.assertNotIn(0x0DEE, requested_registers)
            self.assertFalse(
                any(
                    operation[0] == "write"
                    and operation[1] == protocol.CHARACTERISTIC_DATA_UUID
                    for operation in peripheral.operations
                )
            )

        asyncio.run(run())

    def test_poll_retries_gatt_failure_after_cleanup(self):
        asyncio.run(self._test_poll_retries_gatt_failure_after_cleanup())

    async def _test_poll_retries_gatt_failure_after_cleanup(self):
        failed = FakePeripheral(
            self._overview_registers(), fail_reads=(protocol.REG_STATUS,)
        )
        recovered = FakePeripheral(self._overview_registers())
        events = []
        coordinator = self._make_coordinator(
            recovered,
            connection_results=[failed, recovered],
            events=events,
        )
        original_sleep = coordinator_module.asyncio.sleep

        async def fake_sleep(delay):
            if delay == coordinator_module.POLL_RETRY_DELAY_SECONDS:
                events.append(("sleep", delay, coordinator._transaction_lock.locked()))

        coordinator_module.asyncio.sleep = fake_sleep
        try:
            data = await coordinator.fast_coordinator._async_update_data()
        finally:
            coordinator_module.asyncio.sleep = original_sleep

        self.assertEqual(data[protocol.REG_STATUS], 2)
        self.assertEqual(
            events,
            [
                ("connect", coordinator.address),
                ("disconnect", failed),
                (
                    "sleep",
                    coordinator_module.POLL_RETRY_DELAY_SECONDS,
                    False,
                ),
                ("connect", coordinator.address),
            ],
        )

    def test_overview_retry_keeps_three_second_delay(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            events = []
            coordinator = self._make_coordinator(
                peripheral,
                connection_results=[
                    coordinator_module.BleakError("connection lost"),
                    peripheral,
                ],
                events=events,
            )
            original_sleep = coordinator_module.asyncio.sleep

            async def fast_sleep(delay):
                if delay == coordinator_module.POLL_RETRY_DELAY_SECONDS:
                    events.append(
                        ("sleep", delay, coordinator._transaction_lock.locked())
                    )
                else:
                    await original_sleep(0)

            coordinator_module.asyncio.sleep = fast_sleep
            try:
                await coordinator._async_update_data()
            finally:
                coordinator_module.asyncio.sleep = original_sleep

            self.assertEqual(
                events[:3],
                [
                    ("connect", coordinator.address),
                    ("sleep", 3, False),
                    ("connect", coordinator.address),
                ],
            )

        asyncio.run(run())

    def test_poll_fails_after_two_transport_errors_without_retrying_protocol_data(self):
        asyncio.run(
            self._test_poll_fails_after_two_transport_errors_without_retrying_protocol_data()
        )

    async def _test_poll_fails_after_two_transport_errors_without_retrying_protocol_data(self):
        first = FakePeripheral(
            self._overview_registers(), fail_reads=(protocol.REG_STATUS,)
        )
        second = FakePeripheral(
            self._overview_registers(), fail_reads=(protocol.REG_STATUS,)
        )
        events = []
        coordinator = self._make_coordinator(
            second,
            connection_results=[first, second],
            events=events,
        )
        original_sleep = coordinator_module.asyncio.sleep
        sleep_delays = []

        async def fake_sleep(delay):
            if delay == coordinator_module.POLL_RETRY_DELAY_SECONDS:
                sleep_delays.append(delay)

        coordinator_module.asyncio.sleep = fake_sleep
        try:
            with self.assertRaises(coordinator_module.UpdateFailed):
                await coordinator.fast_coordinator._async_update_data()
        finally:
            coordinator_module.asyncio.sleep = original_sleep

        self.assertEqual(
            sleep_delays, [coordinator_module.POLL_RETRY_DELAY_SECONDS]
        )
        self.assertEqual(
            events,
            [
                ("connect", coordinator.address),
                ("disconnect", first),
                ("connect", coordinator.address),
                ("disconnect", second),
            ],
        )

        invalid_registers = self._overview_registers()
        invalid_registers[protocol.REG_STATUS] = 99
        invalid_registers[protocol.DISP_VAL_USER] |= 1 << 2
        invalid_registers[protocol.VAL_TEMP_CHAMBER] = 987
        invalid = FakePeripheral(invalid_registers)
        events = []
        coordinator = self._make_coordinator(invalid, events=events)
        coordinator.fast_coordinator.contexts.add(protocol.VAL_TEMP_CHAMBER)
        coordinator_module.asyncio.sleep = fake_sleep
        try:
            data = await coordinator.fast_coordinator._async_update_data()
        finally:
            coordinator_module.asyncio.sleep = original_sleep
        self.assertEqual(sleep_delays, [coordinator_module.POLL_RETRY_DELAY_SECONDS])
        self.assertNotIn(protocol.REG_STATUS, data)
        self.assertIn(protocol.REG_ADVICE, data)
        self.assertEqual(data[protocol.VAL_TEMP_CHAMBER], 987)
        self.assertEqual(
            events,
            [("connect", coordinator.address)],
        )

    def test_unknown_advice_does_not_block_valid_door_status(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.REG_STATUS] = 4
            registers[protocol.REG_ADVICE] = 99
            peripheral = FakePeripheral(registers)
            events = []
            coordinator = self._make_coordinator(peripheral, events=events)

            await coordinator.fast_coordinator.async_refresh()

            self.assertEqual(coordinator.fast_coordinator.data[protocol.REG_STATUS], 4)
            self.assertEqual(coordinator.fast_coordinator.data[protocol.REG_ADVICE], 99)
            self.assertTrue(
                protocol.is_door_open(
                    coordinator.fast_coordinator.data[protocol.REG_STATUS]
                )
            )
            self.assertEqual([event[0] for event in events], ["connect"])

        asyncio.run(run())

    def test_only_enabled_entity_contexts_are_requested(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)
            coordinator.contexts.clear()
            coordinator.fast_coordinator.contexts.clear()
            coordinator.contexts.add(advanced.VAL_TEMP_BOARD)
            coordinator.display_mask = 1

            data = await coordinator._async_update_data()

            self.assertEqual(data, {advanced.VAL_TEMP_BOARD: 0})
            requested_registers = set().union(*map(set, self._read_frames(peripheral)))
            self.assertEqual(requested_registers, {advanced.VAL_TEMP_BOARD})

        asyncio.run(run())

    def test_startup_without_contexts_then_poll_door_only_or_no_entities(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)
            coordinator.contexts.clear()
            coordinator.fast_coordinator.contexts.clear()

            data = await coordinator._async_update_data()
            self.assertEqual(data[protocol.DISP_VAL_USER], 1)
            self.assertNotIn(protocol.VAL_TEMP_AIR, data)
            peripheral.operations.clear()

            coordinator.fast_coordinator.contexts.add(protocol.REG_STATUS)
            await coordinator.fast_coordinator.async_refresh()
            self.assertEqual(
                coordinator.fast_coordinator.data, {protocol.REG_STATUS: 2}
            )
            self.assertEqual(self._read_frames(peripheral), [(protocol.REG_STATUS,) * 5])
            peripheral.operations.clear()

            coordinator.fast_coordinator.contexts.clear()
            self.assertEqual(await coordinator._async_update_data(), {})
            self.assertEqual(peripheral.operations, [])

        asyncio.run(run())

    def test_frame_and_coordinator_stage_timings_are_logged(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)
            with self.assertLogs(level="DEBUG") as log:
                await coordinator._async_update_data()
            output = "\n".join(log.output)
            self.assertIn("BLE transaction lock wait", output)
            self.assertIn("BLE connection acquisition", output)
            self.assertIn("FFF1 write", output)
            self.assertIn("settle", output)
            self.assertIn("FFF2 read", output)

        asyncio.run(run())

    def test_stalled_read_frame_times_out_and_releases_connection(self):
        async def run():
            class StalledPeripheral(FakePeripheral):
                async def read_gatt_char(self, characteristic):
                    self.operations.append(("read", characteristic))
                    await asyncio.Event().wait()

            stalled = StalledPeripheral(self._overview_registers())
            recovered = FakePeripheral(self._overview_registers())
            events = []
            coordinator = self._make_coordinator(
                recovered,
                connection_results=[stalled, recovered],
                events=events,
            )
            old_timeout = coordinator_module.POLL_FRAME_TIMEOUT_SECONDS
            original_sleep = coordinator_module.asyncio.sleep
            coordinator_module.POLL_FRAME_TIMEOUT_SECONDS = 0.01

            async def fast_retry_sleep(delay):
                if delay == coordinator_module.POLL_RETRY_DELAY_SECONDS:
                    events.append(
                        ("sleep", delay, coordinator._transaction_lock.locked())
                    )
                else:
                    await original_sleep(0)

            coordinator_module.asyncio.sleep = fast_retry_sleep
            try:
                data = await coordinator._async_update_data()
            finally:
                coordinator_module.POLL_FRAME_TIMEOUT_SECONDS = old_timeout
                coordinator_module.asyncio.sleep = original_sleep

            self.assertEqual(data[protocol.DISP_VAL_USER], 1)
            self.assertEqual(
                events,
                [
                    ("connect", coordinator.address),
                    ("disconnect", stalled),
                    (
                        "sleep",
                        coordinator_module.POLL_RETRY_DELAY_SECONDS,
                        False,
                    ),
                    ("connect", coordinator.address),
                ],
            )

        asyncio.run(run())

    @staticmethod
    def _overview_registers():
        registers = {register: 0 for register in OVERVIEW_REGISTERS}
        registers.update(
            {
                protocol.REG_STATUS: 2,
                protocol.REG_ADVICE: 1,
                protocol.REG_POWER: 0x10,
                protocol.DISP_VAL_USER: 1,
                protocol.VAL_TEMP_AIR: 234,
                protocol.DESIRE_ROOM_TEMP: 220,
                protocol.MIN_LIMIT_ROOM_TEMP: 180,
                protocol.MAX_LIMIT_ROOM_TEMP: 300,
                protocol.REG_ROOM_TEMP_EN: 5,
                protocol.REG_LED_BRIGHTNESS: 45,
                protocol.REG_SOUND_VOLUME: 30,
                protocol.REG_FUEL: 2,
                protocol.REG_FUEL_COUNT: 3,
            }
        )
        return registers

    def test_status_codes_and_boolean_error_helpers(self):
        self.assertEqual(protocol.decode_status(0), "Standby")
        self.assertEqual(protocol.decode_status(1), "Ignition")
        self.assertEqual(protocol.decode_status(2), "Burning")
        self.assertEqual(protocol.decode_status(3), "Embers")
        self.assertEqual(protocol.decode_status(4), "Door open")
        self.assertEqual(protocol.decode_status(99), "Unknown (99)")
        self.assertIsNone(protocol.decode_status(None))
        self.assertEqual(protocol.decode_advice(0), "No advice")
        self.assertEqual(protocol.decode_advice(1), "Add wood")
        self.assertEqual(protocol.decode_advice(2), "Do not add wood")
        self.assertEqual(protocol.decode_advice(3), "Do not add pellets")
        self.assertEqual(protocol.decode_advice(99), "Unknown (99)")
        self.assertIsNone(protocol.decode_advice(None))
        self.assertTrue(protocol.is_door_open(4))
        self.assertFalse(protocol.is_door_open(2))
        self.assertIsNone(protocol.is_door_open(None))
        self.assertFalse(protocol.has_error(0))
        self.assertTrue(protocol.has_error(4))
        self.assertIsNone(protocol.has_error(None))
        self.assertEqual(protocol.fuel_type_options(1), ("Soft wood",))
        self.assertEqual(
            protocol.fuel_type_options(3),
            ("Soft wood", "Hard wood", "Pellets"),
        )
        self.assertEqual(
            protocol.fuel_type_options(5),
            ("Soft wood", "Hard wood", "Pellets"),
        )
        self.assertEqual(protocol.fuel_type_options(0), ())
        self.assertEqual(protocol.fuel_type_options(6), ())
        self.assertEqual(protocol.selected_fuel_type(2, 3), "Hard wood")
        self.assertEqual(protocol.selected_fuel_type(3, 3), "Pellets")
        self.assertIsNone(protocol.selected_fuel_type(4, 5))

    def test_air_intake_profiles_encode_and_decode_app_values(self):
        self.assertEqual(protocol.fan_register_value(0), 20)
        self.assertEqual(protocol.fan_register_value(50), 60)
        self.assertEqual(protocol.fan_register_value(100), 100)
        self.assertEqual(protocol.fan_register_value(0, automatic=True), 0x114)
        self.assertEqual(
            protocol.fan_register_value(75, const.FUMIS_AIR_MODEL), 75
        )
        self.assertEqual(
            protocol.fan_register_value(0, const.FUMIS_AIR_MODEL, automatic=True),
            0x100,
        )
        self.assertEqual(protocol.fan_automatic(0x114), True)
        self.assertEqual(protocol.fan_automatic(60), False)
        self.assertEqual(protocol.fan_automatic(0x100), True)
        self.assertEqual(protocol.fan_automatic(75, const.FUMIS_AIR_MODEL), False)
        self.assertIsNone(protocol.fan_automatic(None))
        self.assertIsNone(protocol.fan_automatic(0x200))
        self.assertIsNone(protocol.fan_automatic(101, const.FUMIS_AIR_MODEL))
        self.assertEqual(protocol.fan_position_percent(20), 0)
        self.assertEqual(protocol.fan_position_percent(60), 50)
        self.assertEqual(protocol.fan_position_percent(100), 100)
        self.assertIsNone(protocol.fan_position_percent(19))

    def test_air_intake_polling_reads_only_enabled_control_dependencies(self):
        async def run():
            for model in (const.SENSO_MODEL, const.FUMIS_AIR_MODEL):
                disabled_peripheral = FakePeripheral(
                    self._overview_registers()
                    | {
                        protocol.REG_FAN: 0x114,
                        protocol.VAL_REG_POS: 60,
                    }
                )
                disabled = self._make_coordinator(
                    disabled_peripheral, model=model
                )
                disabled.contexts.clear()
                disabled.settings_coordinator.contexts.clear()
                disabled.fast_coordinator.contexts.clear()
                disabled.fast_coordinator.contexts.add(protocol.REG_STATUS)
                await disabled.async_refresh()
                await disabled.settings_coordinator.async_refresh()
                disabled_registers = {
                    register
                    for frame in self._read_frames(disabled_peripheral)
                    for register in frame
                }
                self.assertNotIn(protocol.REG_FAN, disabled_registers)
                self.assertNotIn(protocol.VAL_REG_POS, disabled_registers)

            for model, fan_value, position, expected in (
                (const.SENSO_MODEL, 0x114, 60, 0),
                (const.FUMIS_AIR_MODEL, 75, 0, 75),
            ):
                with self.subTest(model=model):
                    registers = self._overview_registers() | {
                        protocol.REG_FAN: fan_value,
                        protocol.VAL_REG_POS: position,
                    }
                    peripheral = FakePeripheral(registers)
                    coordinator = self._make_coordinator(
                        peripheral, model=model
                    )
                    coordinator.contexts.clear()
                    coordinator.fast_coordinator.contexts.clear()
                    coordinator.settings_coordinator.contexts.clear()
                    coordinator.settings_coordinator.contexts.add(protocol.REG_FAN)
                    await coordinator.settings_coordinator.async_refresh()
                    await coordinator.async_refresh()
                    entry = coordinator.config_entry
                    entry.runtime_data = coordinator
                    numbers, switches = [], []
                    await number_module.async_setup_entry(
                        object(), entry, numbers.extend
                    )
                    await switch_module.async_setup_entry(
                        object(), entry, switches.extend
                    )
                    manual = next(
                        entity
                        for entity in numbers
                        if entity._attr_unique_id.endswith("_manual_air_intake")
                    )
                    automatic = next(
                        entity
                        for entity in switches
                        if entity._attr_unique_id.endswith("_automatic_air_intake")
                    )

                    self.assertTrue(manual.available)
                    self.assertEqual(manual.native_value, expected)
                    self.assertTrue(automatic.available)
                    self.assertEqual(automatic.is_on, model == const.SENSO_MODEL)
                    frames = self._read_frames(peripheral)
                    self.assertTrue(any(protocol.REG_FAN in frame for frame in frames))
                    self.assertFalse(any(protocol.VAL_REG_POS in frame for frame in frames))
                    peripheral.registers[protocol.REG_FAN] = (
                        20 if model == const.SENSO_MODEL else 45
                    )
                    peripheral.registers[protocol.VAL_REG_POS] = 80
                    await coordinator.async_refresh()
                    self.assertEqual(automatic.is_on, model == const.SENSO_MODEL)
                    self.assertEqual(manual.native_value, expected)
                    await coordinator.settings_coordinator.async_refresh()
                    self.assertFalse(automatic.is_on)
                    self.assertEqual(
                        manual.native_value,
                        expected if model == const.SENSO_MODEL else 45,
                    )

        asyncio.run(run())

    def test_air_intake_writes_restore_automatic_and_manual_state(self):
        async def run():
            for model, initial, position, manual_value, automatic_value, restored in (
                (const.SENSO_MODEL, 20, 60, 80, 0x114, 20),
                (const.FUMIS_AIR_MODEL, 15, 0, 75, 0x100, 0),
            ):
                with self.subTest(model=model):
                    peripheral = FakePeripheral(
                        self._overview_registers()
                        | {
                            protocol.REG_FAN: initial,
                            protocol.VAL_REG_POS: position,
                        }
                    )
                    coordinator = self._make_coordinator(
                        peripheral, model=model
                    )
                    await coordinator.async_set_number(protocol.REG_FAN, 75)
                    self.assertEqual(peripheral.registers[protocol.REG_FAN], manual_value)
                    self.assertEqual(
                        coordinator.settings_coordinator.data[protocol.REG_FAN],
                        manual_value,
                    )
                    await coordinator.async_set_air_automatic(True)
                    self.assertEqual(
                        peripheral.registers[protocol.REG_FAN], automatic_value
                    )
                    await coordinator.async_set_air_automatic(False)
                    self.assertEqual(peripheral.registers[protocol.REG_FAN], restored)

            invalid = FakePeripheral(
                self._overview_registers() | {protocol.REG_FAN: 0x200}
            )
            coordinator = self._make_coordinator(invalid)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_number(protocol.REG_FAN, 50)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_air_automatic(True)
            self.assertFalse(
                any(
                    operation[0] == "write"
                    and operation[1] == protocol.CHARACTERISTIC_DATA_UUID
                    for operation in invalid.operations
                )
            )

            automatic_sentinel = FakePeripheral(
                self._overview_registers()
                | {protocol.REG_FAN: 0x100, protocol.VAL_REG_POS: 80}
            )
            coordinator = self._make_coordinator(automatic_sentinel)
            await coordinator.async_set_air_automatic(False)
            self.assertEqual(automatic_sentinel.registers[protocol.REG_FAN], 20)
            self.assertNotIn(
                protocol.VAL_REG_POS,
                set().union(*map(set, self._read_frames(automatic_sentinel))),
            )
            for value in (100.1, 25.5, float("inf")):
                with self.subTest(value=value), self.assertRaises(
                    FakeHomeAssistantError
                ):
                    await coordinator.async_set_number(protocol.REG_FAN, value)

        asyncio.run(run())

    def test_coordinator_polls_user_settings_without_advanced_mode(self):
        asyncio.run(self._test_coordinator_polls_user_settings())

    def test_heat_cap_input_is_packed_with_enabled_display_and_diagnostic_data(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.DISP_VAL_USER] = 1 << 4
            registers[protocol.VAL_TEMP_HEAT] = 123
            registers[advanced.VAL_INPUT_2] = 1234

            for advanced_enabled in (False, True):
                peripheral = FakePeripheral(registers)
                coordinator = self._make_coordinator(peripheral)
                if advanced_enabled:
                    coordinator.contexts.add(advanced.VAL_INPUT_2)

                data = await coordinator._async_update_data()
                fast_data = await coordinator.fast_coordinator._async_update_data()

                self.assertEqual(data[advanced.VAL_INPUT_2], 1234)
                self.assertEqual(fast_data[protocol.VAL_TEMP_HEAT], 123)
                self.assertEqual(fast_data[advanced.VAL_INPUT_2], 1234)
                input_read_requests = [
                    operation
                    for operation in peripheral.operations
                    if operation[0] == "write"
                    and operation[1] == protocol.CHARACTERISTIC_ADDRESS_UUID
                    and advanced.VAL_INPUT_2
                        in _decode_request(operation[2])
                ]
                self.assertEqual(len(input_read_requests), 1)

            failing = FakePeripheral(
                registers, fail_reads=(advanced.VAL_INPUT_2,)
            )
            coordinator = self._make_coordinator(failing)
            data = await coordinator._async_update_data()
            fast_data = await coordinator.fast_coordinator._async_update_data()
            self.assertNotIn(advanced.VAL_INPUT_2, data)
            self.assertEqual(fast_data[protocol.VAL_TEMP_HEAT], 123)

        asyncio.run(run())

    def test_write_reads_back_actual_value_and_failed_readback_retries(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.REG_LED_BRIGHTNESS] = 25
            peripheral = FakePeripheral(
                registers,
                delayed_write_registers=(protocol.REG_LED_BRIGHTNESS,),
            )
            events = []
            coordinator = self._make_coordinator(peripheral, events=events)
            await coordinator.async_refresh()
            await coordinator.settings_coordinator.async_refresh()
            reads_before_write = sum(
                operation[0] == "read" for operation in peripheral.operations
            )
            refreshes_before_write = coordinator.settings_coordinator.refresh_count

            await coordinator.async_set_percentage_select(protocol.REG_LED_BRIGHTNESS, 75)

            self.assertEqual(peripheral.registers[protocol.REG_LED_BRIGHTNESS], 25)
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_LED_BRIGHTNESS], 25
            )
            self.assertEqual(
                sum(operation[0] == "read" for operation in peripheral.operations),
                reads_before_write + 1,
            )
            self.assertEqual(
                [operation[0] for operation in peripheral.operations[-2:]],
                ["write", "read"],
            )
            await asyncio.sleep(0)
            self.assertEqual(
                coordinator.settings_coordinator.refresh_count, refreshes_before_write
            )
            self.assertEqual(
                coordinator.settings_coordinator.request_refresh_count, 1
            )
            self.assertEqual(coordinator.request_refresh_count, 1)
            self.assertEqual([event[0] for event in events], ["connect"])

            peripheral.apply_delayed_writes()
            await coordinator.settings_coordinator.async_refresh()
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_LED_BRIGHTNESS],
                75,
            )
            self.assertEqual([event[0] for event in events], ["connect"])

            failing = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(failing)
            await coordinator.settings_coordinator.async_refresh()
            failing.fail_reads.add(protocol.REG_LED_BRIGHTNESS)
            await coordinator.async_set_percentage_select(protocol.REG_LED_BRIGHTNESS, 100)
            self.assertEqual(failing.registers[protocol.REG_LED_BRIGHTNESS], 100)
            await asyncio.sleep(0)
            self.assertEqual(
                coordinator.settings_coordinator.request_refresh_count, 1
            )
            self.assertEqual(coordinator.request_refresh_count, 1)
            failing.fail_reads.clear()
            await coordinator.settings_coordinator.async_refresh()
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_LED_BRIGHTNESS], 100
            )

        asyncio.run(run())

    def test_settings_poll_cannot_overwrite_a_queued_write_readback(self):
        async def run():
            led = protocol.REG_LED_BRIGHTNESS
            parameter = parameters_module.APK_PARAMETERS[const.SENSO_MODEL]["000-001"]
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)
            coordinator.data = {}
            settings = coordinator.settings_coordinator
            settings.contexts.add(parameter.register)
            parameter_frame = asyncio.Event()
            resume = asyncio.Event()
            read_batch = coordinator._async_read_batch

            async def pause_after_parameter(batch, client=None):
                data = await read_batch(batch, client)
                if parameter.register in data and not parameter_frame.is_set():
                    parameter_frame.set()
                    await resume.wait()
                return data

            with patch.object(coordinator, "_async_read_batch", pause_after_parameter):
                poll = asyncio.create_task(settings.async_refresh())
                await asyncio.wait_for(parameter_frame.wait(), timeout=2)
                await coordinator.async_set_percentage_select(led, 75)
                self.assertEqual(peripheral.registers[led], 75)
                resume.set()
                await poll

            self.assertEqual(settings.data[led], 75)
            self.assertEqual(settings.data[protocol.MAX_LIMIT_ROOM_TEMP], 300)
            self.assertIn(parameter.register, settings.data)
            self.assertNotIn(led, coordinator.data)

        asyncio.run(run())

    def test_target_temperature_is_available_without_temperature_mode(self):
        async def run():
            coordinator = self._make_coordinator(FakePeripheral({}))
            coordinator.available = True
            coordinator.settings_coordinator.data = {}
            entry = types.SimpleNamespace(
                data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Senso"},
                title="Test Senso",
                runtime_data=coordinator,
            )
            entities = []
            await number_module.async_setup_entry(
                object(), entry, entities.extend
            )

            target = next(
                entity
                for entity in entities
                if entity._attr_unique_id.endswith("_target_room_temperature")
            )
            heat_cap = next(
                entity
                for entity in entities
                if entity._attr_unique_id.endswith("_max_heat_cap_temperature")
            )
            self.assertFalse(heat_cap._attr_entity_registry_enabled_default)
            self.assertFalse(heat_cap.available)
            self.assertFalse(target.available)
            self.assertEqual(target.native_min_value, 0)
            self.assertEqual(target.native_max_value, 50)

            coordinator.settings_coordinator.data = {
                protocol.MIN_LIMIT_ROOM_TEMP: 180,
                protocol.MAX_LIMIT_ROOM_TEMP: 300,
                protocol.DESIRE_ROOM_TEMP: 225,
                protocol.REG_POWER: 0,
                protocol.REG_ROOM_TEMP_EN: 0,
            }
            self.assertTrue(target.available)
            self.assertEqual(target.native_value, 22.5)

            coordinator.settings_coordinator.data[protocol.REG_MAX_HEAT_CAP_TEMP] = 135
            coordinator.settings_coordinator.data[protocol.REG_NOMINAL_POWER] = 70
            entities.clear()
            await number_module.async_setup_entry(
                object(), entry, entities.extend
            )
            heat_cap = next(
                entity
                for entity in entities
                if entity._attr_unique_id.endswith("_max_heat_cap_temperature")
            )
            self.assertTrue(heat_cap.available)
            self.assertEqual(heat_cap.native_value, 135)
            nominal = next(
                entity
                for entity in entities
                if entity._attr_unique_id.endswith("_nominal_power")
            )
            self.assertEqual(nominal._attr_name, "Nominal power")
            self.assertEqual(
                (nominal.native_min_value, nominal.native_max_value), (0, 50)
            )
            self.assertEqual(nominal._attr_native_step, 0.1)
            self.assertFalse(nominal._attr_entity_registry_enabled_default)
            self.assertEqual(nominal.native_value, 7.0)

        asyncio.run(run())

    def test_room_temperature_write_does_not_require_temperature_mode(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.REG_POWER] = 0
            peripheral = FakePeripheral(registers)
            coordinator = self._make_coordinator(peripheral)

            await coordinator.async_set_number(protocol.DESIRE_ROOM_TEMP, 23.0)

            self.assertEqual(peripheral.registers[protocol.DESIRE_ROOM_TEMP], 230)
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.DESIRE_ROOM_TEMP], 230
            )
            self.assertEqual(coordinator.refresh_count, 0)
            for value in (17.9, 22.25, 30.1):
                with self.subTest(value=value), self.assertRaises(
                    FakeHomeAssistantError
                ):
                    await coordinator.async_set_number(protocol.DESIRE_ROOM_TEMP, value)

            unconfirmed = FakePeripheral(
                registers,
                ignored_write_registers=(protocol.DESIRE_ROOM_TEMP,),
            )
            coordinator = self._make_coordinator(unconfirmed)
            await coordinator.async_set_number(protocol.DESIRE_ROOM_TEMP, 24.0)
            self.assertEqual(unconfirmed.registers[protocol.DESIRE_ROOM_TEMP], 220)
            self.assertEqual(coordinator.refresh_count, 0)

        asyncio.run(run())

    def test_advanced_poll_reads_maximum_heat_cap_when_enabled(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.REG_MAX_HEAT_CAP_TEMP] = 135
            registers[advanced.VAL_TEMP_BOARD] = 215
            coordinator = self._make_coordinator(FakePeripheral(registers))
            coordinator.settings_coordinator.contexts.add(
                protocol.REG_MAX_HEAT_CAP_TEMP
            )
            coordinator.contexts.add(
                advanced.VAL_TEMP_BOARD
            )

            data = await coordinator._async_update_data()
            settings = await coordinator.settings_coordinator._async_update_data()

            self.assertEqual(settings[protocol.REG_MAX_HEAT_CAP_TEMP], 135)
            self.assertEqual(data[advanced.VAL_TEMP_BOARD], 215)

            failing = FakePeripheral(
                registers,
                fail_reads=(protocol.REG_MAX_HEAT_CAP_TEMP,),
            )
            coordinator = self._make_coordinator(failing)
            coordinator.settings_coordinator.contexts.add(
                protocol.REG_MAX_HEAT_CAP_TEMP
            )
            coordinator.contexts.add(advanced.VAL_TEMP_BOARD)
            with self.assertRaises(coordinator_module.UpdateFailed):
                await coordinator.settings_coordinator._async_update_data()

        asyncio.run(run())

    def test_maximum_heat_cap_writes_are_validated_and_read_back(self):
        async def run():
            registers = self._overview_registers()
            registers[protocol.REG_MAX_HEAT_CAP_TEMP] = 120
            registers[protocol.VAL_HEAT_CAP] = 45
            peripheral = FakePeripheral(registers)
            coordinator = self._make_coordinator(peripheral)

            await coordinator.async_set_number(protocol.REG_MAX_HEAT_CAP_TEMP, 140)

            self.assertEqual(
                peripheral.registers[protocol.REG_MAX_HEAT_CAP_TEMP], 140
            )
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_MAX_HEAT_CAP_TEMP],
                140,
            )
            self.assertEqual(peripheral.registers[protocol.VAL_HEAT_CAP], 45)
            self.assertEqual(coordinator.refresh_count, 0)
            for value in (70, 160):
                await coordinator.async_set_number(
                    protocol.REG_MAX_HEAT_CAP_TEMP, value
                )
                self.assertEqual(
                    peripheral.registers[protocol.REG_MAX_HEAT_CAP_TEMP], value
                )
            self.assertEqual(coordinator.refresh_count, 0)
            self.assertEqual(peripheral.registers[protocol.VAL_HEAT_CAP], 45)

            for value in (
                69,
                160.5,
                161,
                float("inf"),
                True,
            ):
                with self.subTest(value=value), self.assertRaises(
                    FakeHomeAssistantError
                ):
                    await coordinator.async_set_number(
                        protocol.REG_MAX_HEAT_CAP_TEMP, value
                    )

            unsupported = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(unsupported)
            with self.assertRaises(FakeHomeAssistantError):
                await coordinator.async_set_number(
                    protocol.REG_MAX_HEAT_CAP_TEMP, 140
                )
            self.assertFalse(
                any(
                    operation[0] == "write"
                    and operation[1] == protocol.CHARACTERISTIC_DATA_UUID
                    for operation in unsupported.operations
                )
            )

            unconfirmed = FakePeripheral(
                registers,
                ignored_write_registers=(protocol.REG_MAX_HEAT_CAP_TEMP,),
            )
            coordinator = self._make_coordinator(unconfirmed)
            await coordinator.async_set_number(protocol.REG_MAX_HEAT_CAP_TEMP, 145)
            self.assertEqual(unconfirmed.registers[protocol.REG_MAX_HEAT_CAP_TEMP], 120)
            self.assertEqual(coordinator.refresh_count, 0)

        asyncio.run(run())

    async def _test_coordinator_polls_user_settings(self):
        registers = self._overview_registers()
        peripheral = FakePeripheral(registers)
        coordinator = self._make_coordinator(peripheral)

        await coordinator.settings_coordinator.async_refresh()
        data = coordinator.settings_coordinator.data

        self.assertNotIn(protocol.REG_STATUS, data)
        self.assertEqual(data[protocol.REG_ROOM_TEMP_EN], 5)
        self.assertEqual(data[protocol.REG_LED_BRIGHTNESS], 45)
        self.assertEqual(data[protocol.REG_SOUND_VOLUME], 30)
        self.assertEqual(data[protocol.REG_FUEL], 2)
        self.assertEqual(data[protocol.REG_FUEL_COUNT], 3)
        self.assertEqual(
            coordinator_module.room_temperature_range(data), (180, 300)
        )
        requested_registers = set().union(*map(set, self._read_frames(peripheral)))
        self.assertTrue(set(advanced.ADVANCED_REGISTERS).isdisjoint(requested_registers))
        self.assertNotIn(protocol.REG_POWER, requested_registers)
        self.assertNotIn(protocol.REG_MAX_HEAT_CAP_TEMP, requested_registers)

    def test_settings_read_failure_does_not_affect_telemetry(self):
        asyncio.run(self._test_settings_read_failure_does_not_affect_telemetry())

    async def _test_settings_read_failure_does_not_affect_telemetry(self):
        peripheral = FakePeripheral(
            self._overview_registers(), fail_reads=USER_SETTING_REGISTERS
        )
        coordinator = self._make_coordinator(peripheral)

        await coordinator.async_refresh()
        await coordinator.fast_coordinator.async_refresh()
        await coordinator.settings_coordinator.async_refresh()
        self.assertFalse(coordinator.settings_coordinator.last_update_success)
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(coordinator.data[protocol.VAL_TEMP_AIR], 234)
        self.assertEqual(coordinator.fast_coordinator.data[protocol.REG_STATUS], 2)

    def test_setting_writes_validate_and_read_back(self):
        asyncio.run(self._test_setting_writes_validate_and_read_back())

    async def _test_setting_writes_validate_and_read_back(self):
        peripheral = FakePeripheral(self._overview_registers())
        coordinator = self._make_coordinator(peripheral)

        for value in (0, 25, 50, 75, 100):
            await coordinator.async_set_percentage_select(
                protocol.REG_LED_BRIGHTNESS, value
            )
            self.assertEqual(peripheral.registers[protocol.REG_LED_BRIGHTNESS], value)
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_LED_BRIGHTNESS],
                value,
            )
        self.assertEqual(coordinator.settings_coordinator.refresh_count, 0)

        for value in (True, 1, 25.5, 125):
            with self.subTest(value=value), self.assertRaises(
                FakeHomeAssistantError
            ):
                await coordinator.async_set_percentage_select(
                    protocol.REG_LED_BRIGHTNESS, value
                )

        fumis = self._make_coordinator(
            FakePeripheral(self._overview_registers()), model=const.FUMIS_AIR_MODEL
        )
        with self.assertRaises(FakeHomeAssistantError):
            await fumis.async_set_percentage_select(protocol.REG_LED_BRIGHTNESS, 50)

        unconfirmed = FakePeripheral(
            self._overview_registers(),
            ignored_write_registers=(protocol.REG_SOUND_VOLUME,),
        )
        coordinator = self._make_coordinator(unconfirmed)
        await coordinator.async_set_percentage_select(protocol.REG_SOUND_VOLUME, 50)
        self.assertEqual(coordinator.settings_coordinator.refresh_count, 0)
        self.assertEqual(unconfirmed.registers[protocol.REG_SOUND_VOLUME], 30)

    def test_fuel_type_writes_are_limited_and_read_back(self):
        asyncio.run(self._test_fuel_type_writes_are_limited_and_read_back())

    async def _test_fuel_type_writes_are_limited_and_read_back(self):
        peripheral = FakePeripheral(self._overview_registers())
        coordinator = self._make_coordinator(peripheral)

        for option, value in zip(protocol.FUEL_TYPE_OPTIONS, (1, 2, 3)):
            await coordinator.async_set_fuel_type(option)
            self.assertEqual(peripheral.registers[protocol.REG_SET_FUEL], value)
            self.assertEqual(
                peripheral.registers[protocol.REG_FUEL] & protocol.FUEL_TYPE_MASK,
                value,
            )
            self.assertEqual(
                coordinator.settings_coordinator.data[protocol.REG_FUEL]
                & protocol.FUEL_TYPE_MASK,
                value,
            )
        self.assertEqual(coordinator.settings_coordinator.refresh_count, 0)

        custom_state = self._overview_registers()
        custom_state[protocol.REG_FUEL] = 4
        custom_state[protocol.REG_FUEL_COUNT] = 5
        custom_peripheral = FakePeripheral(custom_state)
        coordinator = self._make_coordinator(custom_peripheral)
        await coordinator.settings_coordinator.async_refresh()
        data = coordinator.settings_coordinator.data
        self.assertIsNone(
            protocol.selected_fuel_type(
                data[protocol.REG_FUEL], data[protocol.REG_FUEL_COUNT]
            )
        )
        self.assertEqual(data[protocol.REG_FUEL], 4)
        await coordinator.async_set_fuel_type("Soft wood")
        self.assertEqual(custom_peripheral.registers[protocol.REG_FUEL], 1)

        limited = self._overview_registers()
        limited[protocol.REG_FUEL_COUNT] = 2
        peripheral = FakePeripheral(limited)
        coordinator = self._make_coordinator(peripheral)
        with self.assertRaises(FakeHomeAssistantError):
            await coordinator.async_set_fuel_type("Pellets")
        with self.assertRaises(FakeHomeAssistantError):
            await coordinator.async_set_fuel_type("Custom fuel")
        self.assertEqual(coordinator.refresh_count, 0)
        self.assertNotIn(protocol.REG_SET_FUEL, peripheral.registers)

        invalid_count = self._overview_registers()
        invalid_count[protocol.REG_FUEL_COUNT] = 0
        coordinator = self._make_coordinator(FakePeripheral(invalid_count))
        with self.assertRaises(FakeHomeAssistantError):
            await coordinator.async_set_fuel_type("Soft wood")

        unconfirmed = FakePeripheral(
            self._overview_registers(),
            ignored_write_registers=(protocol.REG_SET_FUEL,),
        )
        coordinator = self._make_coordinator(unconfirmed)
        await coordinator.async_set_fuel_type("Pellets")
        self.assertEqual(unconfirmed.registers[protocol.REG_FUEL], 2)
        self.assertEqual(coordinator.refresh_count, 0)

    def test_room_temperature_control_preserves_installer_and_other_bits(self):
        asyncio.run(self._test_room_temperature_control_preserves_bits())

    async def _test_room_temperature_control_preserves_bits(self):
        peripheral = FakePeripheral(self._overview_registers())
        coordinator = self._make_coordinator(peripheral)

        await coordinator.async_set_room_temperature_control(True)
        self.assertEqual(peripheral.registers[protocol.REG_ROOM_TEMP_EN], 7)
        self.assertEqual(
            coordinator.settings_coordinator.data[protocol.REG_ROOM_TEMP_EN], 7
        )
        await coordinator.async_set_room_temperature_control(False)
        self.assertEqual(peripheral.registers[protocol.REG_ROOM_TEMP_EN], 5)

        peripheral.registers[protocol.REG_ROOM_TEMP_EN] = 4
        data_writes = sum(
            operation[0] == "write"
            and operation[1] == protocol.CHARACTERISTIC_DATA_UUID
            for operation in peripheral.operations
        )
        with self.assertRaises(FakeHomeAssistantError):
            await coordinator.async_set_room_temperature_control(False)
        self.assertEqual(
            sum(
                operation[0] == "write"
                and operation[1] == protocol.CHARACTERISTIC_DATA_UUID
                for operation in peripheral.operations
            ),
            data_writes,
        )

    def test_read_batches_repeat_last_register_to_five_words(self):
        batches = protocol.build_read_batches((1, 2, 3, 4, 5, 6, 7))

        self.assertEqual(len(batches), 2)
        self.assertEqual(
            batches[0].frame,
            b"\x00\x01\x00\x02\x00\x03\x00\x04\x00\x05",
        )
        self.assertEqual(batches[1].registers, (6, 7))
        self.assertEqual(
            batches[1].frame,
            b"\x00\x06\x00\x07\x00\x07\x00\x07\x00\x07",
        )

    def test_protocol_rejects_register_groups_larger_than_five_words(self):
        registers = (1, 2, 3, 4, 5, 6)

        with self.assertRaises(ValueError):
            protocol.encode_read_request(registers)
        with self.assertRaises(ValueError):
            protocol.decode_register_batch(registers, b"\x00\x00" * 5)

    def test_protocol_requires_exactly_five_response_words(self):
        for size in (8, 9, 20):
            with self.subTest(size=size):
                with self.assertRaises(protocol.FumisAirResponseSizeError):
                    protocol.decode_register_batch((1,), b"\x00" * size)

    def test_controller_register_groups_are_split_into_fixed_size_frames(self):
        async def run():
            peripheral = FakePeripheral(self._overview_registers())
            coordinator = self._make_coordinator(peripheral)

            values = await coordinator._async_read_data()
            fast_values = await coordinator._async_read_fast_data()

            address_writes = [
                operation
                for operation in peripheral.operations
                if operation[0] == "write"
                and operation[1] == protocol.CHARACTERISTIC_ADDRESS_UUID
            ]
            self.assertGreater(len(address_writes), 1)
            self.assertTrue(
                all(
                    len(operation[2]) == protocol.FRAME_SIZE
                    for operation in address_writes
                )
            )
            self.assertTrue(peripheral.response_sizes)
            self.assertTrue(
                all(size == protocol.FRAME_SIZE for size in peripheral.response_sizes)
            )
            self.assertEqual(
                fast_values[protocol.REG_STATUS],
                self._overview_registers()[protocol.REG_STATUS],
            )
            self.assertEqual(
                values[protocol.DISP_VAL_USER],
                self._overview_registers()[protocol.DISP_VAL_USER],
            )

        asyncio.run(run())

    def test_decodes_signed_values_as_big_endian(self):
        unsigned_register = SENSO_CATALOG["supply_voltage"].register
        payload = b"\xff\xf6\x80\x01" + b"\x00\x00" * 3

        values = protocol.decode_register_batch(
            (protocol.VAL_TEMP_AIR, unsigned_register), payload
        )

        self.assertEqual(
            values, {protocol.VAL_TEMP_AIR: -10, unsigned_register: 0x8001}
        )
        app_parameter = SENSO_CATALOG["parameter_senso_000_001"]
        self.assertEqual(
            parameters_module.decode_catalog_value(app_parameter, 0xFFF6), -1.0
        )
        controlled_setting = SENSO_CATALOG["nominal_power"]
        self.assertEqual(
            parameters_module.decode_catalog_value(controlled_setting, 0xFFF6),
            6552.6,
        )

    def test_signed_registers_are_limited_to_values_read_by_integration(self):
        self.assertEqual(
            protocol.SIGNED_REGISTERS,
            {
                10241,
                10242,
                10243,
                10244,
                10245,
                10246,
                10247,
                10248,
                10251,
                10252,
                10253,
                10254,
                10255,
                10256,
                10262,
                10263,
                10264,
                11010,
                11011,
                11012,
                11013,
                11014,
                11015,
                11018,
            },
        )

    def test_padding_registers_are_not_returned(self):
        payload = b"\x00\x2a" * 5

        values = protocol.decode_register_batch((101, 102), payload)

        self.assertEqual(values, {101: 42, 102: 42})
        self.assertEqual(protocol.displayed_registers(0), ())
        self.assertEqual(
            set(protocol.displayed_registers(0b101)),
            {protocol.VAL_TEMP_AIR, protocol.VAL_TEMP_CHAMBER},
        )

    def test_display_mask_ignores_bits_outside_the_ten_app_channels(self):
        self.assertEqual(
            protocol.displayed_registers(0xFFFF),
            (10241, 10242, 10244, 10245, 10246, 10247, 10243, 10262, 10263, 10264),
        )
        with self.assertRaises(ValueError):
            protocol.displayed_registers(0x10000)

    def test_requested_overview_registers_are_selected_by_display_mask(self):
        requested = {
            protocol.VAL_TEMP_AIR,
            protocol.VAL_TEMP_CHAMBER,
            protocol.VAL_TEMP_HEAT,
            protocol.VAL_DRAFT,
            protocol.VAL_HEAT_CAP,
            protocol.VAL_HEAT_OUTPUT,
        }
        mask = sum(
            1 << row.display_bit
            for row in parameters_module.ENTITY_CATALOG[const.SENSO_MODEL].values()
            if row.display_bit is not None and row.register in requested
        )

        displayed = protocol.displayed_registers(mask)
        self.assertEqual(set(displayed), requested)
        self.assertEqual(len(displayed), len(requested))
        self.assertIn(advanced.VAL_TEMP_BOARD, advanced.ADVANCED_REGISTERS)
        self.assertTrue(
            set(USER_SETTING_REGISTERS).isdisjoint(
                advanced.ADVANCED_REGISTERS
            )
        )

    def test_probe_requires_known_status_but_not_known_advice(self):
        protocol.validate_probe_registers(
            {
                protocol.REG_STATUS: 2,
                protocol.REG_ADVICE: 1,
                protocol.DISP_VAL_USER: 0b101 | (1 << 10),
            }
        )
        protocol.validate_probe_registers(
            {
                protocol.REG_STATUS: 2,
                protocol.REG_ADVICE: 99,
                protocol.DISP_VAL_USER: 0,
            }
        )
        with self.assertRaises(protocol.FumisAirProtocolError):
            protocol.validate_probe_registers({protocol.REG_ADVICE: 1})
        with self.assertRaises(protocol.FumisAirProtocolError):
            protocol.validate_probe_registers(
                {
                    protocol.REG_STATUS: 9,
                    protocol.REG_ADVICE: 1,
                    protocol.DISP_VAL_USER: 0,
                }
            )

    def test_ble_read_and_write_sequence(self):
        asyncio.run(self._test_ble_read_and_write_sequence())

    def test_read_frames_and_commands_keep_their_delays(self):
        client = FakePeripheral({})
        delays = []

        async def record_delay(delay):
            delays.append(delay)

        self.assertEqual(protocol.READ_FRAME_DELAY_SECONDS, 0.1)
        self.assertEqual(protocol.COMMAND_DELAY_SECONDS, 0.1)
        with patch.object(protocol.asyncio, "sleep", record_delay):
            asyncio.run(protocol.read_registers(client, (1, 2, 3, 4, 5, 6)))
            self.assertEqual(
                delays,
                [0.1, 0.1],
            )

            delays.clear()
            asyncio.run(protocol.write_register(client, 1, 2))

        self.assertEqual(delays, [0.1])


    def test_catalog_metadata_decodes_advanced_values(self):
        def decode(key, value):
            return parameters_module.decode_catalog_value(SENSO_CATALOG[key], value)

        self.assertEqual(decode("supply_voltage", 238), 23.8)
        self.assertEqual(decode("board_temperature", -10), -1.0)
        self.assertEqual(decode("maximum_temperature", 235), 235)
        self.assertEqual(decode("time_to_service", 120), 120)
        self.assertIsNone(decode("battery_voltage", None))
        self.assertEqual(decode("input_1_configuration", 10), "Output")
        self.assertEqual(decode("input_1_configuration", 99), "Unknown (99)")

    async def _test_ble_read_and_write_sequence(self):
        client = FakePeripheral({protocol.REG_STATUS: 7})

        result = await protocol.read_registers(client, (protocol.REG_STATUS,))

        self.assertEqual(result, {protocol.REG_STATUS: 7})
        self.assertEqual(
            [operation[0:2] for operation in client.operations],
            [
                ("write", protocol.CHARACTERISTIC_ADDRESS_UUID),
                ("read", protocol.CHARACTERISTIC_DATA_UUID),
            ],
        )
        self.assertTrue(client.operations[0][3])

        client.operations.clear()
        await protocol.write_register(client, protocol.DESIRE_ROOM_TEMP, 235)
        self.assertEqual(
            [operation[0:2] for operation in client.operations],
            [
                ("write", protocol.CHARACTERISTIC_ADDRESS_UUID),
                ("write", protocol.CHARACTERISTIC_DATA_UUID),
            ],
        )
        self.assertEqual(
            client.operations[0][2],
            b"\x16\x06" * 5,
        )
        self.assertEqual(client.operations[1][2], b"\x00\xeb" * 5)


class FumisAirNumberTest(unittest.TestCase):
    def test_number_entities_keep_ids_metadata_and_write_dispatch(self):
        async def run():
            calls = []

            class Coordinator:
                data = {protocol.REG_FAN: 60, protocol.REG_MAX_HEAT_CAP_TEMP: 135}
                available = True

                async def async_set_number(self, register, value):
                    calls.append((register, value))

            coordinator = Coordinator()
            coordinator.settings_coordinator = types.SimpleNamespace(data=coordinator.data)
            entry = types.SimpleNamespace(
                data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Senso"},
                title="Test Senso",
                runtime_data=coordinator,
            )
            entities = []
            await number_module.async_setup_entry(object(), entry, entities.extend)
            by_id = {entity._attr_unique_id: entity for entity in entities}
            air = by_id["fumis_air_aa:bb:cc:dd:ee:ff_manual_air_intake"]
            heat_cap = by_id[
                "fumis_air_aa:bb:cc:dd:ee:ff_max_heat_cap_temperature"
            ]

            self.assertEqual(air._attr_name, "Manual air intake")
            self.assertEqual((air.native_min_value, air.native_max_value), (0, 100))
            self.assertEqual(air._attr_native_unit_of_measurement, "%")
            self.assertEqual(air._attr_unique_id.endswith("_manual_air_intake"), True)
            self.assertEqual(air.context, protocol.REG_FAN)
            self.assertEqual(
                (
                    heat_cap.native_min_value,
                    heat_cap.native_max_value,
                ),
                (70, 160),
            )
            self.assertEqual(heat_cap.native_value, 135)
            self.assertFalse(heat_cap._attr_entity_registry_enabled_default)
            self.assertEqual(heat_cap._attr_native_unit_of_measurement, "°C")
            self.assertEqual(heat_cap._attr_device_class, "temperature")

            await air.async_set_native_value(25)
            await heat_cap.async_set_native_value(140)
            self.assertEqual(
                calls,
                [
                    (protocol.REG_FAN, 25),
                    (protocol.REG_MAX_HEAT_CAP_TEMP, 140),
                ],
            )

        asyncio.run(run())


class FumisAirSelectTest(unittest.TestCase):
    def test_selects_keep_fuel_support_and_offer_exact_level_presets(self):
        async def run():
            calls = []

            class Coordinator:
                data = {
                    protocol.REG_LED_BRIGHTNESS: 75,
                    protocol.REG_SOUND_VOLUME: 52,
                    protocol.REG_FUEL: 2,
                    protocol.REG_FUEL_COUNT: 3,
                }
                available = True

                async def async_set_percentage_select(self, register, value):
                    calls.append((register, value))

                async def async_set_fuel_type(self, option):
                    calls.append((protocol.REG_FUEL, option))

            coordinator = Coordinator()
            coordinator.settings_coordinator = types.SimpleNamespace(
                data=coordinator.data
            )
            entry = types.SimpleNamespace(
                data={"address": "AA:BB:CC:DD:EE:FF", "name": "Test Senso"},
                title="Test Senso",
                runtime_data=coordinator,
            )
            entities = []
            await select_module.async_setup_entry(object(), entry, entities.extend)
            by_id = {entity._attr_unique_id: entity for entity in entities}
            led = by_id["fumis_air_aa:bb:cc:dd:ee:ff_led_brightness"]
            buzzer = by_id["fumis_air_aa:bb:cc:dd:ee:ff_sound_volume"]
            fuel = by_id["fumis_air_aa:bb:cc:dd:ee:ff_fuel_type"]

            expected_options = ["Off", "Low", "Mid", "High", "Max"]
            self.assertEqual(led.options, expected_options)
            self.assertEqual(buzzer.options, expected_options)
            for entity, register in (
                (led, protocol.REG_LED_BRIGHTNESS),
                (buzzer, protocol.REG_SOUND_VOLUME),
            ):
                for value, expected in (
                    (0, "Off"),
                    (1, "Low"),
                    (25, "Low"),
                    (26, "Mid"),
                    (50, "Mid"),
                    (51, "High"),
                    (52, "High"),
                    (75, "High"),
                    (76, "Max"),
                    (100, "Max"),
                    (-1, None),
                    (101, None),
                ):
                    with self.subTest(register=register, value=value):
                        coordinator.data[register] = value
                        self.assertEqual(entity.current_option, expected)
                        self.assertEqual(coordinator.data[register], value)
                del coordinator.data[register]
                self.assertIsNone(entity.current_option)
            self.assertEqual(calls, [])
            self.assertEqual(fuel.options, list(protocol.FUEL_TYPE_OPTIONS))
            self.assertEqual(fuel.current_option, "Hard wood")

            await led.async_select_option("Off")
            await led.async_select_option("Low")
            await led.async_select_option("Mid")
            await led.async_select_option("High")
            await led.async_select_option("Max")
            await fuel.async_select_option("Pellets")
            self.assertEqual(
                calls,
                [
                    (protocol.REG_LED_BRIGHTNESS, 0),
                    (protocol.REG_LED_BRIGHTNESS, 25),
                    (protocol.REG_LED_BRIGHTNESS, 50),
                    (protocol.REG_LED_BRIGHTNESS, 75),
                    (protocol.REG_LED_BRIGHTNESS, 100),
                    (protocol.REG_FUEL, "Pellets"),
                ],
            )

        asyncio.run(run())


class FumisAirEntityMetadataTest(unittest.TestCase):
    def _metadata(self, key):
        return SENSO_CATALOG[key]

    def _sensor(self, key, registers):
        coordinator = types.SimpleNamespace(data=registers, available=True)
        entry = types.SimpleNamespace(
            data={"address": "AA:BB:CC:DD:EE:FF", "name": "Old saved name"},
            title="Test Senso",
        )
        return sensor_module.FumisAirSensor(
            coordinator, entry, self._metadata(key)
        )

    def test_raw_inputs_counters_and_fan_are_numeric_entities(self):
        registers = {
            advanced.VAL_INPUT_1: -12,
            advanced.VAL_INPUT_2: 0,
            advanced.IN_CONFIG_1: 2,
            advanced.EVM_COUNT: 0,
            advanced.EVO_COUNT: 5,
            protocol.REG_FAN: 0,
        }

        input_one = self._sensor("input_1_value", registers)
        input_two = self._sensor("input_2_value", registers)
        misfires = self._sensor("misfires_count", registers)
        overheatings = self._sensor("overheatings_count", registers)
        fan = self._sensor("fan_setting", registers)

        self.assertEqual(input_one.native_value, -12)
        self.assertIsInstance(input_one.native_value, int)
        self.assertEqual(input_two.native_value, 0)
        self.assertEqual(misfires.native_value, 0)
        self.assertEqual(overheatings.native_value, 5)
        self.assertEqual(fan.native_value, 0)
        self.assertEqual(
            input_one.entity_description.state_class, "measurement"
        )
        self.assertEqual(input_two.entity_description.state_class, "measurement")
        self.assertEqual(fan.entity_description.state_class, "measurement")
        self.assertEqual(
            misfires.entity_description.state_class, "total_increasing"
        )
        self.assertEqual(
            overheatings.entity_description.state_class, "total_increasing"
        )

        configuration = self._sensor("input_1_configuration", registers)
        missing_input = self._sensor("input_3_value", registers)
        self.assertEqual(configuration.native_value, "NTC")
        self.assertIsNone(configuration.entity_description.state_class)
        self.assertIsNone(missing_input.native_value)

    def test_display_values_use_catalog_formats_and_regulator_scaling(self):
        for key, raw, expected in (
            ("air_temperature", -123, -12.3),
            ("water_temperature", 123, 123),
            ("chamber_temperature", 123, 123),
            ("oven_temperature", 123, 123),
            ("heat_temperature", 123, 123),
            ("heat_output", 1234, 123400),
            ("lambda", 123, 123),
        ):
            with self.subTest(key=key):
                metadata = self._metadata(key)
                sensor = self._sensor(key, {metadata.register: raw})
                self.assertEqual(sensor.native_value, expected)

        regulator = self._metadata("regulator_position")
        self.assertEqual(
            self._sensor("regulator_position", {regulator.register: 61}).native_value,
            51,
        )
        self.assertEqual(
            self._sensor("regulator_position", {regulator.register: 19}).native_value,
            -1,
        )
        self.assertIsNone(self._sensor("heat_output", {}).native_value)

    def test_parameter_catalogs_cover_models_and_entity_metadata(self):
        expected_counts = {const.SENSO_MODEL: 148, const.FUMIS_AIR_MODEL: 309}
        for model, count in expected_counts.items():
            with self.subTest(model=model):
                parameters = parameters_module.APK_PARAMETERS[model].values()
                self.assertEqual(len(parameters), count)
                self.assertEqual(
                    len({parameter.identifier for parameter in parameters}), count
                )
                for parameter in parameters:
                    self.assertEqual(
                        parameter.register,
                        (int(parameter.identifier[:3]) << 8)
                        | int(parameter.identifier[4:]),
                    )
                    self.assertIn(parameter.entity_type, {"sensor", "config", "diag"})
                    if parameter.entity_type == "config":
                        continue
                    sensor = sensor_module.FumisAirSensor(
                        types.SimpleNamespace(data={}, available=True),
                        types.SimpleNamespace(
                            data={"address": "AA:BB:CC:DD:EE:FF", "model": model},
                            title="Test controller",
                        ),
                        parameter,
                    )
                    self.assertEqual(
                        sensor.entity_description.name, parameter.description
                    )
                    self.assertNotIn(
                        parameter.identifier, sensor.entity_description.name
                    )
                    self.assertEqual(sensor._attr_entity_category, "diagnostic")
                    self.assertFalse(sensor._attr_entity_registry_enabled_default)

                catalog = parameters_module.ENTITY_CATALOG[model].values()
                self.assertEqual(len(catalog), 190 if model == const.SENSO_MODEL else 344)
                self.assertTrue(
                    all(
                        item.entity_type in {"sensor", "config", "diag"}
                        and not hasattr(item, "name")
                        for item in catalog
                    )
                )
                parameter_rows = [
                    item for item in catalog if item.entity_key.startswith("parameter_")
                ]
                self.assertTrue(parameter_rows)
                self.assertTrue(
                    all(
                        item.entity_type == "diag"
                        and item.disabled_by_default
                        and item.group == "parameter"
                        and not hasattr(item, "signed")
                        for item in parameter_rows
                    )
                )
                direct_sensors = [
                    item for item in catalog
                    if item.identifier is None and item.platform == "sensor"
                ]
                self.assertEqual(len(direct_sensors), 35 if model == const.SENSO_MODEL else 29)
                display_rows = [
                    (item.display_bit, item.register)
                    for item in direct_sensors
                    if item.display_bit is not None
                ]
                expected_display_rows = list(
                    enumerate(
                        (10241, 10242, 10243, 10244, 10245, 10246, 10247)
                        + (
                            (10262, 10263, 10264)
                            if model == const.SENSO_MODEL
                            else ()
                        )
                    )
                )
                self.assertEqual(sorted(display_rows), sorted(expected_display_rows))
                config_keys = {
                    item.entity_key
                    for item in catalog
                    if item.entity_type == "config"
                }
                self.assertEqual(
                    config_keys,
                    set(parameters_module.WRITABLE_ENTITIES[model]),
                )
                if model == const.SENSO_MODEL:
                    self.assertEqual(
                        config_keys,
                        {
                            "manual_air_intake",
                            "automatic_air_intake",
                            "fuel_type",
                            "target_room_temperature",
                            "room_temperature_control",
                            "led_brightness",
                            "sound_volume",
                            "nominal_power",
                            "max_heat_cap_temperature",
                        },
                    )
                else:
                    self.assertEqual(
                        config_keys, {"manual_air_intake", "automatic_air_intake"}
                    )

        senso_power = parameters_module.APK_PARAMETERS[const.SENSO_MODEL]["030-016"]
        self.assertEqual(senso_power.register, 0x1E10)
        self.assertEqual(senso_power.scale, 0.1)
        self.assertEqual(senso_power.unit, "kW")
        self.assertEqual(senso_power.entity_type, "config")
        self.assertEqual(senso_power.platform, "number")
        self.assertTrue(senso_power.disabled_by_default)
        self.assertEqual(senso_power.entity_key, "nominal_power")
        self.assertEqual((senso_power.minimum, senso_power.maximum), (0, 50))
        self.assertIs(
            parameters_module.WRITABLE_NUMBERS[const.SENSO_MODEL][0x1E10],
            senso_power,
        )
        fumis_power = parameters_module.APK_PARAMETERS[const.FUMIS_AIR_MODEL]["030-016"]
        self.assertEqual(fumis_power.entity_type, "diag")
        self.assertNotIn(
            0x1E10, parameters_module.WRITABLE_NUMBERS[const.FUMIS_AIR_MODEL]
        )

        settings = {
            item.identifier: item
            for item in parameters_module.APK_PARAMETERS[const.SENSO_MODEL].values()
            if item.entity_type == "config"
        }
        self.assertEqual((settings["030-014"].minimum, settings["030-014"].maximum), (0, 100))
        self.assertEqual((settings["030-015"].minimum, settings["030-015"].maximum), (0, 100))
        self.assertEqual((settings["030-016"].minimum, settings["030-016"].maximum), (0, 50))
        self.assertEqual((settings["030-017"].minimum, settings["030-017"].maximum), (70, 160))
        self.assertTrue(settings["030-017"].disabled_by_default)
        self.assertEqual((settings["022-006"].minimum, settings["022-006"].maximum), (0, 50))

        fumis_offset = parameters_module.APK_PARAMETERS[const.FUMIS_AIR_MODEL]["030-002"]
        self.assertEqual((fumis_offset.minimum, fumis_offset.maximum), (-100, 100))
        fumis_sensor = self._parameter_sensor(
            const.FUMIS_AIR_MODEL, fumis_offset, 0xFFC4
        )
        self.assertEqual(fumis_sensor.native_value, -6.0)
        self.assertEqual(
            fumis_sensor._attr_extra_state_attributes["parameter_id"], "030-002"
        )
        self.assertTrue(
            fumis_sensor._attr_unique_id.endswith("_parameter_fumis_air_030_002")
        )

    def test_config_role_requires_a_model_specific_verified_writer(self):
        writable = SENSO_CATALOG["led_brightness"]
        self.assertEqual((writable.entity_type, writable.platform), ("config", "select"))
        self.assertIs(
            parameters_module.WRITABLE_SELECTS[const.SENSO_MODEL][writable.register],
            writable,
        )
        nominal = SENSO_CATALOG["nominal_power"]
        self.assertEqual((nominal.entity_type, nominal.platform), ("config", "number"))
        self.assertEqual(
            parameters_module.WRITABLE_NUMBERS[const.SENSO_MODEL][nominal.register],
            nominal,
        )
        self.assertNotIn(0x1E10, parameters_module.WRITABLE_NUMBERS[const.FUMIS_AIR_MODEL])

    def _parameter_sensor(self, model, parameter, value):
        entry = types.SimpleNamespace(
            data={"address": "AA:BB:CC:DD:EE:FF", "model": model},
            title="Test controller",
        )
        return sensor_module.FumisAirSensor(
            types.SimpleNamespace(data={parameter.register: value}, available=True),
            entry,
            parameter,
        )

    def test_status_and_door_entities_keep_stable_ids(self):
        status_metadata = self._metadata("status")
        coordinator = types.SimpleNamespace(
            data={protocol.REG_STATUS: 4}, available=True
        )
        entry = types.SimpleNamespace(
            data={
                "address": "AA:BB:CC:DD:EE:FF",
                "name": "Old saved name",
                "model": "Tulikivi Senso",
            },
            title="Test Senso",
        )
        status = sensor_module.FumisAirSensor(
            coordinator, entry, status_metadata
        )
        door_metadata = self._metadata("door_open")
        door = binary_sensor_module.FumisAirBinarySensor(
            coordinator, entry, door_metadata
        )

        self.assertIsNone(status._attr_entity_category)
        self.assertEqual(status.native_value, "Door open")
        self.assertEqual(
            status._attr_unique_id, "fumis_air_aa:bb:cc:dd:ee:ff_status"
        )
        self.assertEqual(
            status._attr_device_info["manufacturer"], "ATech elektronika d.o.o."
        )
        self.assertEqual(status._attr_device_info["name"], "Test Senso")
        self.assertEqual(status._attr_device_info["model"], "Tulikivi Senso")
        self.assertIsNone(door._attr_entity_category)
        self.assertTrue(door.is_on)
        self.assertEqual(
            door._attr_unique_id, "fumis_air_aa:bb:cc:dd:ee:ff_door_open"
        )
        for key in ("control_error", "motor_error", "sensor_error"):
            entity = binary_sensor_module.FumisAirBinarySensor(
                coordinator, entry, self._metadata(key)
            )
            self.assertEqual(entity._attr_entity_category, "diagnostic")

    def test_fast_update_metadata_matches_the_required_registers(self):
        expected = {
            const.SENSO_MODEL: {
                "status",
                "door_open",
                "heat_temperature",
                "draft",
                "advice",
                "chamber_temperature",
            },
            const.FUMIS_AIR_MODEL: {
                "status",
                "door_open",
                "heat_temperature",
                "draft",
                "flue_gas_temperature",
            },
        }
        for model, fast_keys in expected.items():
            with self.subTest(model=model):
                actual = {
                    key
                    for key, item in parameters_module.ENTITY_CATALOG[model].items()
                    if item.polling_tier == "fast"
                }
                self.assertEqual(actual, fast_keys)
                self.assertFalse(
                    parameters_module.ENTITY_CATALOG[model][
                        "air_temperature"
                    ].polling_tier == "fast"
                )

    def test_fast_entities_route_separately_and_optional_entities_start_disabled(self):
        async def run():
            fast_coordinator = types.SimpleNamespace(
                data={
                    protocol.REG_STATUS: 4,
                    protocol.REG_ADVICE: 1,
                    protocol.ERR_CONTROL: 0,
                    protocol.ERR_MOTOR: 0,
                    protocol.ERR_SENSOR: 0,
                },
                available=True,
            )
            settings_coordinator = types.SimpleNamespace(
                data={protocol.REG_POWER: 0x10, protocol.REG_FAN: 0x114},
                available=True,
            )
            coordinator = types.SimpleNamespace(
                data={},
                available=True,
                display_mask=(1 << 4) | 1,
                settings_coordinator=settings_coordinator,
                fast_coordinator=fast_coordinator,
            )
            entry = types.SimpleNamespace(
                runtime_data=coordinator,
                data={
                    "address": "AA:BB:CC:DD:EE:FF",
                    "name": "Test Senso",
                    "model": "Tulikivi Senso",
                },
                title="Test Senso",
            )

            sensors = []
            await sensor_module.async_setup_entry(object(), entry, sensors.extend)
            self.assertTrue(sensors)
            by_key = {
                entity.entity_description.key: entity for entity in sensors
            }
            self.assertIs(by_key["status"].coordinator, fast_coordinator)
            self.assertIs(by_key["advice"].coordinator, fast_coordinator)
            self.assertIs(by_key["heat_temperature"].coordinator, fast_coordinator)
            self.assertIs(by_key["air_temperature"].coordinator, coordinator)
            self.assertIs(by_key["power_mode"].coordinator, settings_coordinator)
            self.assertIs(by_key["fan_setting"].coordinator, settings_coordinator)
            self.assertTrue(
                all(
                    entity.coordinator is settings_coordinator
                    for entity in sensors
                    if entity.metadata.group == "parameter"
                )
            )
            self.assertFalse(
                by_key["power_mode"]._attr_entity_registry_enabled_default
            )
            self.assertTrue(
                all(
                    not entity._attr_entity_registry_enabled_default
                    for entity in sensors
                    if entity.metadata.register in advanced.ADVANCED_REGISTERS
                )
            )

            numbers, switches, selects = [], [], []
            await number_module.async_setup_entry(object(), entry, numbers.extend)
            await switch_module.async_setup_entry(object(), entry, switches.extend)
            await select_module.async_setup_entry(object(), entry, selects.extend)
            self.assertEqual(
                {entity.register for entity in numbers},
                {
                    protocol.REG_FAN,
                    protocol.DESIRE_ROOM_TEMP,
                    protocol.REG_MAX_HEAT_CAP_TEMP,
                    protocol.REG_NOMINAL_POWER,
                },
            )
            self.assertEqual(
                {entity.metadata.register for entity in selects},
                {
                    protocol.REG_LED_BRIGHTNESS,
                    protocol.REG_SOUND_VOLUME,
                    protocol.REG_FUEL,
                },
            )
            self.assertTrue(all(
                entity.register not in {sensor.context for sensor in sensors}
                for entity in numbers if entity.register != protocol.REG_FAN
            ))
            self.assertTrue(all(entity.coordinator is settings_coordinator for entity in numbers))
            self.assertTrue(all(entity.coordinator is settings_coordinator for entity in switches))
            self.assertTrue(
                all(entity.coordinator is settings_coordinator for entity in selects)
            )

            binary_sensors = []
            await binary_sensor_module.async_setup_entry(
                object(), entry, binary_sensors.extend
            )
            self.assertIs(
                next(
                    entity
                    for entity in binary_sensors
                    if entity.entity_description.key == "door_open"
                ).coordinator,
                fast_coordinator,
            )
            self.assertTrue(
                all(entity.coordinator is coordinator for entity in binary_sensors if entity.entity_description.key != "door_open")
            )

        asyncio.run(run())

    def test_power_mode_sensor_decodes_bit_and_preserves_identity(self):
        entity = self._sensor("power_mode", {})
        self.assertEqual(entity.entity_description.name, "Operating mode")
        self.assertEqual(entity._attr_entity_category, "diagnostic")

        for value, expected in (
            (0, "Fixed power"),
            (0x0F, "Fixed power"),
            (0x10, "Temperature adjust"),
            (0x1F, "Temperature adjust"),
        ):
            with self.subTest(value=value):
                sensor = self._sensor("power_mode", {protocol.REG_POWER: value})
                self.assertEqual(sensor.native_value, expected)
                self.assertFalse(sensor._attr_entity_registry_enabled_default)
                self.assertEqual(
                    sensor._attr_unique_id,
                    "fumis_air_aa:bb:cc:dd:ee:ff_power_mode",
                )

        missing = self._sensor("power_mode", {})
        self.assertIsNone(missing.native_value)

    def test_heat_cap_sensor_uses_input2_tenths_when_it_agrees(self):
        sensor = self._sensor(
            "heat_temperature",
            {protocol.VAL_TEMP_HEAT: 123, advanced.VAL_INPUT_2: 1234},
        )
        missing_input = self._sensor(
            "heat_temperature", {protocol.VAL_TEMP_HEAT: 123}
        )
        mismatched_input = self._sensor(
            "heat_temperature",
            {protocol.VAL_TEMP_HEAT: 123, advanced.VAL_INPUT_2: 1250},
        )
        boundary_input = self._sensor(
            "heat_temperature",
            {protocol.VAL_TEMP_HEAT: 123, advanced.VAL_INPUT_2: 1240},
        )
        room = self._sensor("air_temperature", {protocol.VAL_TEMP_AIR: 234})

        self.assertEqual(sensor.entity_description.suggested_display_precision, 1)
        self.assertEqual(sensor.native_value, 123.4)
        self.assertEqual(missing_input.native_value, 123)
        self.assertEqual(mismatched_input.native_value, 123)
        self.assertEqual(boundary_input.native_value, 124.0)
        self.assertEqual(sensor.entity_description.state_class, "measurement")
        self.assertEqual(room.native_value, 23.4)
        self.assertEqual(
            room.entity_description.suggested_display_precision,
            1,
        )
        chamber = self._sensor("chamber_temperature", {})
        self.assertEqual(chamber.entity_description.suggested_display_precision, 0)

    def test_advice_sensor_is_human_readable_and_not_diagnostic(self):
        expected = {
            0: "No advice",
            1: "Add wood",
            2: "Do not add wood",
            3: "Do not add pellets",
        }
        for code, label in expected.items():
            with self.subTest(code=code):
                sensor = self._sensor("advice", {protocol.REG_ADVICE: code})
                self.assertEqual(sensor.native_value, label)

        sensor = self._sensor("advice", {protocol.REG_ADVICE: 1})
        self.assertEqual(sensor.entity_description.name, "Advice")
        self.assertIsNone(sensor._attr_entity_category)
        self.assertEqual(
            sensor._attr_unique_id, "fumis_air_aa:bb:cc:dd:ee:ff_advice"
        )

if __name__ == "__main__":
    unittest.main()
