# Fumis AIR integration for Home Assistant

This integration connects Home Assistant locally to supported Fumis AIR and Tulikivi Senso controllers over Bluetooth Low Energy. It does not require a cloud account or service.

## Install
1. In Home Assistant, open **Settings** -> **Marketplace**
2. Click **Add from link** button in top right corner and paste the link to this repository `https://github.com/sopsy/ha-fumis-air`.
3. Find **Fumis AIR** and install it.

Home Assistant starts the integration setup automatically after the Marketplace installation. Follow the prompts below to select and configure the controller; you do not need to add the integration separately.

## Add a controller

1. Make sure Home Assistant can reach a connectable Bluetooth adapter or Bluetooth proxy near the controller.
2. Select the controller in the setup flow. Automatic discovery accepts the exact Bluetooth names `Fumis AIR`, `Senso`, and `AirMaster`, provided the device advertises the required service. `Fumis AIR` selects the Fumis AIR profile and `Senso` selects Tulikivi Senso. Because both products can advertise as `AirMaster`, setup asks you to choose the controller model.
3. If pairing requires a PIN, follow the instructions in [Bluetooth pairing](#bluetooth-pairing). The PIN is not saved.

If the controller is connectable and advertises the required `FFF0` service but Home Assistant receives no name, start setup manually from **Settings → Devices & services → Add integration → Fumis AIR**. The unnamed device appears by Bluetooth address. Select it, choose the controller model, and continue through the usual pairing and controller-validation steps. This address fallback is available only in manual setup; it does not broaden automatic discovery.

Setup checks the selected controller's Bluetooth profile and registers before creating the device. Home Assistant identifies the device manufacturer as **ATech elektronika d.o.o.** and reports the firmware version in device details when the controller provides it.

### Bluetooth pairing

If the controller is already paired to the adapter Home Assistant will use, leave the PIN field blank. For a local Linux/BlueZ adapter, enter the controller's six-digit PIN when the setup flow asks for it; the integration uses it only for that pairing attempt.

An ESPHome Bluetooth proxy is a separate Bluetooth adapter: a bond made by the Home Assistant host or another adapter is not shared with the proxy. The PIN entered in the integration's setup flow is handled by local BlueZ and is not forwarded to an ESPHome proxy. Configure the proxy to support active GATT connections, and, if the controller requests a passkey, configure ESPHome to answer that request on the proxy. For example, adapt this to your proxy node and controller address:

```yaml
esp32_ble:
  io_capability: keyboard_only

esp32_ble_tracker:

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"
    id: fumis_controller
    on_passkey_request:
      then:
        - ble_client.passkey_reply:
            id: fumis_controller
            passkey: 123456  # Replace with the controller's six-digit PIN.

bluetooth_proxy:
  active: true
```

Keep the PIN private in your ESPHome configuration. With this setup, leave the integration's PIN field blank; the ESPHome node handles the passkey request. See the [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/) and [BLE client passkey](https://esphome.io/components/ble_client/#passkey-examples) documentation for platform requirements and configuration details. If your proxy cannot handle the controller's pairing method, use a local BlueZ adapter or configure pairing on the same proxy that Home Assistant will use.

## Entities and controls

The entities available depend on the controller model and, for display readings, the channels enabled on the controller.

### Status and readings

- **Status** reports a readable controller state. **Door open** is a boolean entity derived from that status.
- **Control error**, **Motor error**, and **Sensor error** are boolean problem entities. Their `active_errors` attribute lists recognized active faults when available.
- **Advice** is provided by Tulikivi Senso controllers.
- Display readings are created for channels enabled by the controller. Depending on the model and its configuration, these can include room, water, firebox or flue-gas, oven, and heat-cap temperatures; draft; lambda; heat capacity; regulator position; and heat output.
- **Operating mode** is a diagnostic sensor and is disabled by default.

### Controls

Both profiles provide **Manual air intake** and **Automatic air intake** controls. The Senso manual setting follows the controller's configured manual value; it is not continually overwritten with the live regulator position.

Tulikivi Senso additionally provides **LED display brightness** and **Beeper volume** selects with **Off**, **Low**, **Mid**, **High**, and **Max** presets (0%, 25%, 50%, 75%, and 100%). Home Assistant displays readings of 0%, 1–25%, 26–50%, 51–75%, and 76–100% as those presets, respectively, without changing the controller's stored value.

Senso also provides **Nominal power**, **Set max ambient temperature.**, **Enable Room Temperature Function**, and **Fuel type**. Nominal power is a per-installation controller setting (0–50 kW in 0.1 kW steps) and is disabled by default. The room-control switch is available only when the controller's installer setting permits it. Fuel choices are limited to the standard types the controller reports as supported: Soft wood, Hard wood, and Pellets. **Max stone temp - forh heat charge calculation** changes the controller's live maximum heat-cap setting and is disabled by default.

The shared rows live in `profiles/common.yaml`; `profiles/senso.yaml` and `profiles/fumis_air.yaml` define each model's rows. The runtime catalog holds register, app ID, scale, unit, platform, category, limits, default enablement, and polling metadata. Polling tiers are derived from settings groups and the optional `fast_update_frequency` flag; display channels are identified by their `display_bit`. Parameter diagnostics are disabled by default and are read every five minutes when enabled. App parameters that already have a control appear only once. Profile data describes entities but does not authorize writes; write access remains explicitly allowlisted in Python.

Other optional diagnostic entities are also disabled by default. These include input configuration and raw input readings, misfire and overheating counters, board and voltage readings, operating mode, and service-related values. Disabled entities do not add reads.

## Updates and Bluetooth behavior

Fast status and selected temperature/draft readings are scheduled every second; other enabled telemetry is scheduled every 30 seconds. Enabled settings and parameter diagnostics are scheduled every five minutes. A successful Home Assistant setting change is followed by a read of the controller's reported value and immediate refreshes of the 30-second and five-minute coordinators. Senso's optional Input 2 precision reading is refreshed with the 30-second telemetry and used only when it agrees with the fast heat-cap temperature. The integration reuses its Bluetooth connection while the config entry is loaded and reconnects if the link is lost.

These intervals are scheduling targets, not delivery deadlines. A poll may need several controller-compatible frames, and Bluetooth response time, other adapter activity, or a reconnect can delay updates. The fast values share a single controller-compatible frame and publish independently from the slower telemetry and settings polls.

## Troubleshooting

- **No controller appears:** check that the Home Assistant Bluetooth integration is working, the device is in range, and the adapter or proxy can see a connectable advertisement. Automatic discovery requires an exact supported name; manual setup also lists unnamed devices when they advertise the required `FFF0` service.
- **Setup cannot validate the controller:** confirm the selected model, especially for `AirMaster`, and ensure the device is not out of range or already in use by another client.
- **Pairing fails:** for a local adapter, verify the six-digit PIN and pair through the host operating system if needed. For an ESPHome proxy, check that `bluetooth_proxy: active: true` is configured and that its `ble_client` passkey handler uses the correct controller address and PIN; leave the Home Assistant PIN field blank when using the proxy.
- **A reading is missing:** some sensors depend on the controller's display configuration or model. Optional diagnostics start disabled; enable the relevant entity if you need it.
- **Updates are delayed:** Bluetooth polling can exceed its nominal interval. To collect timing details, enable debug logging in Home Assistant and review the integration logs alongside the Bluetooth adapter or proxy logs.

For repository development, tests, and project information, see the [repository README](../../README.md).