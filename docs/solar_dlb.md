# Solar Charging And Dynamic Load Balancing

This integration contains local control logic for Solar surplus charging and Dynamic Load Balancing.

The goal is stable behavior, not aggressive short-term chasing of every sensor change.

Solar current control and phase policy behavior follow the [Behavior contract](behavior_contract.md). Automatic Solar phase switching is experimental and only writes register `405` after stable target, cooldown and session-count guards pass.

## Solar Charging

Solar charging supports three strategies:

| Strategy | Behavior |
|---|---|
| `Eco Solar` | Charges only when enough Solar surplus is available. Pauses on invalid Solar input. |
| `Smart Solar` | Uses at least Solar Minimum Current when Solar input is valid, then adds available surplus. |
| `Solar Boost` | Uses Solar Minimum Current plus available surplus. |

## Solar Input Sources

Supported Solar input models:

- `Solar Surplus Sensor`
- `Signed Grid Power Sensor`
- `DSMR Import/Export Sensors`

### Solar Surplus Sensor

Use this when the selected sensor already reports available surplus power.

Important:

- The sensor should report live power.
- It should not become stale when surplus is `0 W`.
- It should expose a supported unit if `Require Solar Sensor Units` is enabled.

### Signed Grid Power Sensor

Use this when one sensor reports grid import/export.

Configure `Grid Power Direction`:

- `Negative Export`: export is below zero.
- `Positive Export`: export is above zero.

The integration derives usable Solar surplus from this value and charger power.

### DSMR Import/Export Sensors

Recommended for P1/DSMR setups with separate import and export sensors.

The integration calculates signed grid power internally:

```text
signed_grid_power = import_power - export_power
```

This avoids common mistakes with sensors that show production/export as a separate positive value.

## Solar Stability Features

Solar control includes:

- smoothing/filtering of Solar input
- ramp limiting for current increases
- start/stop thresholds
- start/stop delays
- minimum runtime and pause timers
- stale sensor protection
- phase-aware current calculation based on charger configuration and observed active phases

DLB and safety reductions can still reduce current immediately.

## Dynamic Load Balancing

DLB uses Home Assistant phase current sensors to keep charging below the configured connection limit.

Configuration concepts:

- `Main Fuse (A)`: available connection current.
- `Safety Margin (A)`: margin kept below the main fuse limit.
- `Sensor Scope`: whether sensors include or exclude charger load.
- `Require Sensor Units`: reject unitless sensors when enabled.
- L1/L2/L3 sensors: current sensors for each configured phase.

For `1P` charger configuration, L1 is required.

For `3P` charger configuration, L1, L2 and L3 are required.

## Stale Sensor Protection

DLB and Solar sensors must update regularly. If a required sensor is older than `Control Sensor Timeout (s)`, the integration treats it as unsafe.

Typical behavior:

- DLB falls back to `Fallback Current`.
- Eco Solar pauses.
- Smart Solar and Solar Boost follow `Solar Sensor Failure Behavior`.

This protects against situations where Home Assistant still shows an old value while the real sensor has stopped updating.

## Current Control

The integration controls charging current through register `5004`.

It uses `0 A` as pause command. It does not use a separate charger session command register for start/stop control.

`Charging Enabled` is the user-facing control for this same behavior. It does not end the charger session; it only disables or re-enables charging through the integration's current-control flow.

To avoid current bouncing, normal current writes require a meaningful change, a short write throttle and stable target cycles. If the target does not become perfectly stable, the integration eventually writes the latest target after an internal maximum wait instead of waiting indefinitely.

## Phase Awareness

The integration observes actual charger phase currents and exposes them through `Observed Phase`.

This is used by DLB and Solar to avoid treating a 1-phase vehicle on a 3-phase charger the same as a 3-phase charging session.

The configured `Charger Configuration` still matters:

- `1P`: the charger is physically/configurationally treated as one phase.
- `3P`: the charger is treated as three-phase capable, while the active vehicle/session may still be observed as 1P.
