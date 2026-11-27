"""Fuel and percentage setting selects for Fumis AIR controllers."""

from __future__ import annotations

from bisect import bisect_left

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import FumisAirConfigEntry
from .const import PERCENTAGE_PRESETS
from .coordinator import FumisAirCoordinator
from .identity import CatalogCoordinatorEntity, controller_model
from .parameters import ControllerParameter, WRITABLE_ENTITIES
from .protocol import (
    REG_FUEL,
    REG_FUEL_COUNT,
    fuel_type_options,
    selected_fuel_type,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FumisAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up controller select controls."""
    model = controller_model(entry)
    async_add_entities(
        FumisAirSelect(entry.runtime_data, entry, metadata)
        for metadata in WRITABLE_ENTITIES[model].values()
        if metadata.platform == "select"
    )


class FumisAirSelect(CatalogCoordinatorEntity, SelectEntity):
    """Select a supported fuel type or Senso brightness/volume level."""

    def __init__(
        self,
        coordinator: FumisAirCoordinator,
        entry: FumisAirConfigEntry,
        metadata: ControllerParameter,
    ) -> None:
        super().__init__(coordinator.settings_coordinator, entry, metadata)
        self.owner = coordinator

    @property
    def available(self) -> bool:
        """Require fuel support data when this entity selects a fuel type."""
        return super().available and (
            self.metadata.entity_key != "fuel_type" or bool(self.options)
        )

    @property
    def options(self) -> list[str]:
        """Return controller-supported choices for this setting."""
        if self.metadata.entity_key != "fuel_type":
            return [option for option, _ in PERCENTAGE_PRESETS]
        count = self.coordinator.data.get(REG_FUEL_COUNT)
        return list(fuel_type_options(count))

    @property
    def current_option(self) -> str | None:
        """Display valid percentages at the next preset at or above them."""
        if self.metadata.entity_key != "fuel_type":
            value = self.coordinator.data.get(self.metadata.register)
            if value is None or not 0 <= value <= PERCENTAGE_PRESETS[-1][1]:
                return None
            return PERCENTAGE_PRESETS[
                bisect_left(tuple(level for _, level in PERCENTAGE_PRESETS), value)
            ][0]
        return selected_fuel_type(
            self.coordinator.data.get(REG_FUEL),
            self.coordinator.data.get(REG_FUEL_COUNT),
        )

    async def async_select_option(self, option: str) -> None:
        """Set a select option after coordinator-side support validation."""
        if self.metadata.entity_key == "fuel_type":
            await self.owner.async_set_fuel_type(option)
            return
        level = next(
            (level for label, level in PERCENTAGE_PRESETS if label == option), None
        )
        if level is None:
            raise ValueError(f"Unsupported level: {option}")
        await self.owner.async_set_percentage_select(self.metadata.register, level)