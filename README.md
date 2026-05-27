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
| Automatic phase switching | Not included |

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
- pause/resume buttons
- measured power and phase currents
- session energy
- observed active phases
- readable and machine-oriented diagnostics

Use `Integration Charging Control = External Controller` when EVCC is the active charging manager. Check the actual Home Assistant entity IDs before copying the example configuration.

See [EVCC compatibility](docs/evcc.md) and the [EVCC Home Assistant example](examples/evcc_home_assistant.yaml).

## Phase Switching

Automatic phase switching is not included.

Experimental manual phase switching is available only when `Phase Switching Mode = Manual Only`. It is off by default and must be triggered explicitly through the 1P/3P buttons or services. The known register mapping used by the integration is:

- input register `404`: charger preconfigured phase count (`0 = 1P`, `1 = 3P`). If this reports 1P, phase switching is blocked.
- holding register `405`: phase-switch mode (`0 = 1P`, `1 = 3P`). Manual switching writes and verifies this register.

Measured active phases are diagnostic only. A 1P vehicle on a 3P charger is normal and is not treated as a mismatch. Manual phase switching is intended for testing and validation, not for unattended automation yet.

Manual switching now separates register verification from physical verification. The integration pauses charging with `0 A`, waits, writes register `405`, waits, verifies the register, waits again, resumes charging and then observes the measured active phases for multiple polls. `Register Verified` means register `405` accepted the request. `Physical Verified` means the measured charging phases also match the request.

`Restore Default Phase Mode` writes the configured `Charger Configuration` (`1P` or `3P`) back to register `405` and can run without a connected vehicle.

Manual switching away from `Charger Configuration` is treated as temporary for the connected session. After unplug, the integration tries to restore the configured phase mode.

The integration also exposes diagnostic-only phase policy sensors. These show what future Solar automatic phase switching would request, but they do not perform automatic switching.

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
