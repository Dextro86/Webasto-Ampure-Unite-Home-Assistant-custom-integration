# Webasto/Ampure Unite Home Assistant custom integration

[![Tests](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml/badge.svg)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Latest release](https://img.shields.io/github/v/release/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration?label=latest%20release)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/releases)

Home Assistant custom integration for Webasto Unite and Ampure Unite EV chargers over local Modbus/TCP.

This is a community project developed with significant AI assistance. Active charging control should be verified on your own charger and vehicle before relying on automation.

## Features

- Local Modbus/TCP monitoring
- Keepalive handling
- Current control through register `5004`
- Dynamic Load Balancing (DLB)
- Solar charging
- Optional Lovelace dashboard and automation examples

Supported charge modes:

- `Off`
- `Normal`
- `Solar`
- `Fixed Current`

Solar strategies:

- `Eco Solar`
- `Smart Solar`

Temporary session overrides:

- `Solar Until Unplug`
- `Fixed Current Until Unplug`

## Requirements

- Home Assistant is already running.
- HACS is installed if you want to install through HACS.
- The charger has network connectivity and a fixed IP address.
- `Modbus/TCP` is enabled in the charger's web interface.
- The Modbus/TCP port is normally `502`; the Webasto/Ampure Unite unit ID is often `255`.
- No other system keeps an active `Modbus/TCP` connection open to the charger. The charger appears to work reliably with only one active Modbus master.
- DLB and Solar control require suitable Home Assistant sensors.

## Installation

### HACS custom repository

1. Open `HACS`.
2. Open the top-right menu and choose `Custom repositories`.
3. Add this repository URL:
   `https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration`
4. Select category `Integration`.
5. Click `Add`.
6. Search for `Webasto/Ampure Unite`.
7. Open the integration and click `Download`.
8. Restart Home Assistant.
9. Go to `Settings` -> `Devices & Services`.
10. Click `Add Integration`.
11. Search for `Webasto/Ampure Unite` and complete the config flow.

### Manual installation

Copy `custom_components/webasto_unite` to `config/custom_components/webasto_unite`, restart Home Assistant, and add the integration through `Settings` -> `Devices & Services` -> `Add Integration`.

## Documentation

- [Configuration guide](docs/configuration.md): setup, one-screen settings layout, sensor choices, DLB, Solar charging and troubleshooting.
- [Dashboard examples](examples): optional Lovelace dashboard and automation examples.

Start conservatively: first confirm monitoring works, then set `Integration Charging Control` to `Enabled`, and only then enable DLB and Solar charging.

`Integration Charging Control = Monitoring Only` is the safest first setup. In this mode the integration keeps the charger alive and monitors all values, but it does not write charging-current commands.

## Settings Overview

The integration options are grouped into one settings screen with these sections:

- `Connection`: charger network and Modbus settings
- `Charging`: installed phases, default mode and current limits
- `Temporary Session Settings`: temporary per-session Fixed Current and Solar Until Unplug behavior when managed control is enabled
- `Dynamic Load Balancing`: exposes DLB mode, sensor scope, fuse settings and DLB sensors in one section
- `Solar Charging`: exposes Solar strategy, input source, thresholds and timing settings in one section
- `Advanced`: keepalive, control sensor freshness and communication tuning

This keeps the full configuration in one place while preserving the same validation rules as before.

## Notes

- Automatic and manual phase switching are removed in this stability release. Remove old phase-switching dashboard controls, services and automations from custom dashboards.
- DLB and Solar charging are disabled by default and should be enabled only after selecting suitable sensors.
- DLB uses per-phase current sensors only. In `1p` setup, only L1 is required; in `3p`, L1/L2/L3 are required.
- DLB and Solar input sensors must be live power/current sensors. If a required sensor stops updating for longer than `Control Sensor Timeout (s)`, the integration falls back safely instead of trusting stale values.
- For Solar with a signed grid power sensor, choose the sign direction by looking at the sensor while exporting and not charging: negative export means export is below zero, positive export means export is above zero.
- Session command register `5006` is not used for start/stop control. The integration uses register `5004` current control instead.

## Diagnostics and Troubleshooting

If the charger does not behave as expected, first check these entities:

- `Connected` and `Client Error`: Modbus connection status.
- `Final Target`: current the integration is currently requesting.
- `Control Reason`: why the current target was chosen.
- `Fallback Active` and `Sensor Invalid Reason`: whether DLB/Solar input is missing, stale or unsafe.
- `DLB Limit`: current limit calculated by Dynamic Load Balancing.
- `Solar Input State` and `Solar Surplus Input`: whether Solar input is valid and how much surplus the integration sees.

Common causes:

- Another Modbus client is connected to the charger.
- The charger IP address changed.
- Modbus/TCP is disabled in the charger web interface.
- A P1, grid power or template sensor stopped updating while Home Assistant kept showing the last value.
- A Solar signed grid power sensor has the wrong `Grid Power Direction`.

## Repository contents

- [`custom_components/webasto_unite`](custom_components/webasto_unite): integration code
- [`docs`](docs): configuration documentation
- [`examples`](examples): dashboard and automation examples
- [`tests`](tests): unit tests
- [`hacs.json`](hacs.json): HACS metadata
