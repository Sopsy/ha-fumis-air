# Fumis AIR for Home Assistant

This repository contains the `fumis_air` custom integration for Home Assistant and its tests. The integration communicates with compatible Fumis AIR and Tulikivi Senso controllers over Bluetooth Low Energy.

For installation, controller setup, entities, and troubleshooting, see the [Home Assistant user guide](custom_components/fumis_air/README.md).

### Repository contents

- `custom_components/fumis_air/` — the Home Assistant integration package.
- `custom_components/fumis_air/profiles/` — shared and model-specific YAML register catalogs.
- `tests/` — unit tests for the integration, configuration flow, Bluetooth helpers, and package structure.
- `LICENSE` — the project license.

### Development

The tests use Python's built-in `unittest` runner and PyYAML for profile loading. From the repository root, install PyYAML and run:

```sh
python -m pip install PyYAML
python -m unittest discover -s tests
```

When changing protocol behavior, update or add simulated-controller tests so register framing, model-specific decoding, and write validation remain covered. Hardware behavior and Bluetooth timing should be confirmed on a real controller before relying on changes that affect them.

### Contributing and support

Bug reports and focused contributions are welcome. For a useful issue, include the controller model, Home Assistant and Bluetooth-adapter details, relevant integration logs, and steps to reproduce; remove PINs and other private information.