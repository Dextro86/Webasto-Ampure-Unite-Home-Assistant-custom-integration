# Webasto/Ampure Unite Home Assistant custom integration

[![Tests](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml/badge.svg)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Latest release](https://img.shields.io/github/v/release/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration?label=latest%20release)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/releases)

Home Assistant custom integration for Webasto Unite and Ampure Unite EV chargers over local Modbus/TCP.

This is a community project developed with significant AI assistance. It has been tested on one charger so far, so verify behavior on your own hardware before relying on automated charging control.

## Features

| Feature | Status |
| --- | --- |
| Local Modbus monitoring | Validated on one charger |
| Keepalive handling | Validated on one charger |
| Current control through register `5004` | Validated on one charger |
| Dynamic Load Balancing (DLB) | Validated on one charger |
| Manual 1P/3P phase switching through register `405` | Partially validated |
| PV charging | Partially validated |
| Automatic PV 1P/3P phase switching | Experimental |

Supported charge modes:

- `Off`
- `Normal`
- `PV`
- `Fixed Current`

Temporary session overrides:

- `PV until Unplug`
- `Fixed Current until Unplug`

## Documentation

- [Configuration guide](docs/configuration.md): setup screens, DLB, PV charging, phase switching and important Home Assistant entities.
- [Dashboard examples](examples): optional Lovelace dashboard and automation examples.

## Compatibility

| Charger / firmware | Status in this project | Notes |
| --- | --- | --- |
| Webasto/Ampure Unite firmware `3.187` | Tested | Main development and validation firmware. |
| UNITE HMI `3.156` | Not tested | Listed by NeLeSo as the latest stable Webasto/Ampure UNITE firmware. |
| UNITE HMI `3.166` | Not tested | Listed by NeLeSo for specific larger dynamic load-management clusters. |
| Other UNITE firmware versions | Unknown | Register behavior may differ and should be verified carefully. |

Firmware background: see the NeLeSo Webasto/Ampure UNITE firmware page: <https://www.neleso.com/unite-downloads>.

## Requirements

Before installing:

- Home Assistant is already running.
- HACS is installed if you want to install through HACS.
- The charger has network connectivity and a fixed IP address.
- `Modbus/TCP` is enabled in the charger's web interface.
- No other system keeps an active `Modbus/TCP` connection open to the charger. The charger appears to accept only one active Modbus client at a time.
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

## Home Assistant entities

Important entities for daily use:

- `Charge mode`
- `Allow charging`
- `Phase switch mode`
- `PV until Unplug`
- `Fixed Current until Unplug`
- `Current limit`
- `Fixed Current`
- `Active mode`
- `Charging behavior`
- `Final target`
- `DLB limit`

Useful diagnostics:

- `Connected`
- `Client error`
- `Control reason`
- `Dominant limit`
- `Sensor invalid reason`
- `Write queue depth`
- `Phase switch mode code`

`Charge mode` is the selected base mode. `Active mode` shows what the integration is actually doing after overrides and PV behavior are applied. `Charging behavior` is a short dashboard-friendly status summary.

## Known limitations

- Register `405` is used for phase switching and has been validated on firmware `3.187` with one tested charger. Other firmware versions may behave differently.
- Automatic PV phase switching is implemented but still experimental until validated with more charging sessions, vehicles and firmware versions.
- Manual switching back to 3-phase may require a pause/resume cycle before the charger applies the new phase mode.
- Session command register `5006` is not used for start/stop control. The integration uses register `5004` current control instead.
- Power-based DLB and PV calculations use a practical nominal `230 V` conversion.

## Examples

Dashboard examples:

- [`examples/lovelace_dashboard.yaml`](examples/lovelace_dashboard.yaml)
- [`examples/lovelace_basic.yaml`](examples/lovelace_basic.yaml)
- [`examples/lovelace_advanced.yaml`](examples/lovelace_advanced.yaml)
- [`examples/lovelace_troubleshooting.yaml`](examples/lovelace_troubleshooting.yaml)

Automation examples:

- [`examples/automation_enable_pv_until_unplug.yaml`](examples/automation_enable_pv_until_unplug.yaml)
- [`examples/automation_disable_pv_until_unplug.yaml`](examples/automation_disable_pv_until_unplug.yaml)
- [`examples/automation_enable_fixed_current_until_unplug.yaml`](examples/automation_enable_fixed_current_until_unplug.yaml)
- [`examples/automation_disable_fixed_current_until_unplug.yaml`](examples/automation_disable_fixed_current_until_unplug.yaml)

## Repository contents

- [`custom_components/webasto_unite`](custom_components/webasto_unite): integration code
- [`examples`](examples): dashboard and automation examples
- [`tests`](tests): unit tests
- [`hacs.json`](hacs.json): HACS metadata
