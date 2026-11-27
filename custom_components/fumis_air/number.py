"""Temperature and appliance setting controls for Fumis AIR."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import FumisAirConfigEntry
from .const import FUMIS_AIR_MODEL
from .coordinator import FumisAirCoordinator, room_temperature_range
from .identity import CatalogCoordinatorEntity, controller_model
from .parameters import ControllerParameter, WRITABLE_NUMBERS
from .protocol import (
    REG_FAN,
    fan_automatic,
    fan_position_percent,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FumisAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose ordinary settings and temperature controls."""
    coordinator = entry.runtime_data
    model = controller_model(entry)
    async_add_entities(
        FumisAirNumber(coordinator, entry, metadata)
        for metadata in WRITABLE_NUMBERS[model].values()
    )


class FumisAirNumber(CatalogCoordinatorEntity, NumberEntity):
    """A numeric controller setting with live or fixed limits."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: FumisAirCoordinator,
        entry: FumisAirConfigEntry,
        metadata: ControllerParameter,
    ) -> None:
        super().__init__(coordinator.settings_coordinator, entry, metadata)
        self.owner = coordinator
        self.is_air_intake = self.register == REG_FAN
        self.is_room_temperature = metadata.entity_key == "target_room_temperature"
        self.minimum, self.maximum = metadata.minimum, metadata.maximum
        self._attr_native_step = metadata.scale
        is_temperature = metadata.unit == UnitOfTemperature.CELSIUS
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS if is_temperature else metadata.unit
        )
        self._attr_device_class = (
            NumberDeviceClass.TEMPERATURE if is_temperature else None
        )
        self._last_valid_range = (
            room_temperature_range(coordinator.settings_coordinator.data)
            if self.is_room_temperature
            else None
        )

    def _room_limits(self) -> tuple[int, int] | None:
        return room_temperature_range(self.coordinator.data) or self._last_valid_range

    @property
    def native_min_value(self) -> float | int:
        """Return the configured minimum or the controller's live minimum."""
        limits = self._room_limits() if self.is_room_temperature else None
        return limits[0] / (1 / self.metadata.scale) if limits else self.minimum

    @property
    def native_max_value(self) -> float | int:
        """Return the configured maximum or the controller's live maximum."""
        limits = self._room_limits() if self.is_room_temperature else None
        return limits[1] / (1 / self.metadata.scale) if limits else self.maximum

    @property
    def native_value(self) -> float | None:
        """Return the controller value only when it is within its entity limits."""
        value = self.register_value
        if self.is_air_intake:
            automatic = fan_automatic(value, self.model)
            if automatic is None:
                return None
            if self.model == FUMIS_AIR_MODEL:
                return float(0 if automatic else value & 0xFF)
            position = fan_position_percent(value & 0xFF)
            return float(position) if position is not None else None
        if self.is_room_temperature:
            return value / (1 / self.metadata.scale) if value is not None else None
        if value is None:
            return None
        scaled = value * self.metadata.scale
        if not self.minimum <= scaled <= self.maximum:
            return None
        return float(scaled)

    @property
    def available(self) -> bool:
        """Require a current value within the entity's configured limits."""
        if not super().available:
            return False
        if self.is_air_intake:
            return self.native_value is not None
        if self.is_room_temperature:
            limits = room_temperature_range(self.coordinator.data)
            value = self.register_value
            return (
                value is not None
                and limits is not None
                and limits[0] <= value <= limits[1]
            )
        return self.native_value is not None

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting and publish its immediate controller readback."""
        await self.owner.async_set_number(self.register, value)


