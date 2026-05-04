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
- DLB and Solar control require suitable Home Assistant sensors.

## Settings layout

The integration uses one options screen with the settings grouped in logical sections:

- `Connection`
- `Charging`
- `Temporary Session Settings`
- `Dynamic Load Balancing`
- `Solar Charging`
- `Advanced`

This keeps the full configuration in one place. The validation rules remain the same: invalid sensor combinations or invalid Solar thresholds are still rejected before the options are saved.

## Connection

Main settings:

- `Host`: fixed IP address or host name of the charger.
- `Port`: Modbus TCP port, normally `502`.
- `Unit ID`: Modbus unit ID used by the charger.
- `Polling Interval`: how often the integration refreshes charger state.

## Charging

Main settings:

- `Charger Installation`: installed charger phase configuration, usually `1 Phase` or `3 Phases`.
- `Integration Charging Control`: whether the integration may actively control the charger or stay in monitoring-only mode.
- `Default Mode`: charge mode selected when Home Assistant starts or reloads the integration. The default is `Normal`.
- `Minimum Current`: lowest current the integration may request. EV charging normally starts at `6 A`.
- `Maximum Current`: highest current the integration may request. Set this to match the charger and installation limit.
- `Current Limit`: normal target current for charging.
- `Fallback Current`: fallback current used when the integration cannot safely rely on its sensor inputs.

Recommended first setup:

1. Set `Integration Charging Control` to `Monitoring Only`.
2. Confirm that monitoring, connection state, currents and power values look correct.
3. Switch `Integration Charging Control` to `Enabled` only after the monitored values are plausible.

If you want the integration to return to Solar charging after a Home Assistant restart, set `Default Mode` to the Solar option. The label of that option follows the configured Solar strategy, so it appears as `Eco Solar` or `Smart Solar`. Solar must still be configured with a valid Solar strategy and sensor setup; otherwise startup falls back to `Normal`.

Restart behavior is intentionally split in two parts:

- `Default Mode` is restored from the integration settings.
- `Charging On/Off` is restored from persistent storage.

This means a Home Assistant restart does not automatically resume charging if charging was previously turned off. It also means temporary runtime session settings are not restored after restart.

`Charging On/Off` is only available when `Integration Charging Control` is `Enabled`. In `Monitoring Only` mode the integration keeps the charger alive and monitors it, but it does not write charging-current commands.

## Temporary Session Settings

This section is only shown when `Integration Charging Control` is set to `Enabled`.

Main settings:

- `Fixed Current Until Unplug`: target used when `Fixed Current Until Unplug` is enabled for a session.
- `Solar Until Unplug Mode`: temporary Solar mode used while `Solar Until Unplug` is active for a session.

These settings do not change the configured `Default Mode`. They only define how the temporary session settings behave when those runtime overrides are enabled.

## Current Limits

Important current settings:

- `Minimum Current`: lower control bound. Values below `6 A` normally mean no valid EV charging.
- `Maximum Current`: upper control bound. Increase this if your charger and installation safely support more than the default `16 A`.
- `Current Limit`: normal target current for charging.
- `Fallback Current`: low safety current used when DLB cannot trust its sensor inputs. `6 A` is recommended.
- `Fixed Current`: target used by `Fixed Current` mode.

The final current target can still be limited by the charger-reported session limit, DLB, safety settings or fallback behavior.

`Current Limit` is also a general user limit. This means `Fixed Current` and `Fixed Current Until Unplug` can still be capped by `Current Limit`, `Maximum Current`, DLB and charger/session limits. Example: if `Fixed Current` is `16 A` but `Current Limit` is `10 A`, the final target will not exceed `10 A`.

If DLB input becomes unavailable, the integration falls back to `Fallback Current`. This is intentional safety behavior. A low `Final Target` together with `Fallback Active = True` or a `Sensor Invalid Reason` usually means the integration is limiting charging because it cannot trust the configured sensors.

External DLB sensors must also be recent. If a required phase-current sensor has not been updated within `Control Sensor Timeout`, the integration treats it as unsafe and falls back to `Fallback Current`. This prevents DLB from trusting stale P1 or template-sensor values after a sensor gateway has stopped updating.

## Dynamic Load Balancing

Dynamic Load Balancing (DLB) reduces the charger current when house load gets close to the configured main fuse limit.

DLB is disabled by default. Enable it only after selecting suitable Home Assistant sensors.

Main settings:

- `Sensor Scope`
- `Main Fuse`
- `Safety Margin`
- `L1 Current Sensor`
- `L2 Current Sensor`
- `L3 Current Sensor`

DLB can be enabled or disabled. When enabled, it uses phase current sensors only:

- `1p` charger setup: L1 is required.
- `3p` charger setup: L1, L2 and L3 are required.

During active charging, DLB only requires fresh sensor data for the phases the charger is actually using. For example: a 1-phase car on a 3-phase charger can keep using L1 safely even if L2/L3 are idle and do not update. Before active phases are known, the integration remains conservative and requires all configured phases.

Use live measurement sensors, not energy counters. Current sensors should report `A` or `mA`. Energy sensors such as `Wh` or `kWh` are not suitable for DLB because they represent accumulated energy, not current load.

`Require Sensor Units` is recommended. It makes the integration reject unitless or wrongly typed DLB sensors instead of guessing that a plain number is amperes.

Sensor Scope:

- `Exclude Charger Load`: sensors measure house load without the charger.
- `Include Charger Load`: sensors measure total house load including the charger.

If your sensors include the charger load, select `Include Charger Load`. The integration then compensates for the charger's own measured current before calculating the DLB Limit. This prevents the charger from immediately reducing itself just because its own load appears in the house-current sensors.

If your sensors exclude the charger load, select `Exclude Charger Load`. In that case the integration assumes the house load sensors already represent non-charger load only.

Example:

```text
main fuse = 25 A
safety margin = 2 A
highest measured phase = 18 A including charger
charger measured current = 15 A
available current estimate = 25 - 2 - (18 - 15) = 20 A
```

## Solar Charging

Main settings:

- `Solar Strategy`
- `Solar Input Source`
- `Grid Power Direction`
- `Require Solar Sensor Units`
- `Solar Surplus Sensor`
- `Start Threshold (W)`
- `Stop Threshold (W)`
- `Solar Start Delay`
- `Solar Stop Delay`
- `Solar Minimum Runtime`
- `Solar Minimum Pause`
- `Solar Minimum Current`

Solar Strategy:

- `Disabled`: do not use Solar charging.
- `Eco Solar`: charge only when enough surplus is available.
- `Smart Solar`: keep charging at minimum current and add surplus when available, but pause if the configured Solar input is unavailable.

Solar charging is disabled by default. Enable it only after selecting a suitable surplus or signed grid power sensor.

`Smart Solar` is not pure surplus-only charging. It may charge at `Solar Minimum Current` when there is little or no surplus, as long as the Solar input is valid. Use `Eco Solar` if you want Solar charging to wait until enough surplus is present.

If the configured Solar input becomes unavailable, `Eco Solar` and `Smart Solar` pause by writing `0 A`.
Solar input must also be recent. If the configured Solar sensor is older than `Control Sensor Timeout`, Solar control treats it as unavailable.

Solar input can be provided in two ways:

- `Solar Surplus Sensor`: select a Home Assistant sensor that directly represents available Solar surplus as current power in `W` or `kW`.
- `Signed Grid Power Sensor`: select a live net grid power sensor that reports both import and export using positive and negative values. The integration converts the export side of that sensor into Solar surplus. While the charger is actively charging, the integration adds the charger's measured power back to the export value, so Smart Solar can keep increasing current instead of getting stuck at the minimum current.

`Grid Power Direction` tells the integration which sign means export to the grid:

- `Negative Export`: `-1800 W` means about `1800 W` export. This is the default.
- `Positive Export`: `1800 W` means about `1800 W` export.

Example with `Negative Export`: if the grid sensor reports `-1500 W` while the charger itself is using `1500 W`, the integration treats this as roughly `3000 W` available Solar input.

The surplus sensor must represent power that is available now. Do not select a daily energy production sensor or energy import/export counter.

Do not use separate production and consumption sensors directly unless you first combine them into one surplus sensor. If your smart meter provides separate live production and consumption power sensors, create a helper/template sensor that outputs the actual surplus power.

If your consumption sensor includes the charger, calculate surplus like this:

```text
surplus = Solar production - total consumption + charger power
```

This avoids the common issue where export drops to zero as soon as the charger starts using the available solar power.

Use current power sensors, not energy counters. `W` and `kW` are valid. `Wh` and `kWh` are not suitable for Solar control because they represent accumulated energy, not current surplus.
`Require Solar Sensor Units` is recommended. If it is enabled, unitless Solar sensors are ignored to prevent accidental misconfiguration.

Avoid template sensors that keep updating themselves with old source data. `Control Sensor Timeout` can only protect you when the selected sensor's timestamp becomes stale when the underlying source stops updating.

When Solar power is converted to charging current, the integration uses the charger-reported phase voltages when they are plausible. If voltage data is missing or invalid, it falls back to `230 V` per phase.

Solar thresholds:

- `Start Threshold (W)`: surplus must reach this value before `Eco Solar` starts charging.
- `Stop Threshold (W)`: if surplus stays below this value while charging, the integration may pause after the configured stop delay and minimum runtime.
- `Solar Start Delay`: surplus must stay above the start threshold for this long before charging starts.
- `Solar Stop Delay`: surplus must stay below the stop threshold for this long before charging stops.
- `Solar Minimum Runtime`: once Solar charging starts, keep it running for at least this long.
- `Solar Minimum Pause`: after Solar charging stops, wait this long before starting again.
- `Solar Minimum Current`: lowest current target used when Solar charging is allowed to run.

In `Eco Solar`, the integration also requires enough surplus to support at least `Solar Minimum Current` on the active phase setup. This keeps surplus mode aligned with real surplus charging behavior.

`Solar Minimum Current` is not a Solar maximum. There is no separate Solar maximum-current setting. Solar charging is capped by `Current Limit`, `Maximum Current`, DLB, safety behavior and charger/session limits.

## Solar Until Unplug

`Solar Until Unplug` is a temporary session override.

It does not permanently change the selected base `Charge Mode`. It stays active until the vehicle is unplugged or until you disable the override manually.

The `Solar Until Unplug Mode` can inherit the normal Solar strategy or use a separate Solar strategy for this temporary session.

## Advanced

This section groups lower-level communication settings that most users should leave at their defaults.

Main settings:

- `Keepalive Interval`
- `Control Sensor Timeout`
- `Request Timeout`
- `Retry Attempts`

Keepalive is always enabled by the integration, independent of `Integration Charging Control` being `Enabled` or `Monitoring Only`.
`Control Sensor Timeout` is the maximum age for external DLB and Solar sensors before the integration stops trusting them. The default is `60 seconds`.
These values are mainly useful for charger-specific troubleshooting or communication tuning. They do not change the high-level charging strategy.

Useful diagnostics:

- `Effective Active Phases`: phases currently drawing measurable current.
- `Solar Surplus Input`: surplus value used by the Solar logic.

## Important entities

Daily-use entities:

- `Charge Mode`: selected base charging mode.
- `Charging On/Off`: user switch for whether charging is allowed. Unavailable when `Integration Charging Control` is `Monitoring Only`.
- `Solar Until Unplug`: temporary Solar session override.
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
- `Effective Active Phases`
- `Solar Surplus Input`

## Troubleshooting basics

If setup or updates fail:

- Confirm that no other Modbus client is connected to the charger.
- Confirm that the charger IP address is fixed and reachable.
- Confirm that `Modbus/TCP` is enabled in the charger web interface.
- Start with `Integration Charging Control = Monitoring Only` before enabling active charging control.
- Check `Client Error`, `Connected` and `Sensor Invalid Reason`.
