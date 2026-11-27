"""Controller setting switches for Fumis AIR."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import FumisAirConfigEntry
from .coordinator import (
    FumisAirCoordinator,
    ROOM_TEMP_INSTALLER_MASK,
    ROOM_TEMP_USER_MASK,
)
from .identity import CatalogCoordinatorEntity, controller_model
from .parameters import ControllerParameter, WRITABLE_ENTITIES
from .protocol import fan_automatic


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FumisAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up air-intake mode and Senso room-temperature controls."""
    model = controller_model(entry)
    async_add_entities(
        FumisAirSwitch(entry.runtime_data, entry, metadata)
        for metadata in WRITABLE_ENTITIES[model].values()
        if metadata.platform == "switch"
    )


class FumisAirSwitch(CatalogCoordinatorEntity, SwitchEntity):
    """Expose a guarded Senso or air-intake mode switch."""

    def __init__(
        self,
        coordinator: FumisAirCoordinator,
        entry: FumisAirConfigEntry,
        metadata: ControllerParameter,
    ) -> None:
        super().__init__(coordinator.settings_coordinator, entry, metadata)
        self.owner = coordinator
        self.is_air_intake = metadata.entity_key == "automatic_air_intake"

    @property
    def available(self) -> bool:
        """Require a valid air mode or installer-enabled room control."""
        value = self.register_value
        if not super().available or value is None:
            return False
        return (
            fan_automatic(value, self.model) is not None
            if self.is_air_intake
            else bool(value & ROOM_TEMP_INSTALLER_MASK)
        )

    @property
    def is_on(self) -> bool:
        """Return the current mode or user-controlled bit."""
        value = self.register_value
        return (
            fan_automatic(value, self.model) is True
            if self.is_air_intake
            else value is not None and bool(value & ROOM_TEMP_USER_MASK)
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the selected mode or user room-temperature control."""
        if self.is_air_intake:
            await self.owner.async_set_air_automatic(True)
        else:
            await self.owner.async_set_room_temperature_control(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the selected mode or user room-temperature control."""
        if self.is_air_intake:
            await self.owner.async_set_air_automatic(False)
        else:
            await self.owner.async_set_room_temperature_control(False)