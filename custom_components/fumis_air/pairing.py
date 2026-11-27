"""One-shot, user-authorized Bluetooth pairing for Senso setup."""

import asyncio
import re
from typing import Any

PIN_PATTERN = re.compile(r"^[0-9]{6}$")
PAIRING_TIMEOUT = 30
AGENT_PATH = "/org/homeassistant/fumis_air/PairingAgent"


class PairingError(Exception):
    """The selected controller could not be paired."""


class PairingNotSupported(PairingError):
    """The selected Bluetooth adapter cannot use the local BlueZ pairing path."""


def valid_pin(pin: str) -> bool:
    """Return whether the user supplied a six-digit PIN, preserving leading zeroes."""
    return bool(PIN_PATTERN.fullmatch(pin))


def _property_value(value: Any) -> Any:
    """Unwrap a D-Bus Variant when reading BlueZ object properties."""
    return getattr(value, "value", value)


def _make_pairing_agent(
    service_interface: Any,
    dbus_method: Any,
    dbus_error: Any,
    device_path: str,
    pin: str,
) -> Any:
    """Build an agent that only answers PIN requests for the selected device."""

    class SensoPairingAgent(service_interface):
        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")

        @dbus_method()
        def Release(self) -> None:
            """Acknowledge BlueZ releasing the temporary agent."""

        @dbus_method()
        def RequestPinCode(self, device: "o") -> "s":
            """Return the user-entered PIN only to the selected peripheral."""
            if device != device_path:
                raise dbus_error("org.bluez.Error.Rejected", "Unexpected device")
            return pin

        @dbus_method()
        def RequestPasskey(self, device: "o") -> "u":
            """Support numeric passkey requests without losing leading zeroes."""
            if device != device_path:
                raise dbus_error("org.bluez.Error.Rejected", "Unexpected device")
            return int(pin)

        @dbus_method()
        def Cancel(self) -> None:
            """Acknowledge cancellation without approving another request."""

    return SensoPairingAgent()


async def async_pair_device(hass: Any, address: str, pin: str) -> None:
    """Pair one selected local BlueZ device using a temporary scoped agent."""
    if not valid_pin(pin):
        raise ValueError("The Bluetooth PIN must contain exactly six digits")

    from homeassistant.components.bluetooth import async_ble_device_from_address

    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise PairingNotSupported("No local connectable Bluetooth device is available")

    details = getattr(ble_device, "details", None)
    path_hint = details.get("path") if isinstance(details, dict) else None

    try:
        from dbus_fast import DBusError
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
        from dbus_fast.service import ServiceInterface, dbus_method
    except ImportError as err:
        raise PairingNotSupported("Local BlueZ pairing is unavailable") from err

    bus = None
    exported = False
    registered = False
    try:
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as err:
            raise PairingNotSupported("Local BlueZ D-Bus is unavailable") from err
        root_introspection = await bus.introspect("org.bluez", "/")
        root = bus.get_proxy_object("org.bluez", "/", root_introspection)
        object_manager = root.get_interface("org.freedesktop.DBus.ObjectManager")
        objects = await object_manager.call_get_managed_objects()

        candidates = []
        for object_path, interfaces in objects.items():
            properties = interfaces.get("org.bluez.Device1")
            if (
                properties
                and str(_property_value(properties.get("Address", ""))).upper()
                == address.upper()
            ):
                candidates.append((str(object_path), properties))

        if path_hint:
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate[0] == str(path_hint)
                ),
                None,
            )
        else:
            selected = candidates[0] if len(candidates) == 1 else None
        if selected is None:
            raise PairingNotSupported(
                "The selected device is not available on local BlueZ"
            )

        device_path, properties = selected
        if _property_value(properties.get("Paired", False)):
            return

        agent = _make_pairing_agent(
            ServiceInterface, dbus_method, DBusError, device_path, pin
        )
        bus.export(AGENT_PATH, agent)
        exported = True

        agent_manager_introspection = await bus.introspect(
            "org.bluez", "/org/bluez"
        )
        agent_manager_object = bus.get_proxy_object(
            "org.bluez", "/org/bluez", agent_manager_introspection
        )
        agent_manager = agent_manager_object.get_interface(
            "org.bluez.AgentManager1"
        )
        await agent_manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
        registered = True

        device_introspection = await bus.introspect("org.bluez", device_path)
        device_object = bus.get_proxy_object(
            "org.bluez", device_path, device_introspection
        )
        device_interface = device_object.get_interface("org.bluez.Device1")
        await asyncio.wait_for(device_interface.call_pair(), PAIRING_TIMEOUT)
    except PairingError:
        raise
    except asyncio.TimeoutError as err:
        raise PairingError("Bluetooth pairing timed out") from err
    except Exception as err:
        raise PairingError("Bluetooth pairing failed") from err
    finally:
        if bus is not None:
            if registered:
                try:
                    await asyncio.shield(
                        agent_manager.call_unregister_agent(AGENT_PATH)
                    )
                except Exception:
                    pass
            if exported:
                try:
                    bus.unexport(AGENT_PATH)
                except Exception:
                    pass
            try:
                bus.disconnect()
            except Exception:
                pass
