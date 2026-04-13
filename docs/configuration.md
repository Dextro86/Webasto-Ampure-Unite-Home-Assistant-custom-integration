# Configuration guide

This guide explains the main settings for the Webasto/Ampure Unite Home Assistant custom integration.

Start conservatively. First confirm that monitoring works and that the charger values look correct. Only then enable active charging control.

## Requirements

Before using the integration:

- Home Assistant is running.
- HACS is installed if you use the HACS installation method.
- The charger has network connectivity and a fixed IP address.
- `Modbus/TCP` is enabled in the charger's web interface.
- No other system keeps an active `Modbus/TCP` connection open to the charger. The charger appears to accept only one active Modbus client at a time.
- DLB and PV control require suitable Home Assistant sensors.

## Connection and control

Main settings:

- `Charger IP address`: fixed IP address of the charger.
- `Port`: Modbus TCP port, normally `502`.
- `Modbus Unit ID`: unit ID used by the charger.
- `Charger phase configuration`: installed charger phase configuration, usually `1 Phase` or `3 Phases`.
- `Refresh interval`: how often the integration refreshes charger state.
- `Modbus timeout` and `Modbus retries`: connection resilience settings.
- `Control mode`: whether the integration may actively control charging.

Recommended first setup:

1. Use `Read-only + Keepalive`.
2. Confirm that monitoring, connection state, currents, power and firmware values look correct.
3. Switch to `Managed Charging Control` only after the read-only values are plausible.

## Current limits

Important current settings:

- `Minimum current`: lower current limit used by control logic.
- `Maximum current`: configured upper current limit.
- `Default current limit`: normal target current for `Normal` mode.
- `Safe current on failure`: fallback current used when control input is unavailable or invalid.
- `Fixed Current`: target used by `Fixed Current` mode.

The final current target can still be limited by the charger-reported session limit, DLB, safety settings or fallback behavior.

## Dynamic Load Balancing

Dynamic Load Balancing (DLB) reduces the charger current when house load gets close to the configured main fuse limit.

DLB measurement sources:

- `Disabled`: do not use DLB.
- `Phase current sensors (recommended)`: use separate L1, L2 and L3 current sensors.
- `Grid power sensor`: calculate available current from a power sensor.

DLB sensor scope:

- `Total house current charger excluded`: sensors measure house load without the charger.
- `Total house current charger included`: sensors measure total house load including the charger.

If your sensors include the charger load, select `Total house current charger included`. The integration then compensates for the charger's own measured current before calculating the DLB limit. This prevents the charger from immediately reducing itself just because its own load appears in the house-current sensors.

Example:

```text
main fuse = 25 A
safety margin = 2 A
highest measured phase = 18 A including charger
charger measured current = 15 A
available current estimate = 25 - 2 - (18 - 15) = 20 A
```

## PV charging

PV control strategy:

- `Disabled`: do not use PV charging.
- `Surplus only`: charge only when enough surplus is available.
- `Minimum + surplus`: keep charging at minimum current and add surplus when available.

PV surplus can be provided in two ways:

- A dedicated surplus power sensor.
- A signed net grid power sensor where negative values mean export to the grid.

Do not use separate production and consumption sensors directly unless you first combine them into one surplus sensor.

If your consumption sensor includes the charger, calculate surplus like this:

```text
surplus = PV production - total consumption + charger power
```

This avoids the common issue where export drops to zero as soon as the charger starts using the available solar power.

## PV until unplug

`PV until Unplug` is a temporary session override.

It does not permanently change the selected base `Charge mode`. It stays active until the vehicle is unplugged or until you disable the override manually.

The `PV until unplug strategy` can inherit the normal PV strategy or use a separate PV strategy for this temporary session.

## Phase switching

PV phase switching modes:

- `Disabled`: do not expose or use phase switching through the integration.
- `Manual only`: expose `Phase switch mode`, but do not switch automatically.
- `Automatic 1P/3P`: allow automatic phase switching in PV mode.

Manual phase switching uses register `405` and is only available when phase switching is not disabled.

Automatic phase switching only runs in `PV` mode or `PV until Unplug`. It does not run in `Normal`, `Fixed Current` or `Off`.

The integration performs phase switching conservatively:

1. It writes `0 A` to pause charging.
2. It waits for a later refresh cycle where the charger reports that charging is no longer active.
3. It writes phase-switch register `405`.
4. It resumes charging after the charger reports the requested phase mode.

Register `405` has been validated on one charger with firmware `3.187`. Other firmware versions may behave differently.

## Important entities

Daily-use entities:

- `Charge mode`: selected base charging mode.
- `Allow charging`: user switch for whether charging is allowed.
- `Phase switch mode`: manual 1P/3P phase selection when available.
- `PV until Unplug`: temporary PV session override.
- `Fixed Current until Unplug`: temporary fixed-current session override.
- `Current limit`: normal target current.
- `Fixed Current`: target used in fixed-current mode.
- `Active mode`: effective mode after overrides and runtime behavior.
- `Charging behavior`: dashboard-friendly status summary.
- `Final target`: final current target after limits and safety logic.
- `DLB limit`: current limit calculated by DLB.

Useful diagnostics:

- `Connected`
- `Client error`
- `Control reason`
- `Dominant limit`
- `Sensor invalid reason`
- `Write queue depth`
- `Phase switch mode code`

## Troubleshooting basics

If setup or updates fail:

- Confirm that no other Modbus client is connected to the charger.
- Confirm that the charger IP address is fixed and reachable.
- Confirm that `Modbus/TCP` is enabled in the charger web interface.
- Start with `Read-only + Keepalive` before enabling managed control.
- Check `Client error`, `Connected`, `Sensor invalid reason` and `Write queue depth`.
