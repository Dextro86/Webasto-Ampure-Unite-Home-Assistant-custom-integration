# Webasto / Ampure Unite for Home Assistant

[![Tests](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml/badge.svg)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Latest release](https://img.shields.io/github/v/release/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration?label=latest%20release)](https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration/releases)

Advanced local control, Solar charging and Dynamic Load Balancing for Webasto Unite and Ampure Unite EV chargers.

This integration controls and monitors Webasto/Ampure Unite chargers directly over local Modbus/TCP. It is not a cloud integration and it is not a generic Modbus wrapper. The focus is stable charger control inside Home Assistant, with Solar surplus charging, Dynamic Load Balancing, restart-safe behavior, diagnostics and EVCC-oriented entities.

This is a community project developed with significant AI assistance. Active charging control should be verified on your own charger and vehicle before relying on automation.

## Features

| Feature | Supported |
|---|---|
| Local Modbus/TCP monitoring | Yes |
| Cloud-free operation | Yes |
| Keepalive handling | Yes |
| Current control through register `5004` | Yes |
| Solar surplus charging | Yes |
| Dynamic Load Balancing | Yes |
| Session-aware charging logic | Yes |
| Derived IEC 61851 state | Yes |
| EVCC compatibility entities | Yes |
| Solar smoothing/filtering | Yes |
| Solar ramp limiting | Yes |
| Stale sensor protection | Yes |
| Restart-safe charging state | Yes |
| Reconnect handling | Yes |
| Advanced diagnostics | Yes |
| Manual 1P/3P phase switching | Experimental, off by default |
| Automatic Solar phase switching | Experimental, opt-in |

## Why this integration exists

Many charger integrations and generic Modbus examples expose only basic charger data. This integration is specific to Webasto/Ampure Unite chargers and aims to provide a stable local charger-control platform for Home Assistant.

Special focus areas:

- predictable charging behavior
- safe behavior after Home Assistant restarts
- Dynamic Load Balancing protections
- Solar charging smoothing and filtering
- stale sensor detection
- detailed diagnostics for troubleshooting and support
- EVCC-oriented status and current-control entities

## Architecture

```text
Home Assistant
      |
      v
Webasto / Ampure Unite integration
      |
      v
Local Modbus/TCP
      |
      v
Webasto / Ampure Unite charger
```

The integration communicates directly with the charger over the local network. No cloud connection is required.

## Quick Start

1. Install the integration through HACS or manually.
2. Restart Home Assistant.
3. Add `Webasto/Ampure Unite` through `Settings` -> `Devices & Services`.
4. Start with `Integration Charging Control = Monitoring Only`.
5. Confirm that charger state, currents, power and energy sensors update correctly.
6. Set `Integration Charging Control = Enabled` only after monitoring is stable, or use `External Controller` when EVCC or another controller should manage charging current.
7. Enable DLB and Solar only after selecting suitable live Home Assistant sensors.

Detailed instructions: [Installation](docs/installation.md)

## Documentation

- [Installation](docs/installation.md)
- [Full configuration reference](docs/configuration.md)
- [Behavior contract](docs/behavior_contract.md)
- [Public API](docs/public_api.md)
- [EVCC compatibility](docs/evcc.md)
- [Solar charging and Dynamic Load Balancing](docs/solar_dlb.md)
- [Diagnostics](docs/diagnostics.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Dashboard examples](examples)

## Supported Charge Modes

- `Off`
- `Normal`
- `Eco Solar`
- `Smart Solar`
- `Solar Boost`
- `Fixed Current`

The configured `Default Solar Mode` is used after restart when `Default Mode` is set to Solar. During normal use, the `Charge Mode` entity lets you select a specific Solar mode directly.

Temporary session overrides:

- `Solar Until Unplug`
- `Fixed Current Until Unplug`

## Solar Charging And DLB

The integration contains logic for Solar surplus charging and Dynamic Load Balancing:

- Solar input models: Solar surplus sensor, signed grid power sensor, DSMR import/export sensors
- Solar smoothing and filtering to reduce bouncing
- Ramp limiting for Solar current increases
- Dynamic Load Balancing based on phase current sensors
- Control Sensor Timeout protection against stale Home Assistant sensor values
- Conservative fallback behavior when sensor input is unavailable or unsafe

See [Solar charging and Dynamic Load Balancing](docs/solar_dlb.md).

## EVCC Compatibility

The integration exposes entities and diagnostics that can be used by EVCC through Home Assistant:

- derived IEC 61851 state
- charger enable/disable switch
- current-control number entity
- measured power and phase currents
- session energy
- observed active phases
- readable and machine-oriented diagnostics

Use `Integration Charging Control = External Controller` when EVCC is the active charging manager. Check the actual Home Assistant entity IDs before copying the example configuration.

See [EVCC compatibility](docs/evcc.md) and the [EVCC Home Assistant example](examples/evcc_home_assistant.yaml).

## Behavior Contract

The intended runtime behavior is documented in [Behavior contract](docs/behavior_contract.md). In short:

- plug/unplug never writes the phase register
- automatic Solar phase switching only writes after stable policy/cooldown guards
- phase writes happen only through explicit manual/EVCC requests or guarded Automatic Solar execution
- `Charging Enabled` is a current-control helper, not a real session stop/start command
- `Requested Phase` and `Observed Phase` are separate on purpose

## Phase Switching

Phase switching is experimental and off by default. `Manual Only` exposes explicit 1P/3P buttons, services and an EVCC-compatible phase select. `Automatic Solar` lets the Solar policy request 1P/3P writes after the target has been stable long enough and cooldown/session-count guards allow it.

The known register mapping used by the integration is:

- input register `404`: charger-reported phase capability/configuration context. This is diagnostic only; field testing showed it can report `1P` while the charger is physically charging on 3 phases.
- holding register `405`: requested phase-switch mode (`0 = 1P`, `1 = 3P`). Manual phase switching writes this register directly.

Phase diagnostics are intentionally consolidated:

- `Requested Phase`: requested phase mode from register `405`.
- `Observed Phase`: physical phase usage derived from measured L1/L2/L3 current.
- `Phase Recovery State`: current phase-switch/recovery state plus reason attributes.

Measured active phases are diagnostic only. A 1P vehicle on a 3P charger is normal and is not treated as a vehicle capability claim. Lower-level details such as register `404`, raw register `405`, policy target, session override, offer state, consistency and block reasons are exposed as attributes on these three phase sensors or through diagnostics, not as separate normal entities.

Manual switching now follows the same simple model as EVCC's native Vestel/Webasto driver: validate the request, write register `405`, mark the switch as settling and let the normal control loop continue current control. It does not pause charging, resume charging, retry, verify the register in a loop or use measured phases as a hard success/failure condition. An explicit request writes `405` even if `Requested Phase` already shows the same target. `Requested Phase` shows register `405`; `Observed Phase` remains diagnostic.

`Restore Configured Phase` writes the configured `Charger Configuration` (`1P` or `3P`) back to register `405` when a vehicle is connected. Without a connected vehicle it only clears the runtime phase override state.

Manual switching away from `Charger Configuration` is treated as temporary for the connected session. On unplug, the integration only clears its runtime/session state and resets the runtime charge mode to the configured `Default Mode`; it does not write register `405` during charger session shutdown. A new plug-in session no longer triggers automatic 3P restore or recovery writes.

Automatic Solar phase switching uses the same direct register-write path as manual switching. It can request `1P` or `3P` only after the target has remained stable, cooldown is clear and the session switch limit has not been reached. If register `405` says `3P` while charging physically remains 1P, the integration reports the mismatch and keeps charging instead of starting automatic recovery. In `External Controller` mode, EVCC may still request phase switches through the phase select; this integration's own Solar phase switching does not run there.

## Stability-First Design

This integration prioritizes:

- predictable behavior over aggressive automation
- conservative fallback behavior
- restart-safe state handling
- stale sensor protection
- diagnostics that explain why current was reduced or charging paused
- avoiding automatic recovery loops that repeatedly write to the charger

## Requirements

- Home Assistant
- Webasto Unite or Ampure Unite charger
- Charger reachable over the local network
- Modbus/TCP enabled on the charger
- TCP port `502` reachable from Home Assistant
- Recommended Modbus unit ID: `255`
- No other system keeping an active Modbus/TCP connection open to the charger

The charger appears to work reliably with only one active Modbus master connection.

## Troubleshooting First Checks

If charging behavior is unexpected, check:

- `Connected`
- `Client Error`
- `Control Reason`
- `Final Target`
- `Fallback Active`
- `Sensor Invalid Reason`
- `DLB Limit`
- `Solar Input State`
- `Solar Raw Input`
- `Solar Filtered Input`
- `EVCC Status`

See [Diagnostics](docs/diagnostics.md) and [Troubleshooting](docs/troubleshooting.md).

## Repository Contents

- [`custom_components/webasto_unite`](custom_components/webasto_unite): integration code
- [`docs`](docs): documentation
- [`examples`](examples): dashboard and automation examples
- [`tests`](tests): unit tests
- [`hacs.json`](hacs.json): HACS metadata

## Disclaimer

This project is not affiliated with Webasto, Ampure or EVCC.
