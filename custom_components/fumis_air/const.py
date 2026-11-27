"""Shared integration identity and device models."""

DOMAIN = "fumis_air"
CONF_MODEL = "model"
MANUFACTURER = "ATech elektronika d.o.o."
FUMIS_AIR_MODEL = "Fumis AIR"
SENSO_MODEL = "Tulikivi Senso"
DEVICE_MODELS = (FUMIS_AIR_MODEL, SENSO_MODEL)
PERCENTAGE_PRESETS = (
    ("Off", 0),
    ("Low", 25),
    ("Mid", 50),
    ("High", 75),
    ("Max", 100),
)

ADVERTISEMENT_MODELS = {
    "Fumis AIR": FUMIS_AIR_MODEL,
    "Senso": SENSO_MODEL,
    "AirMaster": None,
}


def model_for_advertisement(name: str) -> str | None:
    """Return the user-facing device model for an exact Bluetooth name."""
    return ADVERTISEMENT_MODELS.get(name)