"""Polling and command coordination for Fumis AIR controllers."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .ble import FumisAirConnectionError, async_connect, async_disconnect
from .const import CONF_MODEL, FUMIS_AIR_MODEL, PERCENTAGE_PRESETS, SENSO_MODEL
from .parameters import (
    ENTITY_CATALOG,
    ROOM_TARGET,
    WRITABLE_NUMBERS,
    WRITABLE_SELECTS,
)
from .protocol import (
    DESIRE_ROOM_TEMP,
    DISP_VAL_USER,
    FIRMWARE_VERSION_REGISTERS,
    FUEL_TYPE_OPTIONS,
    FumisAirProtocolError,
    GattClient,
    MAX_LIMIT_ROOM_TEMP,
    MIN_LIMIT_ROOM_TEMP,
    REG_FUEL,
    REG_FUEL_COUNT,
    REG_FAN,
    REG_MAX_HEAT_CAP_TEMP,
    REG_NOMINAL_POWER,
    REG_ROOM_TEMP_EN,
    REG_SET_FUEL,
    REG_STATUS,
    ReadBatch,
    VAL_INPUT_2,
    VAL_TEMP_HEAT,
    build_read_batches,
    decode_firmware_version,
    displayed_registers,
    fan_automatic,
    fan_position_percent,
    fan_register_value,
    fuel_type_options,
    read_register_batch,
    read_registers,
    status_names,
    write_register,
)

if TYPE_CHECKING:
    from . import FumisAirConfigEntry

ROOM_TEMP_INSTALLER_MASK = 0x01
ROOM_TEMP_USER_MASK = 0x02
POLL_RETRY_DELAY_SECONDS = 3
POLL_RETRY_ATTEMPTS = 2
POLL_FRAME_TIMEOUT_SECONDS = 2
FAST_REFRESH_SECONDS = 1
TELEMETRY_REFRESH_SECONDS = 30
SETTINGS_REFRESH_SECONDS = 300
TRANSPORT_ERRORS = (BleakError, OSError, FumisAirConnectionError, TimeoutError)
OPTIONAL_READ_ERRORS = (*TRANSPORT_ERRORS, FumisAirProtocolError)
_LOGGER = logging.getLogger(__name__)
_NUMBER_WRITE_RULES = {
    REG_FAN: ("Manual air intake", "%", "Unable to set manual air intake", {}),
    DESIRE_ROOM_TEMP: (
        "Target temperature",
        "°C",
        "Unable to set target temperature",
        {
            "finite_error": "Target temperature must be a finite value",
            "range_error": "Target temperature is outside the catalog limits",
            "step_error": "Target temperature must use 0.1°C steps",
        },
    ),
    REG_NOMINAL_POWER: (
        "Nominal power",
        "kW",
        "Unable to set nominal power",
        {},
    ),
    REG_MAX_HEAT_CAP_TEMP: (
        "Maximum heat-cap temperature",
        "°C",
        "Unable to set maximum heat-cap temperature",
        {},
    ),
}


def room_temperature_range(data: dict[int, int]) -> tuple[int, int] | None:
    """Return a sane, live device range in register tenths of a degree."""
    minimum = data.get(MIN_LIMIT_ROOM_TEMP)
    maximum = data.get(MAX_LIMIT_ROOM_TEMP)
    if (
        minimum is None
        or maximum is None
        or minimum < ROOM_TARGET.raw_minimum
        or maximum > ROOM_TARGET.raw_maximum
        or minimum >= maximum
    ):
        return None
    return minimum, maximum


class FumisAirCoordinator(DataUpdateCoordinator[dict[int, int]]):
    """Serialize BLE polling and commands over one retained connection."""

    config_entry: FumisAirConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: FumisAirConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=config_entry.title,
            update_interval=timedelta(seconds=TELEMETRY_REFRESH_SECONDS),
        )
        self.config_entry = config_entry
        self.address = config_entry.data["address"]
        self.name = config_entry.title
        self.model = config_entry.data.get(CONF_MODEL, SENSO_MODEL)
        self._transaction_lock = asyncio.Lock()
        self._client: GattClient | None = None
        self._closed = False
        self.display_mask: int | None = None
        self.firmware_version: str | None = None
        self._input_2_value: int | None = None
        self._settings_write_generation = 0
        self._settings_write_values: dict[int, tuple[int, int | None]] = {}
        self.settings_coordinator = DataUpdateCoordinator[dict[int, int]](
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=f"{config_entry.title} settings",
            update_method=self._async_update_settings,
            update_interval=timedelta(seconds=SETTINGS_REFRESH_SECONDS),
        )
        self.settings_coordinator.data = {}
        self.fast_coordinator = DataUpdateCoordinator(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=f"{config_entry.title} fast telemetry",
            update_method=self._async_update_fast_data,
            update_interval=timedelta(seconds=FAST_REFRESH_SECONDS),
        )
        self.fast_coordinator.data = {}

    async def _async_get_client(self) -> GattClient:
        """Return the retained connection, reconnecting if it was lost."""
        if self._client is not None and self._client.is_connected:
            return self._client
        await self._async_drop_client()
        self._client = await async_connect(self.hass, self.address)
        return self._client

    async def _async_drop_client(self) -> None:
        """Forget and disconnect the retained peripheral connection."""
        client = self._client
        self._client = None
        if client is not None:
            await async_disconnect(client)

    @asynccontextmanager
    async def _async_client_transaction(self) -> AsyncIterator[GattClient]:
        """Serialize a client operation and discard broken or cancelled links."""
        wait_started = time.perf_counter()
        async with self._transaction_lock:
            _LOGGER.debug(
                "BLE transaction lock wait took %.3f seconds",
                time.perf_counter() - wait_started,
            )
            try:
                if self._closed:
                    raise FumisAirConnectionError("The controller entry is unloaded")
                connect_started = time.perf_counter()
                try:
                    client = await self._async_get_client()
                finally:
                    _LOGGER.debug(
                        "BLE connection acquisition took %.3f seconds",
                        time.perf_counter() - connect_started,
                    )
                yield client
            except asyncio.CancelledError:
                await self._async_drop_client()
                raise
            except (*TRANSPORT_ERRORS, FumisAirProtocolError):
                await self._async_drop_client()
                raise

    @asynccontextmanager
    async def _async_setting_transaction(
        self, error_message: str
    ) -> AsyncIterator[GattClient]:
        """Run a setting operation and translate BLE errors for Home Assistant."""
        try:
            async with self._async_client_transaction() as client:
                yield client
        except OPTIONAL_READ_ERRORS as err:
            raise HomeAssistantError(f"{error_message}: {err}") from err

    async def async_shutdown(self) -> None:
        """Disconnect from the controller when its config entry unloads."""
        self._closed = True
        await self.settings_coordinator.async_shutdown()
        await self.fast_coordinator.async_shutdown()
        await super().async_shutdown()
        async with self._transaction_lock:
            await self._async_drop_client()

    async def _async_update_data(self) -> dict[int, int]:
        return await self._async_poll(self._async_read_data, "telemetry")

    async def _async_update_fast_data(self) -> dict[int, int]:
        return await self._async_poll(self._async_read_fast_data, "fast telemetry")

    async def _async_poll(
        self, read: Callable[[], Awaitable[dict[int, int]]], subject: str
    ) -> dict[int, int]:
        """Retry a poll after a transport failure on a fresh link."""
        for attempt in range(POLL_RETRY_ATTEMPTS):
            try:
                return await read()
            except FumisAirProtocolError as err:
                raise UpdateFailed(
                    f"Unable to read controller {subject}: {err}"
                ) from err
            except TRANSPORT_ERRORS as err:
                if self._closed or attempt + 1 == POLL_RETRY_ATTEMPTS:
                    raise UpdateFailed(
                        f"Unable to read controller {subject} after retry: {err}"
                    ) from err
                _LOGGER.debug(
                    "Unable to read controller %s; retrying in %s seconds",
                    subject,
                    POLL_RETRY_DELAY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(POLL_RETRY_DELAY_SECONDS)
        raise UpdateFailed(f"Unable to read controller {subject}")

    async def _async_update_settings(self) -> dict[int, int]:
        """Poll only enabled settings and their live dependencies."""
        write_generation = self._settings_write_generation
        contexts = set(self.settings_coordinator.async_contexts())
        registers = list(
            dict.fromkeys(
                item.register
                for item in ENTITY_CATALOG[self.model].values()
                if item.group == "settings" and item.register in contexts
            )
        )
        if self.model != FUMIS_AIR_MODEL:
            if DESIRE_ROOM_TEMP in contexts:
                registers.extend((MIN_LIMIT_ROOM_TEMP, MAX_LIMIT_ROOM_TEMP))
            if REG_FUEL in contexts:
                registers.append(REG_FUEL_COUNT)
        data: dict[int, int] = {}
        if registers:
            async def read() -> dict[int, int]:
                # Hold the lock through the last frame so a write cannot overtake this snapshot.
                async with self._async_client_transaction() as client:
                    return await self._async_read_registers(registers, client=client)

            data.update(await self._async_poll(read, "settings"))

        settings_registers = set(registers)
        parameter_registers = tuple(
            dict.fromkeys(
                parameter.register
                for parameter in ENTITY_CATALOG[self.model].values()
                if parameter.group == "parameter"
                and parameter.register in contexts
                and parameter.register not in settings_registers
            )
        )
        for batch in build_read_batches(parameter_registers):
            try:
                # Optional catalog frames release the lock between requests.
                data.update(await self._async_read_batch(batch))
            except OPTIONAL_READ_ERRORS:
                _LOGGER.debug(
                    "Unable to read optional parameter frame %s",
                    batch.registers,
                    exc_info=True,
                )
        for register, (generation, value) in self._settings_write_values.items():
            if generation > write_generation:
                if value is None:
                    data.pop(register, None)
                else:
                    data[register] = value
        return data

    async def async_read_firmware_version(self) -> None:
        """Read optional device metadata before forwarding entity setup."""
        try:
            firmware = await self._async_read_registers(FIRMWARE_VERSION_REGISTERS)
        except OPTIONAL_READ_ERRORS:
            _LOGGER.debug("Unable to read optional controller firmware version")
        else:
            self.firmware_version = decode_firmware_version(
                *(firmware[register] for register in FIRMWARE_VERSION_REGISTERS)
            )

    async def _async_read_registers(
        self,
        registers: Sequence[int],
        *,
        client: GattClient | None = None,
        optional_input_2: bool = False,
    ) -> dict[int, int]:
        """Read deduplicated registers in five-word frames."""
        result: dict[int, int] = {}

        for batch in build_read_batches(tuple(dict.fromkeys(registers))):
            try:
                values = await self._async_read_batch(batch, client)
            except OPTIONAL_READ_ERRORS:
                if not optional_input_2 or VAL_INPUT_2 not in batch.registers:
                    raise
                _LOGGER.debug(
                    "Unable to read optional heat-cap precision; retrying without Input 2"
                )
                fallback = build_read_batches(
                    tuple(
                        register for register in batch.registers if register != VAL_INPUT_2
                    )
                )
                if not fallback:
                    continue
                values = await self._async_read_batch(fallback[0], client)
            result.update(values)
        return result

    async def _async_read_batch(
        self, batch: ReadBatch, client: GattClient | None = None
    ) -> dict[int, int]:
        """Read one already-built frame through the shared transaction path."""
        if client is None:
            async with self._async_client_transaction() as connection:
                return await self._async_read_frame(connection, batch)
        return await self._async_read_frame(client, batch)

    async def _async_read_frame(
        self, client: GattClient, batch: ReadBatch
    ) -> dict[int, int]:
        return await asyncio.wait_for(
            read_register_batch(client, batch), timeout=POLL_FRAME_TIMEOUT_SECONDS
        )

    async def _async_read_data(self) -> dict[int, int]:
        """Read enabled non-fast telemetry in packed frames."""
        contexts = set(self.async_contexts())
        catalog = ENTITY_CATALOG[self.model]
        fast_contexts = set(self.fast_coordinator.async_contexts())
        fast_registers = {
            row.register for row in catalog.values() if row.polling_tier == "fast"
        }
        mask = self.display_mask
        registers = [DISP_VAL_USER] if mask is None else []
        gated = {}
        for row in catalog.values():
            if (
                row.polling_tier == "telemetry"
                and row.display_bit is None
                and row.register in contexts
            ):
                if row.input_role is None:
                    registers.append(row.register)
                else:
                    gated[row.register] = catalog[row.input_role].register
        registers.extend(gated.values())
        optional_input_2 = VAL_INPUT_2 not in contexts
        input_2_requested = VAL_INPUT_2 in registers
        data = await self._async_read_registers(
            registers, optional_input_2=optional_input_2
        )
        if mask is None:
            mask = data[DISP_VAL_USER]

        visible_registers = displayed_registers(mask, self.model)
        display_reads = tuple(
            register
            for register in visible_registers
            if register in contexts and register not in fast_registers
        )
        if (
            self.model == SENSO_MODEL
            and VAL_TEMP_HEAT in fast_contexts
            and VAL_TEMP_HEAT in visible_registers
            and VAL_INPUT_2 not in data
        ):
            display_reads += (VAL_INPUT_2,)
            input_2_requested = True
        if display_reads:
            data.update(
                await self._async_read_registers(
                    display_reads, optional_input_2=optional_input_2
                )
            )

        dependent_registers = tuple(
            register
            for register, role in gated.items()
            if register not in data and role in data and 1 <= data[role] < 10
        )
        if dependent_registers:
            data.update(
                await self._async_read_registers(
                    dependent_registers,
                    optional_input_2=optional_input_2,
                )
            )

        self.display_mask = mask
        if input_2_requested:
            self._input_2_value = data.get(VAL_INPUT_2)
        return data

    async def _async_read_fast_data(self) -> dict[int, int]:
        """Read enabled fast-frequency values in one packed frame."""
        if self.display_mask is None:
            mask_data = await self._async_read_registers((DISP_VAL_USER,))
            self.display_mask = mask_data[DISP_VAL_USER]
        contexts = set(self.fast_coordinator.async_contexts())
        visible_registers = set(displayed_registers(self.display_mask, self.model))
        registers = tuple(
            dict.fromkeys(
                row.register
                for row in ENTITY_CATALOG[self.model].values()
                if row.polling_tier == "fast"
                and row.register in contexts
                and (row.display_bit is None or row.register in visible_registers)
            )
        )
        data = await self._async_read_registers(registers)
        if REG_STATUS in data and data[REG_STATUS] not in status_names(self.model):
            _LOGGER.debug("Ignoring unknown controller status code")
            data.pop(REG_STATUS)
        if (
            self.model == SENSO_MODEL
            and VAL_TEMP_HEAT in data
            and self._input_2_value is not None
        ):
            data[VAL_INPUT_2] = self._input_2_value
        return data

    async def _async_write_and_readback(
        self,
        client: GattClient,
        register: int,
        value: int,
        *,
        readback_register: int | None = None,
    ) -> None:
        """Write once, then publish the controller's current setting if readable."""
        await write_register(client, register, value)
        if readback_register is None:
            readback_register = register
        self._settings_write_generation += 1
        write_generation = self._settings_write_generation
        try:
            values = await self._async_read_registers((readback_register,), client=client)
        except OPTIONAL_READ_ERRORS:
            self._settings_write_values[readback_register] = (write_generation, None)
            await self._async_drop_client()
            _LOGGER.debug(
                "Setting register %s was written but its readback failed",
                readback_register,
                exc_info=True,
            )
            self._schedule_post_write_refreshes()
            return

        self._settings_write_values[readback_register] = (
            write_generation,
            values.get(readback_register),
        )
        self.settings_coordinator.async_set_updated_data(
            self.settings_coordinator.data | values
        )
        self._schedule_post_write_refreshes()

    def _schedule_post_write_refreshes(self) -> None:
        """Refresh non-fast telemetry and configuration after a setting write."""
        self.hass.async_create_task(self.async_request_refresh())
        self.hass.async_create_task(self.settings_coordinator.async_request_refresh())

    async def async_set_number(self, register: int, value: float) -> None:
        """Validate and write any exposed numeric setting."""
        item = WRITABLE_NUMBERS[self.model].get(register)
        if item is None or item.minimum is None or item.maximum is None:
            raise HomeAssistantError("Unsupported controller setting")
        if register != REG_FAN and self.model == FUMIS_AIR_MODEL:
            raise HomeAssistantError("Numeric settings are unavailable for Fumis AIR")
        rule = _NUMBER_WRITE_RULES.get(register)
        if rule is None:
            raise HomeAssistantError("Unsupported controller setting")
        setting_name, unit, error_message, validation = rule
        raw_value = _fixed_setting_value(
            value,
            (item.minimum, item.maximum),
            setting_name,
            unit,
            item.scale,
            **validation,
        )

        async with self._async_setting_transaction(error_message) as client:
            if register == REG_FAN:
                await self._async_write_air_intake(
                    client, fan_register_value(raw_value, self.model)
                )
                return
            if register == DESIRE_ROOM_TEMP:
                current = await read_registers(
                    client, (MIN_LIMIT_ROOM_TEMP, MAX_LIMIT_ROOM_TEMP)
                )
                limits = room_temperature_range(current)
                if limits is None:
                    raise HomeAssistantError(
                        "The device did not report valid temperature limits"
                    )
                if not limits[0] <= raw_value <= limits[1]:
                    raise HomeAssistantError(
                        "Target temperature is outside the live device limits"
                    )
            elif register in (REG_MAX_HEAT_CAP_TEMP, REG_NOMINAL_POWER):
                current = await read_registers(client, (register,))
                current_value = current.get(register)
                if (
                    current_value is None
                    or not item.raw_minimum <= current_value <= item.raw_maximum
                ):
                    setting_name = (
                        "nominal power"
                        if register == REG_NOMINAL_POWER
                        else "maximum heat-cap temperature"
                    )
                    raise HomeAssistantError(
                        f"The device did not report a supported {setting_name}"
                    )
            await self._async_write_and_readback(client, register, raw_value)

    async def async_set_percentage_select(self, register: int, value: int) -> None:
        """Set a Senso brightness or volume preset."""
        item = WRITABLE_SELECTS[self.model].get(register)
        if (
            item is None
            or item.entity_key not in {"led_brightness", "sound_volume"}
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value not in {level for _, level in PERCENTAGE_PRESETS}
        ):
            raise HomeAssistantError("Unsupported controller setting")
        async with self._async_setting_transaction(
            "Unable to set controller setting"
        ) as client:
            await self._async_write_and_readback(client, register, value)

    async def _async_write_air_intake(
        self, client: GattClient, value: int, current: dict[int, int] | None = None
    ) -> None:
        """Validate the live fan mode before sending an air-intake command."""
        if current is None:
            current = await read_registers(client, (REG_FAN,))
        if fan_automatic(current.get(REG_FAN), self.model) is None:
            raise HomeAssistantError(
                "The device did not report a valid air-intake setting"
            )
        await self._async_write_and_readback(client, REG_FAN, value)

    async def async_set_air_automatic(self, enabled: bool) -> None:
        """Switch the controller between automatic and manual air intake."""
        async with self._async_setting_transaction(
            "Unable to change automatic air intake"
        ) as client:
            current = await read_registers(client, (REG_FAN,))
            if enabled:
                requested = fan_register_value(0, self.model, automatic=True)
            elif self.model == FUMIS_AIR_MODEL:
                requested = fan_register_value(0, self.model)
            else:
                setting = current[REG_FAN] & 0xFF
                if fan_position_percent(setting) is None:
                    requested = fan_register_value(0, self.model)
                else:
                    requested = setting
            await self._async_write_air_intake(client, requested, current)

    async def async_set_fuel_type(self, option: str) -> None:
        """Set a supported standard fuel choice."""
        if self.model == FUMIS_AIR_MODEL:
            raise HomeAssistantError("Fuel selection is unavailable for Fumis AIR")
        if not isinstance(option, str) or option not in FUEL_TYPE_OPTIONS:
            raise HomeAssistantError("Unsupported fuel type")

        async with self._async_setting_transaction("Unable to set fuel type") as client:
            current = await read_registers(client, (REG_FUEL, REG_FUEL_COUNT))
            supported_options = fuel_type_options(current.get(REG_FUEL_COUNT))
            if option not in supported_options:
                raise HomeAssistantError(
                    "The controller does not support this fuel type"
                )

            requested = FUEL_TYPE_OPTIONS.index(option) + 1
            await self._async_write_and_readback(
                client, REG_SET_FUEL, requested, readback_register=REG_FUEL
            )

    async def async_set_room_temperature_control(self, enabled: bool) -> None:
        """Change only the user bit after verifying installer permission."""
        if self.model == FUMIS_AIR_MODEL:
            raise HomeAssistantError(
                "Room-temperature control is unavailable for Fumis AIR"
            )
        async with self._async_setting_transaction(
            "Unable to set room-temperature control"
        ) as client:
            current = await read_registers(client, (REG_ROOM_TEMP_EN,))
            current_value = current.get(REG_ROOM_TEMP_EN)
            if current_value is None:
                raise HomeAssistantError(
                    "The controller did not report room-temperature settings"
                )
            if not current_value & ROOM_TEMP_INSTALLER_MASK:
                raise HomeAssistantError(
                    "Room-temperature control is disabled by the installer"
                )

            requested = current_value & ~ROOM_TEMP_USER_MASK
            if enabled:
                requested |= ROOM_TEMP_USER_MASK
            await self._async_write_and_readback(client, REG_ROOM_TEMP_EN, requested)


def _fixed_setting_value(
    value: float,
    limits: tuple[float, float],
    name: str,
    unit: str = "",
    scale: float = 1,
    *,
    finite_error: str | None = None,
    range_error: str | None = None,
    step_error: str | None = None,
) -> int:
    """Validate a finite setting and convert its value to raw register units."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise HomeAssistantError(finite_error or f"{name} must be a finite number")
    result = round(value / scale)
    default_error = (
        f"{name} must be a whole value from {limits[0]} to {limits[1]}{unit}"
        if scale == 1
        else f"{name} must be from {limits[0]} to {limits[1]}{unit} "
        f"in {scale:g}{unit} steps"
    )
    if not limits[0] <= value <= limits[1]:
        raise HomeAssistantError(range_error or default_error)
    if not math.isclose(value / scale, result, abs_tol=1e-6):
        raise HomeAssistantError(step_error or range_error or default_error)
    return result
