"""Shared Home Assistant device and entity metadata helpers."""

from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_MODEL, DOMAIN, MANUFACTURER, SENSO_MODEL
from .parameters import ControllerParameter


def entity_category(item: ControllerParameter) -> EntityCategory | None:
    """Translate a catalog role to its Home Assistant entity category."""
    return {
        "config": EntityCategory.CONFIG,
        "diag": EntityCategory.DIAGNOSTIC,
    }.get(item.entity_type)


def entity_coordinator(owner, item: ControllerParameter):
    """Return the coordinator selected by a catalog row's polling tier."""
    if item.polling_tier == "fast":
        return owner.fast_coordinator
    if item.polling_tier == "settings":
        return owner.settings_coordinator
    return owner


def controller_model(entry) -> str:
    """Return the configured controller model, defaulting old entries to Senso."""
    return entry.data.get(CONF_MODEL, SENSO_MODEL)


def entity_unique_id(entry, key: str) -> str:
    """Build a stable, integration-namespaced unique ID."""
    address = entry.data["address"].lower()
    return f"{DOMAIN}_{address}_{key}"


def device_info(entry) -> DeviceInfo:
    """Return consistent device metadata for every platform entity."""
    address = entry.data["address"].lower()
    coordinator = getattr(entry, "runtime_data", None)
    firmware_version = getattr(coordinator, "firmware_version", None)
    info = DeviceInfo(
        identifiers={(DOMAIN, address)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=controller_model(entry),
    )
    if firmware_version is not None:
        info["sw_version"] = firmware_version
    return info


class CatalogCoordinatorEntity(
    CoordinatorEntity[DataUpdateCoordinator[dict[int, int]]]
):
    """Apply shared identity and catalog metadata to a register entity."""

    def __init__(self, coordinator, entry, metadata: ControllerParameter) -> None:
        super().__init__(coordinator, context=metadata.register)
        self.metadata = metadata
        self.model = controller_model(entry)
        self.register = metadata.register
        self._attr_has_entity_name = True
        self._attr_name = metadata.description
        self._attr_entity_category = entity_category(metadata)
        self._attr_unique_id = entity_unique_id(entry, metadata.entity_key)
        self._attr_device_info = device_info(entry)
        self._attr_entity_registry_enabled_default = not metadata.disabled_by_default

    @property
    def register_value(self) -> int | None:
        """Return this entity's latest register value."""
        return self.coordinator.data.get(self.metadata.register)