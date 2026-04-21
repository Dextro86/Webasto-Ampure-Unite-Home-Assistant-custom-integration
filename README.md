# Webasto/Ampure Unite Home Assistant custom integration

[![Tests](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml/badge.svg)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Latest release](https://img.shields.io/github/v/release/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration?label=latest%20release)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/releases)

Home Assistant custom integration for Webasto Unite and Ampure Unite EV chargers over local Modbus/TCP.

This is a community project developed with significant AI assistance. Active charging control and phase switching should be verified on your own charger, vehicle and firmware before relying on automation.

## Features

- Local Modbus/TCP monitoring
- Keepalive handling
- Current control through register `5004`
- Dynamic Load Balancing (DLB)
- PV surplus charging
- Manual 1P/3P phase switching through register `405`
- Experimental automatic PV 1P/3P phase switching
- Optional Lovelace dashboard and automation examples

Supported charge modes:

- `Off`
- `Normal`
- `PV`
- `Fixed Current`

PV strategies:

- `Surplus Only`
- `Minimum + Surplus`

Temporary session overrides:

- `PV Until Unplug`
- `Fixed Current Until Unplug`

## Requirements

- Home Assistant is already running.
- HACS is installed if you want to install through HACS.
- The charger has network connectivity and a fixed IP address.
- `Modbus/TCP` is enabled in the charger's web interface.
- No other system keeps an active `Modbus/TCP` connection open to the charger.
- DLB and PV control require suitable Home Assistant sensors.

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

- [Configuration guide](docs/configuration.md): setup, one-screen settings layout, sensor choices, DLB, PV charging, phase switching and troubleshooting.
- [Dashboard examples](examples): optional Lovelace dashboard and automation examples.

Start conservatively: first confirm monitoring works, then enable `Managed Charging Control`, and only then enable DLB, PV charging or automatic phase switching.

## Settings Overview

The integration options are grouped into one settings screen with these sections:

- `Connection`: charger network and Modbus settings
- `General Charging`: installed phases, startup/default mode and current limits
- `Dynamic Load Balancing`: DLB input model, fuse limit and live sensor mapping
- `PV Charging`: PV mode, PV sensor input and PV thresholds
- `Phase Switching`: manual and automatic 1P/3P switching for 3-phase installations

This keeps the full configuration in one place while preserving the same validation rules as before.

## Notes

- Automatic PV 1P/3P phase switching is experimental.
- Register `405` has been validated on one charger with firmware `3.187`; other firmware versions may behave differently.
- DLB and PV charging are disabled by default and should be enabled only after selecting suitable sensors.
- For 3-phase DLB, use per-phase current sensors. Grid-power DLB is only suitable as a 1-phase approximation.
- Session command register `5006` is not used for start/stop control. The integration uses register `5004` current control instead.

## Repository contents

- [`custom_components/webasto_unite`](custom_components/webasto_unite): integration code
- [`docs`](docs): configuration documentation
- [`examples`](examples): dashboard and automation examples
- [`tests`](tests): unit tests
- [`hacs.json`](hacs.json): HACS metadata
