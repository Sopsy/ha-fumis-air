"""Config flow for locally connected Fumis AIR and Tulikivi Senso controllers."""

from __future__ import annotations

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .ble import FumisAirConnectionError, async_connect, async_disconnect
from .const import (
    ADVERTISEMENT_MODELS,
    CONF_MODEL,
    DEVICE_MODELS,
    DOMAIN,
    SENSO_MODEL,
    model_for_advertisement,
)
from .pairing import (
    PairingError,
    PairingNotSupported,
    async_pair_device,
    valid_pin,
)
from .protocol import (
    SERVICE_UUID,
    FumisAirProtocolError,
    probe_registers,
    read_registers,
    validate_probe_registers,
)

CONF_BLUETOOTH_PIN = "pin"
CONNECTION_ERRORS = (BleakError, OSError, TimeoutError, FumisAirConnectionError)


class FumisAirConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a device found by Bluetooth discovery."""

    VERSION = 1


    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        """Confirm a supported connectable advertisement."""
        if not _is_supported_device(discovery_info):
            return self.async_abort(reason="not_supported")

        return await self._async_confirm_device(discovery_info)

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select a named controller or unnamed FFF0 candidate."""
        errors: dict[str, str] = {}
        discovered = async_discovered_service_info(self.hass, connectable=True)
        discovered_devices = {
            info.address.upper(): info
            for info in discovered
            if _is_manual_device(info)
        }
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper()
            discovery_info = discovered_devices.get(address)
            if discovery_info is None:
                errors["base"] = "device_not_found"
            else:
                return await self._async_confirm_device(discovery_info)

        devices = {
            address: (
                f"{model_for_advertisement(info.name) or info.name} ({address})"
                if not _is_unnamed(info)
                else f"Unnamed controller ({address})"
            )
            for address in sorted(discovered_devices)
            for info in (discovered_devices[address],)
        }
        if not devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(devices)}
            ),
            errors=errors,
        )

    async def _async_confirm_device(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        """Use the shared confirmation flow for either discovery path."""
        self._address = discovery_info.address.upper()
        self._model = model_for_advertisement(discovery_info.name)
        await self.async_set_unique_id(self._address.lower())
        self._abort_if_unique_id_configured()
        if self._model is None:
            return await self.async_step_model()
        return await self.async_step_confirm()

    async def async_step_model(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for a profile when the advertisement does not identify it."""
        if user_input is not None:
            model = user_input[CONF_MODEL]
            if model in DEVICE_MODELS:
                self._model = model
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema({vol.Required(CONF_MODEL): vol.In(DEVICE_MODELS)}),
        )

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for an optional Bluetooth PIN before validating the controller."""
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = user_input.get(CONF_BLUETOOTH_PIN, "")
            if pin and not valid_pin(pin):
                errors[CONF_BLUETOOTH_PIN] = "invalid_pin"
            else:
                try:
                    await self._async_validate_device(pin)
                except FumisAirProtocolError:
                    return self.async_abort(reason="not_supported")
                except PairingNotSupported:
                    errors["base"] = "pairing_unsupported"
                except PairingError:
                    errors["base"] = "pairing_failed"
                except CONNECTION_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    return self._create_entry()

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_BLUETOOTH_PIN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    def _create_entry(self) -> config_entries.ConfigFlowResult:
        """Create an entry without retaining the temporary Bluetooth PIN."""
        name = f"{self._model} {self._address[-5:]}"
        return self.async_create_entry(
            title=name,
            data={
                CONF_ADDRESS: self._address,
                CONF_MODEL: self._model,
            },
        )

    async def _async_validate_device(self, pin: str) -> None:
        """Pair when requested, then verify the controller protocol."""
        if pin:
            await self._async_verify_profile(self._address)
            try:
                await async_pair_device(self.hass, self._address, pin)
            except PairingNotSupported as err:
                try:
                    await self._async_probe(self._address)
                except CONNECTION_ERRORS as probe_error:
                    raise PairingNotSupported(str(probe_error)) from err
                return
        await self._async_probe(self._address)

    async def _async_verify_profile(self, address: str) -> None:
        """Verify the shared GATT service and characteristics before pairing."""
        client = await async_connect(self.hass, address)
        await async_disconnect(client)

    async def _async_probe(self, address: str) -> None:
        """Verify the GATT profile and read recognizable controller registers."""
        client = await async_connect(self.hass, address)
        try:
            profile = self._model or SENSO_MODEL
            values = await read_registers(client, probe_registers(profile))
            validate_probe_registers(values, profile)
        finally:
            await async_disconnect(client)


def _is_supported_device(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Match an exact supported name, service and connectability."""
    return (
        discovery_info.name in ADVERTISEMENT_MODELS
        and _is_connectable_controller(discovery_info)
    )


def _is_manual_device(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Allow supported names or unnamed controller candidates in manual setup."""
    return _is_connectable_controller(discovery_info) and (
        discovery_info.name in ADVERTISEMENT_MODELS or _is_unnamed(discovery_info)
    )


def _is_connectable_controller(
    discovery_info: BluetoothServiceInfoBleak,
) -> bool:
    """Require a connectable advertisement for the controller service."""
    return (
        discovery_info.connectable
        and SERVICE_UUID.lower()
        in {service_uuid.lower() for service_uuid in discovery_info.service_uuids}
    )


def _is_unnamed(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Treat absent names and names that are just the address as unnamed."""
    name = discovery_info.name
    return not name or name.upper() == discovery_info.address.upper()
