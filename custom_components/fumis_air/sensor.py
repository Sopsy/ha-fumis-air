"""Controller overview and selected telemetry sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import FumisAirConfigEntry
from .const import SENSO_MODEL
from .identity import CatalogCoordinatorEntity, controller_model, entity_coordinator
from .parameters import ControllerParameter, ENTITY_CATALOG, decode_catalog_value
from .protocol import (
    REG_ADVICE,
    REG_POWER,
    REG_STATUS,
    VAL_INPUT_2,
    VAL_REG_POS,
    VAL_TEMP_HEAT,
    decode_advice,
    decode_heat_cap_temperature,
    decode_power_mode,
    decode_status,
    displayed_registers,
    senso_fan_position_percent,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FumisAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for fixed values and device-enabled display channels."""
    coordinator = entry.runtime_data
    model = controller_model(entry)
    display_registers = set(
        displayed_registers(coordinator.display_mask or 0, model)
    )
    async_add_entities(
        FumisAirSensor(
            entity_coordinator(coordinator, item),
            entry,
            item,
        )
        for item in ENTITY_CATALOG[model].values()
        if item.platform == "sensor"
        and (item.display_bit is None or item.register in display_registers)
    )


class FumisAirSensor(CatalogCoordinatorEntity, SensorEntity):
    """A register-backed controller sensor."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[int, int]],
        entry: FumisAirConfigEntry,
        metadata: ControllerParameter,
    ) -> None:
        super().__init__(coordinator, entry, metadata)
        self.entity_description = SensorEntityDescription(
            key=metadata.entity_key,
            name=metadata.description,
            native_unit_of_measurement=metadata.unit,
            device_class=(
                SensorDeviceClass(metadata.device_class)
                if metadata.device_class
                else None
            ),
            state_class=(
                SensorStateClass(metadata.state_class)
                if metadata.state_class
                else None
            ),
            suggested_display_precision=metadata.precision,
        )
        if metadata.identifier is not None:
            self._attr_extra_state_attributes = {
                "parameter_id": metadata.identifier,
                "description": metadata.description,
            }

    @property
    def native_value(self) -> int | float | str | None:
        """Return the raw or scaled controller value."""
        value = self.register_value
        if value is None:
            return None
        metadata = self.metadata
        register = metadata.register
        if register == REG_STATUS:
            return decode_status(value, self.model)
        if register == REG_ADVICE:
            return decode_advice(value)
        if register == REG_POWER:
            return decode_power_mode(value)
        if self.model == SENSO_MODEL and register == VAL_TEMP_HEAT:
            return decode_heat_cap_temperature(
                value, self.coordinator.data.get(VAL_INPUT_2)
            )
        if register == VAL_REG_POS:
            return senso_fan_position_percent(value)
        return decode_catalog_value(metadata, value)






