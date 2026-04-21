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

## Settings layout

The integration uses one options screen with the settings grouped in logical sections:

- `Connection`
- `General Charging`
- `Session Overrides`
- `Dynamic Load Balancing`
- `PV Charging`
- `Phase Switching`

This keeps the full configuration in one place. The validation rules remain the same: invalid sensor combinations, unsupported DLB modes or invalid PV thresholds are still rejected before the options are saved.

## Connection

Main settings:

- `Host`: fixed IP address or host name of the charger.
- `Port`: Modbus TCP port, normally `502`.
- `Unit ID`: Modbus unit ID used by the charger.
- `Polling Interval`: how often the integration refreshes charger state.

## General Charging

Main settings:

- `Installed Phases`: installed charger phase configuration, usually `1 Phase` or `3 Phases`.
- `Charging Control`: whether the integration may actively control charging.
- `Startup / Default Mode`: charge mode selected when Home Assistant starts or reloads the integration. The default is `Normal`.
- `Default Current Limit`: normal target current for `Normal` mode.
- `Safe Current`: fallback current used when DLB input is unavailable or invalid.

Recommended first setup:

1. Set `Charging Control` to `Off`.
2. Confirm that monitoring, connection state, currents, power and firmware values look correct.
3. Switch `Charging Control` to `On` only after the monitored values are plausible.

If you want the integration to return to PV charging after a Home Assistant restart, set `Startup Charge Mode` to `PV`. PV must still be configured with a valid PV strategy and sensor setup; otherwise startup falls back to `Normal`.

## Session Overrides

This section is only shown when `Charging Control` is set to `On`.

Main settings:

- `Fixed Current`: target used when `Fixed Current Until Unplug` is enabled for a session.
- `PV Until Unplug Strategy`: temporary PV strategy used while `PV Until Unplug` is active for a session.

These settings do not change the configured `Startup / Default Mode`. They only define how the temporary session overrides behave when those runtime overrides are enabled.

## Current Limits

Important current settings:

- `Default Current Limit`: normal target current for `Normal` mode.
- `Safe Current`: fallback current used when DLB input is unavailable or invalid.
- `Fixed Current`: target used by `Fixed Current` mode.

The final current target can still be limited by the charger-reported session limit, DLB, safety settings or fallback behavior.

`Default Current Limit` is also a general user limit. This means `Fixed Current` and `Fixed Current Until Unplug` can still be capped by `Default Current Limit`, `Maximum Current`, DLB and charger/session limits. Example: if `Fixed Current` is `16 A` but `Default Current Limit` is `10 A`, the final target will not exceed `10 A`.

If DLB input becomes unavailable, the integration falls back to `Safe Current on Failure`. This is intentional safety behavior. A low `Final Target` together with `Fallback Active = True` or a `Sensor Invalid Reason` usually means the integration is limiting charging because it cannot trust the configured DLB sensors.

## Dynamic Load Balancing

Dynamic Load Balancing (DLB) reduces the charger current when house load gets close to the configured main fuse limit.

DLB is disabled by default. Enable it only after selecting suitable Home Assistant sensors.

Main settings:

- `Dynamic Load Balancing Mode`

Additional DLB settings only appear after DLB is enabled:

- `Dynamic Load Balancing Sensor Scope`
- `Main Fuse Limit`
- `Safety Margin`
- `Phase 1 Current Sensor`
- `Phase 2 Current Sensor`
- `Phase 3 Current Sensor`
- `Grid Power Sensor`

DLB measurement sources:

- `Disabled`: do not use DLB.
- `Phase Current Sensors (Recommended)`: use separate L1, L2 and L3 current sensors from your smart meter or energy meter. This is the preferred DLB input because it matches how main fuses are normally loaded per phase. Use current sensors in `A` or `mA`; the integration normalizes them internally.
- `Grid Power Sensor`: calculate available current from a single live power sensor in `W` or `kW`. This is only supported for 1-phase charger configurations. It is not suitable as 3-phase fuse protection because one total power value cannot detect phase imbalance. For 3-phase DLB, use phase-current sensors.

For a 1-phase charger setup, only the L1 current sensor is required. For a 3-phase charger setup, select L1, L2 and L3 sensors.

Use live measurement sensors, not energy counters. Current sensors should report `A` or `mA`. Power sensors should report `W` or `kW`. Energy sensors such as `Wh` or `kWh` are not suitable for DLB because they represent accumulated energy, not current load.

DLB Sensor Scope:

- `Charger Excluded`: sensors measure house load without the charger.
- `Charger Included`: sensors measure total house load including the charger.

If your sensors include the charger load, select `Charger Included`. The integration then compensates for the charger's own measured current before calculating the DLB Limit. This prevents the charger from immediately reducing itself just because its own load appears in the house-current sensors.

If your sensors exclude the charger load, select `Charger Excluded`. In that case the integration assumes the house load sensors already represent non-charger load only.

Example:

```text
main fuse = 25 A
safety margin = 2 A
highest measured phase = 18 A including charger
charger measured current = 15 A
available current estimate = 25 - 2 - (18 - 15) = 20 A
```

## PV Charging

Main settings:

- `PV Charging Strategy`

Additional PV settings only appear after PV charging is enabled:

- `PV Measurement Source`
- `PV Surplus Sensor`
- `Start Surplus Threshold (W)`
- `Stop Surplus Threshold (W)`
- `PV Start Delay`
- `PV Stop Delay`
- `PV Minimum Runtime`
- `PV Minimum Pause`
- `PV Minimum Current`

PV Control Strategy:

- `Disabled`: do not use PV charging.
- `Surplus Only`: charge only when enough surplus is available.
- `Minimum + Surplus`: keep charging at minimum current and add surplus when available, but pause if the configured PV input is unavailable.

PV charging is disabled by default. Enable it only after selecting a suitable surplus or signed grid power sensor.

`Minimum + Surplus` is not pure surplus-only charging. It may charge at `PV Minimum Current` when there is little or no surplus, as long as the PV input is valid. Use `Surplus Only` if you want PV charging to wait until enough surplus is present.

If the configured PV input becomes unavailable, `Surplus Only` and `Minimum + Surplus` pause by writing `0 A`.

PV surplus can be provided in two ways:

- `Use a Surplus Power Sensor`: select a Home Assistant sensor that directly represents available PV surplus as current power in `W` or `kW`.
- `Use Signed Grid Power Sensor`: select a net grid power sensor where negative values mean export to the grid. For example, `-1800 W` means roughly `1800 W` export.

The surplus sensor must represent power that is available now. Do not select a daily energy production sensor or energy import/export counter.

Do not use separate production and consumption sensors directly unless you first combine them into one surplus sensor. If your smart meter provides separate live production and consumption power sensors, create a helper/template sensor that outputs the actual surplus power.

If your consumption sensor includes the charger, calculate surplus like this:

```text
surplus = PV production - total consumption + charger power
```

This avoids the common issue where export drops to zero as soon as the charger starts using the available solar power.

Use current power sensors, not energy counters. `W` and `kW` are valid. `Wh` and `kWh` are not suitable for PV control because they represent accumulated energy, not current surplus.

When PV power is converted to charging current, the integration uses the charger-reported phase voltages when they are plausible. If voltage data is missing or invalid, it falls back to `230 V` per phase.

PV thresholds:

- `PV Start Threshold (W)`: surplus must reach this value before `Surplus Only` starts charging.
- `PV Stop Threshold (W)`: if surplus stays below this value while charging, the integration may pause after the configured stop delay and minimum runtime.
- `PV Start Delay`: surplus must stay above the start threshold for this long before charging starts.
- `PV Stop Delay`: surplus must stay below the stop threshold for this long before charging stops.
- `PV Minimum Runtime`: once PV charging starts, keep it running for at least this long.
- `PV Minimum Pause`: after PV charging stops, wait this long before starting again.
- `PV Minimum Current`: lowest current target used when PV charging is allowed to run.

`PV Minimum Current` is not a PV maximum. There is no separate PV maximum-current setting. PV charging is capped by `Default Current Limit`, `Maximum Current`, DLB, safety behavior and charger/session limits.

## PV Until Unplug

`PV Until Unplug` is a temporary session override.

It does not permanently change the selected base `Charge Mode`. It stays active until the vehicle is unplugged or until you disable the override manually.

The `PV Until Unplug Strategy` can inherit the normal PV strategy or use a separate PV strategy for this temporary session.

## Phase Switching

This section is only shown for `3 Phases` installations with PV charging enabled.

PV Phase Switching modes:

- `Disabled`: do not expose or use phase switching through the integration.
- `Manual Only`: expose `Manual Phase Switch`, but do not switch automatically. Manual switching is only available while charging is inactive.
- `Automatic 1P/3P`: allow automatic phase switching in PV mode.

`PV Phase Switching Hysteresis (W)` controls the extra margin around the automatic 1P/3P switching point. The default is `500 W`. The hysteresis, dwell time and per-session limit are only shown when `Automatic 1P/3P` is selected.

`Minimum Phase Switch Interval` is used as a stability guard for automatic 1P->3P switching. Surplus must remain high for this long before the integration requests 3-phase mode. It also rate-limits repeated 1P->3P attempts. The default is `300` seconds.

`Maximum Phase Switches per Session` limits automatic 1P->3P attempts during one plug-in session. The default is `6`. Returning to 1-phase is treated as a fallback path and may still happen when 3-phase charging is no longer realistic.

Hysteresis prevents rapid switching around the 1P/3P boundary. A larger hysteresis waits longer before switching to 3-phase and switches back to 1-phase sooner when surplus drops. This can be useful with fast-moving clouds. A smaller hysteresis follows surplus more aggressively but may switch more often.

The integration distinguishes between the requested phase-switch mode and the phases that are actually drawing current. `Phase Switch Mode Code` reports register `405`; `Effective Active Phases` is derived from the measured charger currents. PV current calculation uses the effective active phases while charging, because some vehicles or firmware states may report 3-phase mode while still drawing current mostly on one phase.

This distinction is important. Register `405` tells the charger which phase mode was requested. It does not always prove that the vehicle is physically charging on all three phases at that moment. The measured currents are the best practical signal for active phase count while charging.

Automatic PV phase switching uses measured active phases while charging. If register `405` says `3 Phases` but measured current shows the vehicle is still effectively charging on one phase, the integration treats that as a mismatch and can reassert the requested phase mode through the normal pause-and-switch flow.

With `PV Minimum Current = 6 A`, the default thresholds are approximately:

- switch from 1P to 3P at `4640 W` or more surplus
- switch from 3P to 1P between `1380 W` and `3640 W` surplus
- do not switch in the hysteresis band between `3640 W` and `4640 W`

Manual phase switching uses register `405` and is only available when phase switching is not disabled.

Manual phase switching is persistent charger state. If you manually select `1 Phase`, the charger can remain in 1-phase mode for `Normal`, `Fixed Current` and later sessions until you manually select `3 Phases` again or automatic PV phase switching changes it.

Automatic phase switching only runs in `PV` mode or `PV Until Unplug`. It does not run in `Normal`, `Fixed Current` or `Off`.

Automatic 1P->3P switching is deliberately conservative. Surplus must remain high for the configured `Minimum Phase Switch Interval` before the integration requests 3-phase mode. Returning from 3P to 1P is treated as a fallback path and is allowed even when the normal 1P->3P switch budget has been reached.

If surplus drops again while a pending 1P->3P switch is waiting, the pending switch is cancelled. This avoids switching to 3-phase based on a short cloud gap or a brief surplus spike.

The integration performs phase switching conservatively:

1. It writes `0 A` to pause charging.
2. It waits for a later refresh cycle where the charger reports that charging is no longer active.
3. It writes phase-switch register `405`.
4. It resumes charging after the charger reports the requested phase mode.

Register `405` has been validated on one charger with firmware `3.187`. Other firmware versions may behave differently.

Useful phase-switching diagnostics:

- `Effective Active Phases`: phases currently drawing measurable current.
- `PV Surplus Input`: surplus value used by the PV logic.
- `Phase Switch Decision`: current automatic phase-switching decision or block reason.
- `Phase Switch Commands`: number of automatic PV phase-switch commands in the current plug-in session.

## Important entities

Daily-use entities:

- `Charge Mode`: selected base charging mode.
- `Charging On/Off`: user switch for whether charging is allowed.
- `Manual Phase Switch`: manual 1P/3P phase selection when available. The entity is unavailable while charging is active because the charger should be paused before changing phases.
- `PV Until Unplug`: temporary PV session override.
- `Fixed Current Until Unplug`: temporary fixed-current session override.
- `Current Limit`: normal target current.
- `Fixed Current`: target used in fixed-current mode.
- `Active Mode`: effective mode after overrides and runtime behavior.
- `Charging Behavior`: dashboard-friendly status summary.
- `Final Target`: final current target after limits and safety logic.
- `DLB Limit`: current limit calculated by DLB.

Useful diagnostics:

- `Connected`
- `Client Error`
- `Control Reason`
- `Dominant Limit`
- `Sensor Invalid Reason`
- `Write Queue Depth`
- `Phase Switch Mode Code`
- `Effective Active Phases`
- `PV Surplus Input`
- `Phase Switch Decision`
- `Phase Switch Commands`

## Troubleshooting basics

If setup or updates fail:

- Confirm that no other Modbus client is connected to the charger.
- Confirm that the charger IP address is fixed and reachable.
- Confirm that `Modbus/TCP` is enabled in the charger web interface.
- Start with `Charging Control = Off` before enabling active charging control.
- Check `Client Error`, `Connected`, `Sensor Invalid Reason` and `Write Queue Depth`.
