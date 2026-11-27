"""Fumis AIR and Tulikivi Senso BLE register protocol."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .const import FUMIS_AIR_MODEL, SENSO_MODEL
from .parameters import (
    APK_PARAMETERS,
    ENTITY_CATALOG,
    ROOM_TARGET,
    SIGNED_REGISTERS,
)

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_ADDRESS_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_DATA_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

_SENSO = ENTITY_CATALOG[SENSO_MODEL]
_SENSO_PARAMETERS = APK_PARAMETERS[SENSO_MODEL]
DESIRE_ROOM_TEMP = ROOM_TARGET.register
MAX_LIMIT_ROOM_TEMP = _SENSO_PARAMETERS["022-007"].register
MIN_LIMIT_ROOM_TEMP = _SENSO_PARAMETERS["022-008"].register
REG_ROOM_TEMP_EN = _SENSO["room_temperature_control"].register
DISP_VAL_USER = 10755
REG_ADVICE = _SENSO["advice"].register
REG_POWER = _SENSO["power_mode"].register
POWER_MODE_TEMP_ADJUST_BIT = 0x10
REG_STATUS = _SENSO["status"].register
ERR_SENSOR = _SENSO["sensor_error"].register
ERR_MOTOR = _SENSO["motor_error"].register
ERR_CONTROL = _SENSO["control_error"].register
REG_FIRMWARE_VERSION_HI = 10501
REG_FIRMWARE_VERSION_LO = 10502
FIRMWARE_VERSION_REGISTERS = (REG_FIRMWARE_VERSION_HI, REG_FIRMWARE_VERSION_LO)
REG_LED_BRIGHTNESS = _SENSO["led_brightness"].register
REG_SOUND_VOLUME = _SENSO["sound_volume"].register
REG_FAN = _SENSO["manual_air_intake"].register
REG_FUEL_COUNT = _SENSO_PARAMETERS["030-013"].register
REG_FUEL = _SENSO["fuel_type"].register
REG_SET_FUEL = 11271
REG_NOMINAL_POWER = _SENSO["nominal_power"].register
REG_MAX_HEAT_CAP_TEMP = _SENSO["max_heat_cap_temperature"].register
VAL_TEMP_AIR = _SENSO["air_temperature"].register
VAL_TEMP_WATER = _SENSO["water_temperature"].register
VAL_TEMP_CHAMBER = _SENSO["chamber_temperature"].register
VAL_TEMP_OVEN = _SENSO["oven_temperature"].register
VAL_TEMP_HEAT = _SENSO["heat_temperature"].register
VAL_DRAFT = _SENSO["draft"].register
VAL_LAMBDA = _SENSO["lambda"].register
VAL_HEAT_CAP = _SENSO["heat_capacity"].register
VAL_REG_POS = _SENSO["regulator_position"].register
VAL_HEAT_OUTPUT = _SENSO["heat_output"].register
VAL_INPUT_2 = _SENSO["input_2_value"].register

_DISPLAY_ROWS = {
    model: {row.register: row for row in rows.values() if row.display_bit is not None}
    for model, rows in ENTITY_CATALOG.items()
}

FUEL_TYPE_OPTIONS = ("Soft wood", "Hard wood", "Pellets")
FUEL_TYPE_MASK = 0x3F
MAX_APP_FUEL_TYPE_COUNT = 5
FAN_AUTOMATIC_MASK = 0x100
SENSO_FAN_POSITION_MIN = 20
SENSO_FAN_POSITION_MAX = 100

SENSO_STATUS_NAMES = {
    0: "Standby",
    1: "Ignition",
    2: "Burning",
    3: "Embers",
    4: "Door open",
}
FUMIS_STATUS_NAMES = {
    0: "Standby",
    1: "Firing up",
    2: "Burning",
    3: "Refill",
    4: "Cooling",
    5: "Door open",
    6: "Overheating",
    7: "Safe mode",
}
ADVICE_NAMES = {
    0: "No advice",
    1: "Add wood",
    2: "Do not add wood",
    3: "Do not add pellets",
}

WORDS_PER_REQUEST = 5
FRAME_SIZE = WORDS_PER_REQUEST * 2
READ_FRAME_DELAY_SECONDS = 0.1
COMMAND_DELAY_SECONDS = 0.1
_LOGGER = logging.getLogger(__name__)


class FumisAirProtocolError(Exception):
    """The peripheral returned data that does not match the controller protocol."""


class FumisAirResponseSizeError(FumisAirProtocolError):
    """The peripheral response length does not match its read request."""


class GattClient(Protocol):
    """The subset of a BLE client used by this protocol."""

    @property
    def is_connected(self) -> bool: ...

    async def write_gatt_char(
        self, characteristic: str, data: bytes, *, response: bool = False
    ) -> None: ...

    async def read_gatt_char(self, characteristic: str) -> bytearray: ...


@dataclass(frozen=True)
class ReadBatch:
    """One read request and the register IDs whose values are meaningful."""

    registers: tuple[int, ...]
    frame: bytes


def _validate_register(register: int) -> int:
    if not isinstance(register, int) or not 0 <= register <= 0xFFFF:
        raise ValueError(f"Invalid register ID: {register!r}")
    return register


def _encode_word(value: int) -> bytes:
    if not isinstance(value, int) or not -0x8000 <= value <= 0xFFFF:
        raise ValueError(f"Invalid 16-bit value: {value!r}")
    return (value & 0xFFFF).to_bytes(2, "big")


def encode_read_request(registers: Sequence[int]) -> bytes:
    """Encode IDs, padding requests shorter than five with the last ID."""
    if not registers:
        raise ValueError("A read request must contain at least one register")
    if len(registers) > WORDS_PER_REQUEST:
        raise ValueError(
            f"A read request supports at most {WORDS_PER_REQUEST} registers"
        )
    words = [_validate_register(register) for register in registers]
    words.extend([words[-1]] * (WORDS_PER_REQUEST - len(words)))
    return b"".join(register.to_bytes(2, "big") for register in words)


def build_read_batches(registers: Sequence[int]) -> tuple[ReadBatch, ...]:
    """Split register reads into five-word frames."""
    batches = []
    for start in range(0, len(registers), WORDS_PER_REQUEST):
        meaningful_registers = tuple(registers[start : start + WORDS_PER_REQUEST])
        batches.append(
            ReadBatch(meaningful_registers, encode_read_request(meaningful_registers))
        )
    return tuple(batches)


def decode_register_batch(registers: Sequence[int], payload: bytes) -> dict[int, int]:
    """Decode the response values for the requested registers only."""
    if not registers:
        raise ValueError("A response must correspond to at least one register")
    if len(registers) > WORDS_PER_REQUEST:
        raise ValueError(f"A response supports at most {WORDS_PER_REQUEST} registers")
    if len(payload) != FRAME_SIZE:
        raise FumisAirResponseSizeError(
            f"Expected a {FRAME_SIZE}-byte register response, received {len(payload)} bytes"
        )

    values: dict[int, int] = {}
    for index, register in enumerate(registers):
        raw = int.from_bytes(payload[index * 2 : index * 2 + 2], "big")
        if register in SIGNED_REGISTERS and raw & 0x8000:
            raw -= 0x10000
        values[register] = raw
    return values


def displayed_registers(
    display_mask: int, model: str = SENSO_MODEL
) -> tuple[int, ...]:
    """Return the overview sensor registers enabled by the app's display mask."""
    if not isinstance(display_mask, int) or not 0 <= display_mask <= 0xFFFF:
        raise ValueError(f"Invalid display mask: {display_mask!r}")
    return tuple(
        row.register for row in _DISPLAY_ROWS[model].values()
        if display_mask & (1 << row.display_bit)
    )


def decode_status(value: int | None, model: str = SENSO_MODEL) -> str | None:
    """Return the app's human-readable label for an overview status code."""
    if value is None:
        return None
    return status_names(model).get(value, f"Unknown ({value})")


def decode_advice(value: int | None) -> str | None:
    """Return the app's human-readable label for an overview advice code."""
    if value is None:
        return None
    return ADVICE_NAMES.get(value, f"Unknown ({value})")


def decode_power_mode(value: int | None) -> str | None:
    """Return the app's human-readable operating mode from the power register."""
    if value is None:
        return None
    if value & POWER_MODE_TEMP_ADJUST_BIT:
        return "Temperature adjust"
    return "Fixed power"


def decode_heat_cap_temperature(
    overview_value: int | None, input_value: int | None
) -> int | float | None:
    """Use Input 2 tenths only when it agrees with the overview temperature."""
    if overview_value is None:
        return None
    if input_value is not None:
        input_temperature = input_value / 10
        if abs(input_temperature - overview_value) <= 1:
            return input_temperature
    return overview_value


def is_door_open(value: int | None, model: str = SENSO_MODEL) -> bool | None:
    """Return whether the controller status reports an open door."""
    if value not in status_names(model):
        return None
    return value == (5 if model == FUMIS_AIR_MODEL else 4)


def has_error(value: int | None) -> bool | None:
    """Return whether an error register contains any active error bits."""
    if value is None:
        return None
    return value != 0


def fuel_type_options(count: int | None) -> tuple[str, ...]:
    """Return supported standard fuel choices without exposing custom presets."""
    if (
        count is None
        or isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= MAX_APP_FUEL_TYPE_COUNT
    ):
        return ()
    return FUEL_TYPE_OPTIONS[: min(count, len(FUEL_TYPE_OPTIONS))]


def selected_fuel_type(value: int | None, count: int | None) -> str | None:
    """Return a selected standard fuel, leaving custom/unsupported values unknown."""
    options = fuel_type_options(count)
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    selected = value & FUEL_TYPE_MASK
    if not 1 <= selected <= len(options):
        return None
    return options[selected - 1]


def fan_automatic(value: int | None, model: str = SENSO_MODEL) -> bool | None:
    """Return the automatic flag only for a valid fan-setting register value."""
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 0x1FF
    ):
        return None
    automatic = bool(value & FAN_AUTOMATIC_MASK)
    position = value & 0xFF
    if model == FUMIS_AIR_MODEL:
        return automatic if position <= 100 else None
    if automatic and position == 0:
        return True
    if not SENSO_FAN_POSITION_MIN <= position <= SENSO_FAN_POSITION_MAX:
        return None
    return automatic


def fan_position_percent(value: int | None) -> int | None:
    """Convert Senso's actual regulator position to the app's 0–100 scale."""
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, int)
        or not SENSO_FAN_POSITION_MIN <= value <= SENSO_FAN_POSITION_MAX
    ):
        return None
    return senso_fan_position_percent(value)


def senso_fan_position_percent(value: int | None) -> int | None:
    """Scale a Senso regulator reading, truncating toward zero."""
    if value is None:
        return None
    return int(
        (value - SENSO_FAN_POSITION_MIN)
        * 100
        / (SENSO_FAN_POSITION_MAX - SENSO_FAN_POSITION_MIN)
    )


def fan_register_value(
    percent: int, model: str = SENSO_MODEL, *, automatic: bool = False
) -> int:
    """Encode the app's model-specific manual air setting and mode bit."""
    if (
        isinstance(percent, bool)
        or not isinstance(percent, int)
        or not 0 <= percent <= 100
    ):
        raise ValueError("Manual air intake must be a whole percentage from 0 to 100")
    if model == FUMIS_AIR_MODEL:
        position = percent
    else:
        position = SENSO_FAN_POSITION_MIN + (
            (SENSO_FAN_POSITION_MAX - SENSO_FAN_POSITION_MIN) * percent // 100
        )
    return position | (FAN_AUTOMATIC_MASK if automatic else 0)


def validate_probe_registers(
    values: dict[int, int], model: str = SENSO_MODEL
) -> None:
    """Check values that identify a plausible controller overview response."""
    if values.get(REG_STATUS) not in status_names(model):
        raise FumisAirProtocolError("The controller returned an unknown status code")
    display_mask = values.get(DISP_VAL_USER)
    if display_mask is None or not 0 <= display_mask <= 0xFFFF:
        raise FumisAirProtocolError("The controller returned an invalid display mask")


def status_names(model: str = SENSO_MODEL) -> dict[int, str]:
    """Return the app's status labels for the selected controller profile."""
    return FUMIS_STATUS_NAMES if model == FUMIS_AIR_MODEL else SENSO_STATUS_NAMES


def probe_registers(model: str = SENSO_MODEL) -> tuple[int, ...]:
    """Return the small register set used to confirm a controller profile."""
    if model == FUMIS_AIR_MODEL:
        return REG_STATUS, DISP_VAL_USER
    return REG_STATUS, REG_ADVICE, DISP_VAL_USER


def decode_firmware_version(high: int | None, low: int | None) -> str | None:
    """Decode the app's packed controller firmware version fields."""
    if high is None or low is None or (high, low) in {(0, 0), (0xFFFF, 0xFFFF)}:
        return None
    major = high & 0xFF
    build = (high >> 8) & 0x3F
    minor = (low >> 7) & 0x3F
    patch = low & 0x7F
    version = f"{major}.{minor}.{patch}"
    if high & 0x8000:
        return f"{version}-a.{build}"
    if high & 0x4000:
        return f"{version}-b.{build}"
    return version


def error_details(
    register: int, value: int | None, model: str = SENSO_MODEL
) -> list[str]:
    """Return readable details for error bits documented by the selected app."""
    if value is None:
        return []
    details = _ERROR_DETAILS.get(model, _ERROR_DETAILS[SENSO_MODEL]).get(
        register, {}
    )
    active = [description for bit, description in details.items() if value & (1 << bit)]
    known_mask = sum(1 << bit for bit in details)
    active.extend(
        f"Unknown error bit {bit}"
        for bit in range(16)
        if value & (1 << bit) and not known_mask & (1 << bit)
    )
    return active


_COMMON_CONTROL_ERRORS = {
    0: "Battery low",
    1: "System temperature too high",
    2: "Overtemperature",
}
_SENSO_SENSOR_NAMES = ("Firebox", "Stone", "Room", "Door")
_ERROR_DETAILS = {
    FUMIS_AIR_MODEL: {
        ERR_CONTROL: _COMMON_CONTROL_ERRORS,
        ERR_MOTOR: {bit: f"Motor {bit + 1} error" for bit in range(3)},
        ERR_SENSOR: {
            **{bit: f"Input {bit + 1} sensor open" for bit in range(7)},
            **{bit: f"Input {bit - 7} sensor short" for bit in range(8, 15)},
        },
    },
    SENSO_MODEL: {
        ERR_CONTROL: {
            **_COMMON_CONTROL_ERRORS,
            3: "Too many overheating events; contact Tulikivi service",
            4: "Room overheating",
        },
        ERR_MOTOR: {bit: f"Motor {bit + 1} error" for bit in (3, 4)},
        ERR_SENSOR: {
            **{
                bit: f"{name} sensor open"
                for bit, name in enumerate(_SENSO_SENSOR_NAMES)
            },
            **{
                bit + 8: f"{name} sensor short"
                for bit, name in enumerate(_SENSO_SENSOR_NAMES)
            },
        },
    },
}


def encode_write(register: int, value: int) -> tuple[bytes, bytes]:
    """Encode a register write as repeated five-word address and value frames."""
    address_word = _encode_word(_validate_register(register))
    value_word = _encode_word(value)
    return address_word * WORDS_PER_REQUEST, value_word * WORDS_PER_REQUEST


async def read_register_batch(client: GattClient, batch: ReadBatch) -> dict[int, int]:
    """Read one pre-encoded frame using the app's FFF1/FFF2 transaction."""
    started = time.perf_counter()
    write_seconds = settle_seconds = read_seconds = 0.0
    try:
        phase_started = time.perf_counter()
        try:
            await client.write_gatt_char(
                CHARACTERISTIC_ADDRESS_UUID, batch.frame, response=True
            )
        finally:
            write_seconds = time.perf_counter() - phase_started

        phase_started = time.perf_counter()
        try:
            await asyncio.sleep(READ_FRAME_DELAY_SECONDS)
        finally:
            settle_seconds = time.perf_counter() - phase_started

        phase_started = time.perf_counter()
        try:
            payload = await client.read_gatt_char(CHARACTERISTIC_DATA_UUID)
        finally:
            read_seconds = time.perf_counter() - phase_started
        return decode_register_batch(batch.registers, bytes(payload))
    finally:
        _LOGGER.debug(
            "BLE register frame %s: FFF1 write %.3fs, settle %.3fs, "
            "FFF2 read %.3fs, total %.3fs",
            batch.registers,
            write_seconds,
            settle_seconds,
            read_seconds,
            time.perf_counter() - started,
        )


async def read_registers(client: GattClient, registers: Sequence[int]) -> dict[int, int]:
    """Read registers sequentially using the app's FFF1/FFF2 transaction."""
    result: dict[int, int] = {}
    for batch in build_read_batches(registers):
        result.update(await read_register_batch(client, batch))
    return result


async def write_register(client: GattClient, register: int, value: int) -> None:
    """Write a register using the app's paired FFF1/FFF2 writes."""
    address_frame, value_frame = encode_write(register, value)
    await client.write_gatt_char(
        CHARACTERISTIC_ADDRESS_UUID, address_frame, response=True
    )
    await asyncio.sleep(COMMAND_DELAY_SECONDS)
    await client.write_gatt_char(
        CHARACTERISTIC_DATA_UUID, value_frame, response=True
    )
