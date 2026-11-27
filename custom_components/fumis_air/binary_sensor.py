"""Door and error status binary sensors for Fumis AIR controllers."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import FumisAirConfigEntry
from .identity import CatalogCoordinatorEntity, controller_model, entity_coordinator
from .parameters import ControllerParameter, ENTITY_CATALOG
from .protocol import error_details, has_error, is_door_open


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FumisAirConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up boolean door and error status entities."""
    coordinator = entry.runtime_data
    model = controller_model(entry)
    async_add_entities(
        FumisAirBinarySensor(
            entity_coordinator(coordinator, item),
            entry,
            item,
        )
        for item in ENTITY_CATALOG[model].values()
        if item.platform == "binary_sensor"
    )


class FumisAirBinarySensor(CatalogCoordinatorEntity, BinarySensorEntity):
    """Expose a boolean state from a controller status or error register."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[int, int]],
        entry: FumisAirConfigEntry,
        metadata: ControllerParameter,
    ) -> None:
        super().__init__(coordinator, entry, metadata)
        self.entity_description = BinarySensorEntityDescription(
            key=metadata.entity_key,
            name=metadata.description,
            device_class=BinarySensorDeviceClass(metadata.device_class),
        )

    @property
    def available(self) -> bool:
        """Require the register value to be present in the latest poll."""
        if not super().available or self.register_value is None:
            return False
        if self.entity_description.device_class == BinarySensorDeviceClass.DOOR:
            return is_door_open(
                self.coordinator.data[self.metadata.register], self.model
            ) is not None
        return True

    @property
    def is_on(self) -> bool:
        """Return true for an open door or any active bit in an error mask."""
        value = self.register_value
        if self.entity_description.device_class == BinarySensorDeviceClass.DOOR:
            return is_door_open(value, self.model) is True
        return has_error(value) is True

    @property
    def extra_state_attributes(self) -> dict[str, list[str]] | None:
        """Expose the active human-readable faults behind each error mask."""
        if self.entity_description.device_class == BinarySensorDeviceClass.DOOR:
            return None
        return {
            "active_errors": error_details(
                self.metadata.register,
                self.register_value,
                self.model,
            )
        }