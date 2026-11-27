"""Load model catalogs from the bundled YAML profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .const import FUMIS_AIR_MODEL, SENSO_MODEL


@dataclass(frozen=True)
class ControllerParameter:
    """Parameter and entity metadata from a controller profile."""

    entity_key: str
    register: int
    description: str
    scale: float = 1
    unit: str | None = None
    entity_type: str = "sensor"
    platform: str = "sensor"
    raw_minimum: int | None = None
    raw_maximum: int | None = None
    disabled_by_default: bool = False
    group: str = "telemetry"
    fast_update_frequency: bool = False
    identifier: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    precision: int | None = None
    display_bit: int | None = None
    input_role: str | None = None

    @property
    def polling_tier(self) -> str:
        """Return the coordinator tier used by this catalog row."""
        if self.group in {"settings", "parameter"}:
            return "settings"
        return "fast" if self.fast_update_frequency else "telemetry"

    @property
    def minimum(self) -> float | None:
        return None if self.raw_minimum is None else self.raw_minimum * self.scale

    @property
    def maximum(self) -> float | None:
        return None if self.raw_maximum is None else self.raw_maximum * self.scale


@dataclass(frozen=True)
class _DiagnosticParameter(ControllerParameter):
    """An app diagnostic row with its catalog defaults."""

    entity_type: str = "diag"
    disabled_by_default: bool = True
    group: str = "parameter"


_PROFILE_DIR = Path(__file__).with_name("profiles")


def _read_yaml(filename: str) -> dict:
    with (_PROFILE_DIR / filename).open(encoding="utf-8") as profile_file:
        data = yaml.safe_load(profile_file)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid controller profile: {filename}")
    return data


_COMMON_ENTITIES = _read_yaml("common.yaml").get("entities", {})


def _load_catalog(filename: str) -> dict[str, ControllerParameter]:
    profile = _read_yaml(filename)
    specific = profile.get("entities", {})
    if not isinstance(_COMMON_ENTITIES, dict) or not isinstance(specific, dict):
        raise ValueError(f"Invalid entity rows in {filename}")
    if set(_COMMON_ENTITIES) & set(specific):
        raise ValueError(f"Duplicate common entity in {filename}")
    rows = _COMMON_ENTITIES | specific
    catalog = {}
    for key, row in rows.items():
        parameter_type = (
            _DiagnosticParameter if key.startswith("parameter_") else ControllerParameter
        )
        catalog[key] = parameter_type(
            entity_key=key,
            **row,
        )
    return catalog


ENTITY_CATALOG = {
    SENSO_MODEL: _load_catalog("senso.yaml"),
    FUMIS_AIR_MODEL: _load_catalog("fumis_air.yaml"),
}

INPUT_CONFIGURATION_NAMES = {
    0: "N/A",
    1: "K-Type",
    2: "NTC",
    3: "PT100",
    4: "PT1000 Low",
    5: "PT1000 High",
    6: "Lambda",
    7: "Tacho",
    8: "Switch",
    9: "Analog input",
    10: "Output",
}


def decode_catalog_value(
    item: ControllerParameter, value: int | None
) -> int | float | str | None:
    """Decode catalog parameters and scaled values."""
    if value is None:
        return None
    if (
        item.group == "parameter"
        and item.identifier is not None
        and value > 0x7FFF
    ):
        value -= 0x10000
    if item.entity_key.endswith("_configuration"):
        return INPUT_CONFIGURATION_NAMES.get(value, f"Unknown ({value})")
    if item.scale == 1:
        return value
    if item.identifier is None and item.scale < 1:
        return value / (1 / item.scale)
    return value * item.scale


SIGNED_REGISTERS = frozenset(
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
    }
)


# Profile declarations describe entities; this model/key/register/platform map
# is the only source of write authorization.
_AIR_INTAKE_WRITERS = {
    "manual_air_intake": (11265, "number"),
    "automatic_air_intake": (11265, "switch"),
}
_WRITABLE_ENTITIES = {
    SENSO_MODEL: _AIR_INTAKE_WRITERS
    | {
        "target_room_temperature": (5638, "number"),
        "room_temperature_control": (5642, "switch"),
        "led_brightness": (7694, "select"),
        "sound_volume": (7695, "select"),
        "fuel_type": (11270, "select"),
        "nominal_power": (7696, "number"),
        "max_heat_cap_temperature": (7697, "number"),
    },
    FUMIS_AIR_MODEL: _AIR_INTAKE_WRITERS,
}
WRITABLE_ENTITIES = {}
for _model, _entities in _WRITABLE_ENTITIES.items():
    _authorized = {}
    for _key, (_register, _platform) in _entities.items():
        _item = ENTITY_CATALOG[_model].get(_key)
        if (
            _item is None
            or _item.register != _register
            or _item.entity_type != "config"
            or _item.platform != _platform
        ):
            raise ValueError(f"Invalid writer profile: {_model}/{_key}")
        _authorized[_key] = _item
    WRITABLE_ENTITIES[_model] = _authorized

WRITABLE_NUMBERS = {
    model: {
        item.register: item for item in entities.values()
        if item.platform == "number"
    }
    for model, entities in WRITABLE_ENTITIES.items()
}
WRITABLE_SELECTS = {
    model: {
        item.register: item for item in entities.values()
        if item.platform == "select"
    }
    for model, entities in WRITABLE_ENTITIES.items()
}
ROOM_TARGET = ENTITY_CATALOG[SENSO_MODEL]["target_room_temperature"]
APK_PARAMETERS = {
    model: {
        row.identifier: row for row in entities.values()
        if row.identifier is not None
    }
    for model, entities in ENTITY_CATALOG.items()
}


