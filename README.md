# Webasto/Ampure Unite Home Assistant custom integration

Experimental Home Assistant custom integration for `Webasto Unite` / `Ampure Unite` chargers over local `Modbus/TCP`.

## Status

This project was developed with significant AI assistance.

It is currently:

- experimental
- not broadly validated on real hardware yet
- not field-proven for day-to-day charging use

Use it at your own risk.

The most important open validation points are:

- experimental manual phase-switch register `405`
- behavior across multiple Unite / Ampure firmware versions

## What it does

The integration currently supports:

- local Modbus-based charger monitoring
- keepalive handling for Unite control sessions
- Dynamic Load Balancing (DLB)
- PV charging
- Fixed Current charging
- temporary per-session overrides:
  - `PV until Unplug`
  - `Fixed Current until Unplug`

## How it works

The integration runs locally through `Modbus/TCP`.

On every update cycle it:

- reads charger state and measurements from the wallbox
- reads optional Home Assistant sensors for DLB and PV control
- calculates a target current from the selected charge mode
- applies safety limits such as DLB, configured maximum current and charger-reported cable/EV limits when available
- writes a new current target only when control is enabled and a change is needed
- pauses charging by writing `0 A` to the current-control register `5004`

The selected `Charge mode` describes what the user wants. `Active mode` shows what the integration is actually doing after temporary overrides, pauses and PV behavior are applied. `Charging behavior` is a short status summary for dashboards.

## Requirements

Before installing this integration, make sure:

- Home Assistant is already running
- HACS is already installed if you want to install through HACS
- the charger has network connectivity
- the charger has a fixed IP address
- `Modbus/TCP` is enabled in the charger's web interface
- no other system keeps an active `Modbus/TCP` connection open to the charger
- the charger accepts only one active `Modbus/TCP` client at a time

If you want to use DLB or PV control, make sure the required Home Assistant sensors already exist.

## Installation

### HACS custom repository

1. Make sure HACS is already installed in Home Assistant.
2. Open `HACS`.
3. Open the menu in the top-right corner and choose `Custom repositories`.
4. Add this repository URL:
   - `https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration`
5. Select category:
   - `Integration`
6. Click `Add`.
7. Search for `Webasto/Ampure Unite` in HACS.
8. Open the integration and click `Download`.
9. Restart Home Assistant.
10. Go to `Settings` -> `Devices & Services`.
11. Click `Add Integration`.
12. Search for `Webasto/Ampure Unite` and complete the config flow.

### Manual installation

Copy:

- `custom_components/webasto_unite`

to:

- `config/custom_components/webasto_unite`

Then restart Home Assistant and add the integration through:

- `Settings` -> `Devices & Services` -> `Add Integration`

## Main modes

The integration exposes these charge modes:

- `Off`
- `Normal`
- `PV`
- `Fixed Current`

It also exposes two temporary session overrides:

- `PV until Unplug`
- `Fixed Current until Unplug`

Those overrides do not permanently change the selected base `Charge mode`. They stay active until the vehicle is unplugged.

## PV behavior

PV mode supports:

- `Disabled`
  - do not use PV charging
- `Surplus only`
  - only charge when there is enough surplus
- `Minimum + surplus`
  - keep charging at minimum current and use extra surplus to scale up

This means:

- if you do not want to configure PV charging yet, use `Disabled`
- if you want strict surplus charging, use `Surplus only`
- if you want more practical winter/cloud behavior, use `Minimum + surplus`

PV surplus can be provided in two ways:

- use a dedicated surplus power sensor
- use a signed net grid power sensor where negative values mean export to the grid

Do not use the signed grid power option with separate production and consumption sensors unless you first combine them into a single surplus sensor. If the consumption sensor includes the charger, the charger power must be added back when calculating surplus:

```text
surplus = PV production - total consumption + charger power
```

This avoids the common issue where export drops to zero as soon as the charger starts using the available solar power.

## What the user sees in Home Assistant

The most important entities for daily use are:

- `Charge mode`
- `Allow charging`
- `PV until Unplug`
- `Fixed Current until Unplug`
- `Current limit`
- `Fixed Current`
- `Active mode`
- `Charging behavior`
- `Final target`
- `DLB limit`

In general:

- `Charge mode` is the selected base mode
- `Active mode` is what the integration is actually doing right now
- `Charging behavior` is the human-friendly summary of the current runtime behavior

Example:

- base `Charge mode = Normal`
- `PV until Unplug = on`
- `Active mode = PV`

## Configuration summary

During setup, the user mainly configures:

- charger connection:
  - host
  - port
  - unit id
- charger phase configuration:
  - `1p` or `3p`
- control mode:
  - `Read-only + Keepalive`
  - `Managed Charging Control`
- DLB measurement source:
  - `Disabled`
  - `Phase current sensors (recommended)`
  - `Grid power sensor`
- what the DLB sensors measure:
  - `Total house current charger excluded` for sensors that measure only non-charger house load
  - `Total house current charger included` for main/grid sensors that include the charger load
- PV measurement source and strategy
- current limits and safety values

For Unite, `Read-only + Keepalive` is the safest first active mode.

The charger connection fields and charger phase configuration can also be changed later from the integration settings. The integration reloads after saving settings so the new values take effect.

## Dashboard examples

This repository includes example Lovelace dashboards:

- [`examples/lovelace_dashboard.yaml`](examples/lovelace_dashboard.yaml)
- [`examples/lovelace_basic.yaml`](examples/lovelace_basic.yaml)
- [`examples/lovelace_advanced.yaml`](examples/lovelace_advanced.yaml)
- [`examples/lovelace_troubleshooting.yaml`](examples/lovelace_troubleshooting.yaml)

It also includes simple automation examples for the temporary per-session overrides:

- [`examples/automation_enable_pv_until_unplug.yaml`](examples/automation_enable_pv_until_unplug.yaml)
- [`examples/automation_disable_pv_until_unplug.yaml`](examples/automation_disable_pv_until_unplug.yaml)
- [`examples/automation_enable_fixed_current_until_unplug.yaml`](examples/automation_enable_fixed_current_until_unplug.yaml)
- [`examples/automation_disable_fixed_current_until_unplug.yaml`](examples/automation_disable_fixed_current_until_unplug.yaml)

## Known limitations

At the current stage, assume the following:

- register `405` is exposed as the diagnostic `Phase switch mode raw` sensor and as an experimental manual `Phase switch` control; only change it while charging is inactive
- session command register `5006` is not used for start/stop control; `5004` current control is used instead
- the integration has not yet been broadly validated across multiple chargers and firmware versions
- power-based DLB and PV calculations use a practical nominal `230 V` conversion

## Support expectations

This repository should currently be treated as an experimental custom integration, not as a production-grade officially validated package.

If you try it:

- read the warnings above
- start conservatively
- prefer `Read-only + Keepalive` first
- verify behavior on your own hardware before relying on it

## Repository contents

The main integration code lives in:

- [`custom_components/webasto_unite`](custom_components/webasto_unite)

Additional files:

- [`examples`](examples)
- [`tests`](tests)
- [`LICENSE`](LICENSE)
- [`hacs.json`](hacs.json)
