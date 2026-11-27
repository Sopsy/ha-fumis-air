"""Fumis AIR Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import FumisAirCoordinator

PLATFORMS = ("sensor", "binary_sensor", "number", "switch", "select")
type FumisAirConfigEntry = ConfigEntry[FumisAirCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: FumisAirConfigEntry) -> bool:
    """Set up a configured Fumis AIR controller."""
    coordinator = FumisAirCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_read_firmware_version()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await coordinator.async_refresh()
        await coordinator.fast_coordinator.async_refresh()
        await coordinator.settings_coordinator.async_refresh()
    except BaseException:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FumisAirConfigEntry) -> bool:
    """Unload a configured controller."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True
