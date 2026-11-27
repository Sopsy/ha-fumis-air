"""Bluetooth connection helpers for Fumis AIR and Tulikivi Senso."""

from __future__ import annotations

import logging

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant

from .protocol import (
    CHARACTERISTIC_ADDRESS_UUID,
    CHARACTERISTIC_DATA_UUID,
    SERVICE_UUID,
    FumisAirProtocolError,
)

_LOGGER = logging.getLogger(__name__)


class FumisAirConnectionError(Exception):
    """The controller could not be connected to or did not match its profile."""


async def async_connect(hass: HomeAssistant, address: str) -> BleakClient:
    """Connect through HA's Bluetooth device registry and retry connector."""
    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise FumisAirConnectionError("No local connectable Bluetooth device is available")

    client = await establish_connection(
        BleakClientWithServiceCache,
        ble_device,
        address,
        timeout=15,
    )
    try:
        if not client.is_connected:
            raise FumisAirConnectionError("The BLE connection was not established")
        service = client.services.get_service(SERVICE_UUID)
        if service is None:
            raise FumisAirProtocolError("The controller BLE service was not found")
        if (
            service.get_characteristic(CHARACTERISTIC_ADDRESS_UUID) is None
            or service.get_characteristic(CHARACTERISTIC_DATA_UUID) is None
        ):
            raise FumisAirProtocolError("The controller register characteristics were not found")
        await client.pair()
        return client
    except Exception:
        await async_disconnect(client)
        raise


async def async_disconnect(client: BleakClient) -> None:
    """Disconnect cleanly and release the adapter connection slot."""
    if client.is_connected:
        try:
            await client.disconnect()
        except (BleakError, OSError):
            _LOGGER.debug("Error disconnecting from controller", exc_info=True)
