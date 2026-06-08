# Configuration guide

This guide explains the main settings for the Webasto/Ampure Unite Home Assistant custom integration.

Start conservatively. First confirm that monitoring works and that the charger values look correct. Only then enable active charging control.

## Requirements

Before using the integration:

- Home Assistant is running.
- HACS is installed if you use the HACS installation method.
- The charger has network connectivity and a fixed IP address.
- `Modbus/TCP` is enabled in the charger's web interface.
- The Modbus/TCP port is normally `502`.
- The Webasto/Ampure Unite Modbus unit ID is often `255`.
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
- `Unit ID`: Modbus unit ID used by the charger, often `255` on Webasto/Ampure Unite chargers.
- `Polling Interval (s)`: how often the integration refreshes charger state.

If monitoring is unreliable, first check that no other tool, automation or integration keeps a Modbus connection open to the charger. Typical symptoms are intermittent unavailable sensors, `Client Error` changes or updates that work only after restarting another Modbus client.

## Charging

Main settings:

- `Charger Configuration`: charger configuration for the integration, either `1P` or `3P`. Choose `3P` for a three-phase charger setup, even when some connected vehicles only charge on one phase.
- `Integration Charging Control`: who may actively control charging current: this integration, an external controller such as EVCC, or monitoring-only.
- `Default Mode`: charge mode selected when Home Assistant starts or reloads the integration. The default is `Normal`.
- `Minimum Current (A)`: lowest current the integration may request. EV charging normally starts at `6 A`.
- `Maximum Current (A)`: normal target current in `Normal` mode and the highest current the integration may request. Set this to match the charger and installation limit.
- `Fallback Current (A)`: fallback current used when the integration cannot safely rely on its sensor inputs.

Recommended first setup:

1. Set `Integration Charging Control` to `Monitoring Only`.
2. Confirm that monitoring, connection state, currents and power values look correct.
3. Switch `Integration Charging Control` to `Enabled` only after the monitored values are plausible, or choose `External Controller` when EVCC should manage charging current.

If you want the integration to return to Solar charging after a Home Assistant restart, set `Default Mode` to `Solar` and configure `Default Solar Mode`. During normal use, the `Charge Mode` entity lets you choose `Eco Solar`, `Smart Solar` or `Solar Boost` directly. Solar must still be configured with a valid Solar mode and sensor setup; otherwise startup falls back to `Normal`.

Restart behavior is intentionally split in two parts:

- `Default Mode` is restored from the integration settings.
- `Charging On/Off` is restored from persistent storage.

This means a Home Assistant restart does not automatically resume charging if charging was previously turned off. It also means temporary runtime session settings are not restored after restart.

`Charging On/Off` is available when `Integration Charging Control` is `Enabled` or `External Controller`. In `Monitoring Only` mode the integration keeps the charger alive and monitors it, but it does not write charging-current commands.

When `Integration Charging Control` is `Monitoring Only`, `Charging Behavior` shows `Monitoring Only - Not Writing`. `Final Target` may still show the current the integration would choose, but that value is diagnostic only and is not written to the charger.

When `Integration Charging Control` is `External Controller`, this integration's own Solar/DLB/fixed-current controller does not write automatic targets. `Charging On/Off`, `Pause Charging`, `Resume Charging` and `External Requested Current` remain writable so EVCC or another controller can control the charger through Home Assistant. `Maximum Current` remains the configured upper limit.

`Pause Charging` and `Resume Charging` buttons are also available when `Integration Charging Control` is `Enabled` or `External Controller`. They are convenience controls for the same charging-enabled state:

- `Pause Charging`: disables charging and lets the integration write `0 A`.
- `Resume Charging`: enables charging again and lets the active mode calculate the next current target.

The Unite does not expose a known separate session-stop command in this integration. A charging session normally ends when the vehicle is unplugged or when the charger itself ends it.

## Temporary Session Settings

This section is only shown when `Integration Charging Control` is set to `Enabled`.

Main settings:

- `Fixed Current Until Unplug (A)`: target used when `Fixed Current Until Unplug` is enabled for a session.
- `Solar Until Unplug Mode`: temporary Solar mode used while `Solar Until Unplug` is active for a session.

These settings do not change the configured `Default Mode`. They only define how the temporary session settings behave when those runtime overrides are enabled.

## Current Limits

Important current settings:

- `Minimum Current (A)`: lower control bound. Values below `6 A` normally mean no valid EV charging.
- `Maximum Current (A)`: normal target current in `Normal` mode and upper control bound. Increase this if your charger and installation safely support more than the default `16 A`.
- `Fallback Current (A)`: low safety current used when DLB cannot trust its sensor inputs. `6 A` is recommended.
- `Fixed Current`: target current in amperes used by `Fixed Current` mode.

The final current target can still be limited by the charger-reported session limit, DLB, safety settings or fallback behavior.

`Maximum Current (A)` is the single configured upper limit. `Normal` mode targets this value directly. `Fixed Current`, `Fixed Current Until Unplug` and Solar can still be capped by `Maximum Current (A)`, DLB and charger/session limits.

The legacy/config `Maximum Current` number entity is disabled by default so it does not appear as a normal dashboard control. Change this value through the integration settings unless you intentionally need the service/entity for automation compatibility.

If DLB input becomes unavailable, the integration falls back to `Fallback Current (A)`. This is intentional safety behavior. A low `Final Target` together with `Fallback Active = True` or a `Sensor Invalid Reason` usually means the integration is limiting charging because it cannot trust the configured sensors.

External DLB sensors must also be recent. If a required phase-current sensor has not been updated within `Control Sensor Timeout (s)`, the integration treats it as unsafe and falls back to `Fallback Current (A)`. This prevents DLB from trusting stale P1 or template-sensor values after a sensor gateway has stopped updating.

## Dynamic Load Balancing

Dynamic Load Balancing (DLB) reduces the charger current when house load gets close to the configured main fuse limit.

DLB is disabled by default. Enable it only after selecting suitable Home Assistant sensors.

Main settings:

- `Sensor Scope`
- `Main Fuse (A)`
- `Safety Margin (A)`
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

- `Default Solar Mode`
- `Solar Input Source`
- `Grid Power Direction`
- `Solar Sensor Failure Behavior`
- `Require Solar Sensor Units`
- `Solar Surplus Sensor`
- `Grid Power Sensor`
- `Import Power Sensor`
- `Export Power Sensor`
- `Start Threshold (W)`
- `Stop Threshold (W)`
- `Solar Start Delay (s)`
- `Solar Stop Delay (s)`
- `Solar Minimum Runtime (s)`
- `Solar Minimum Pause (s)`
- `Solar Minimum Current (A)`

Default Solar Mode:

- `Disabled`: do not use Solar charging.
- `Eco Solar`: charge only when enough surplus is available.
- `Smart Solar`: charge at least at `Solar Minimum Current (A)` when Solar input is valid, and increase only when the total Solar input supports more than that minimum.
- `Solar Boost`: charge at `Solar Minimum Current (A)` and add available Solar surplus on top.

Solar charging is disabled by default. Enable it only after selecting suitable Solar input sensors. `Default Solar Mode` is the Solar mode used after restart and when a generic Solar service/mode is selected. The runtime `Charge Mode` selector can still choose `Eco Solar`, `Smart Solar` or `Solar Boost` directly.

`Smart Solar` is not pure surplus-only charging. It may charge at `Solar Minimum Current (A)` when there is little or no surplus, as long as the Solar input is valid. It raises current when the calculated Solar input supports more than the minimum. Use `Eco Solar` if you want Solar charging to wait until enough surplus is present.

Smart Solar target calculation:

```text
target current = max(Solar Minimum Current (A), Solar Surplus Input / active phase voltage sum)
```

Solar Boost target calculation:

```text
target current = Solar Minimum Current (A) + Solar Surplus Input / active phase voltage sum
```

Solar Boost examples:

```text
1p, Solar Minimum Current = 6 A, Solar Surplus Input = 2300 W
target = 6 + (2300 / 230) = about 16 A

3p, Solar Minimum Current = 6 A, Solar Surplus Input = 3000 W
target = 6 + (3000 / (230 * 3)) = about 10.3 A per phase
```

The final current can still be capped by `Maximum Current (A)`, DLB, safety behavior and charger/session limits.

Solar control is intentionally conservative. By default the integration smooths Solar input over about 12 seconds, ignores import/export changes smaller than about 150 W around zero and ramps Solar current increases by about 2 A per control step. This reduces current bouncing from short clouds, P1 timing differences and sensor jitter while still reacting reasonably quickly. DLB and safety limits can still reduce the current immediately.

Solar diagnostic sensors expose the calculation path:

- `Solar Raw Input`: interpreted Solar input before deadband and smoothing.
- `Solar Surplus Input`: Solar input after deadband, before smoothing.
- `Solar Filtered Input`: Solar input after smoothing.
- `Solar Target`: Solar current target before DLB, maximum current and charger/session limits.
- `Solar Phase Count`, `Solar Phase Source` and `Solar Voltage Sum`: phase and voltage basis used for the Solar current calculation.

The integration also exposes `IEC 61851 State` as a derived diagnostic sensor for compatibility with external tools such as EVCC:

- `A`: no vehicle connected
- `B`: vehicle connected, not charging
- `C`: charging
- `E`: fault
- `F`: unavailable
- `Unknown`: state could not be derived

This sensor is derived from the charger's Modbus charge point, charging and cable states. It is not a raw IEC61851 register.

The integration also exposes `EVCC Status` as a diagnostic compatibility sensor. Its state and raw attributes are stable machine values for EVCC or automations. Attributes ending in `_label` are meant for human-readable dashboards and support.

Solar Sensor Failure Behavior:

- `Pause charging`: recommended default. `Smart Solar` and `Solar Boost` pause by writing `0 A` when Solar input is stale, unavailable or invalid.
- `Continue at Solar Minimum Current`: `Smart Solar` and `Solar Boost` keep charging at `Solar Minimum Current (A)` when Solar input cannot be trusted. This may use grid power.

`Eco Solar` always pauses on Solar input failure because it is surplus-only charging.
Solar input must also be recent. If the configured Solar sensor is older than `Control Sensor Timeout (s)`, Solar control treats it as unavailable.

Solar input can be provided in three ways:

- `Solar Surplus Sensor`: select a Home Assistant sensor that directly represents available Solar surplus as current power in `W` or `kW`.
- `Signed Grid Power Sensor`: select a live net grid power sensor that reports both import and export using positive and negative values. The integration converts the grid value into Solar input and compensates for charger power while the charger is already charging.
- `DSMR Import/Export Sensors`: select separate P1/DSMR import and export power sensors. The integration calculates signed grid power internally as `import - export`, so no Home Assistant template helper is needed.

For Dutch/Belgian DSMR/P1 meters with separate sensors such as current consumption and current production/return delivery, `DSMR Import/Export Sensors` is usually the clearest option.

How `DSMR Import/Export Sensors` are interpreted:

```text
import sensor = 500 W, export sensor = 0 W     -> signed grid power +500 W
import sensor = 0 W, export sensor = 3000 W    -> signed grid power -3000 W
import sensor = 0 W, export sensor = 0 W       -> signed grid power 0 W
```

The same charger-power correction is then applied as with `Signed Grid Power Sensor`.

For DSMR import/export sensors, a stale zero value is accepted as `0 W`. This is intentional because one direction often stays at zero for a long time. A stale positive import or export value is still rejected as unsafe.

`Grid Power Direction` is ignored for `DSMR Import/Export Sensors`; the integration always treats `import - export` as signed grid power.

`Grid Power Direction` tells the integration which sign means export to the grid:

- `Negative Export`: `-1800 W` means about `1800 W` export. This is the default.
- `Positive Export`: `1800 W` means about `1800 W` export.

Choose this by looking at the grid power sensor while the house is exporting and the car is not charging:

- If export is shown as a negative number, use `Negative Export`.
- If export is shown as a positive number, use `Positive Export`.

How `Signed Grid Power Sensor` is interpreted:

With `Negative Export`:

- Sensor `-3000 W`, charger `0 W` -> Solar input is `3000 W`.
- Sensor `-1500 W`, charger `1500 W` -> Solar input is `3000 W`.
- Sensor `+1000 W`, charger `1500 W` -> Solar input is `500 W`.
- Sensor `+2000 W`, charger `1500 W` -> Solar input is `0 W`.

With `Positive Export`:

- Sensor `+3000 W`, charger `0 W` -> Solar input is `3000 W`.
- Sensor `+1500 W`, charger `1500 W` -> Solar input is `3000 W`.
- Sensor `-1000 W`, charger `1500 W` -> Solar input is `500 W`.
- Sensor `-2000 W`, charger `1500 W` -> Solar input is `0 W`.

The charger-power correction is important. Without it, export can drop as soon as charging starts, making Smart Solar add too little surplus current even though there is still Solar power available.

The surplus sensor must represent power that is available now. Do not select a daily energy production sensor or energy import/export counter.

Do not use separate production and consumption sensors directly unless you first combine them into one surplus sensor. If your smart meter provides separate live production and consumption power sensors, create a helper/template sensor that outputs the actual surplus power.

If your consumption sensor includes the charger, calculate surplus like this:

```text
surplus = Solar production - total consumption + charger power
```

This avoids the common issue where export drops to zero as soon as the charger starts using the available solar power.

Use current power sensors, not energy counters. `W` and `kW` are valid. `Wh` and `kWh` are not suitable for Solar control because they represent accumulated energy, not current surplus.
`Require Solar Sensor Units` is recommended. If it is enabled, unitless Solar sensors are ignored to prevent accidental misconfiguration.

Avoid template sensors that keep updating themselves with old source data. `Control Sensor Timeout (s)` can only protect you when the selected sensor's timestamp becomes stale when the underlying source stops updating.

When Solar power is converted to charging current, the integration uses the charger-reported phase voltages when they are plausible. If voltage data is missing or invalid, it falls back to `230 V` per phase.

Solar thresholds:

- `Start Threshold (W)`: surplus must reach this value before `Eco Solar` starts charging.
- `Stop Threshold (W)`: if surplus stays below this value while charging, the integration may pause after the configured stop delay and minimum runtime.
- `Solar Start Delay (s)`: surplus must stay above the start threshold for this many seconds before charging starts.
- `Solar Stop Delay (s)`: surplus must stay below the stop threshold for this many seconds before charging stops.
- `Solar Minimum Runtime (s)`: once Solar charging starts, keep it running for at least this many seconds.
- `Solar Minimum Pause (s)`: after Solar charging stops, wait this many seconds before starting again.
- `Solar Minimum Current (A)`: lowest current target used when Solar charging is allowed to run.

In `Eco Solar`, the integration also requires enough surplus to support at least `Solar Minimum Current (A)` on the active phase setup. This keeps surplus mode aligned with real surplus charging behavior.

`Solar Minimum Current (A)` is not a Solar maximum. There is no separate Solar maximum-current setting. Solar charging is capped by `Maximum Current (A)`, DLB, safety behavior and charger/session limits.

## Solar Until Unplug

`Solar Until Unplug` is a temporary session override.

It does not permanently change the selected base `Charge Mode`. It stays active until the vehicle is unplugged or until you disable the override manually.

The `Solar Until Unplug Mode` can inherit the active/default Solar mode or use a separate Solar mode for this temporary session.

## Advanced

This section groups lower-level communication settings that most users should leave at their defaults.

Main settings:

- `Keepalive Interval (s)`
- `Control Sensor Timeout (s)`
- `Request Timeout (s)`
- `Retry Attempts`

Keepalive is always enabled by the integration, independent of `Integration Charging Control` being `Enabled` or `Monitoring Only`.
`Control Sensor Timeout (s)` is the maximum age for external DLB and Solar sensors before the integration stops trusting them. The default is `60 seconds`.
These values are mainly useful for charger-specific troubleshooting or communication tuning. They do not change the high-level charging strategy.

Useful diagnostics:

- `Effective Active Phases`: phases currently drawing measurable current.
- `Solar Surplus Input`: surplus value used by the Solar logic.

## Phase Switching

Phase switching is experimental and off by default. `Manual Only` exposes explicit buttons, services and the phase select. `Automatic Solar` lets this integration request 1P/3P switches only while it owns Solar control.

What this means:

- Phase switching is off by default.
- Manual switching is exposed through explicit buttons/services: `request_phase_1p`, `request_phase_3p` and `reset_phase_switch_state`.
- The `Phase Switch` select exposes EVCC-compatible options `1` and `3` and uses the same safe phase-switch sequence.
- The button/service uses the same internal pause/resume semantics as `Pause Charging` and `Resume Charging`, waits until the charger actually appears paused, writes register `405`, verifies that register `405` holds the requested value, resumes charging if the charger was already charging, and then observes the measured active phases for a longer window.
- `Restore Default Phase Mode` writes the configured `Charger Configuration` (`1P` or `3P`) back to register `405`. This can run without a connected vehicle.
- A manual switch away from `Charger Configuration` is treated as a temporary session override. When the vehicle is unplugged, the integration tries to restore `405` back to `Charger Configuration`.
- Existing custom dashboard cards or automations that call old phase-switch services should be removed or disabled.
- The integration still detects `Effective Active Phases` from measured charger current. DLB and Solar use that observation to make safer current decisions for 1-phase and 3-phase charging sessions.
- The integration reads charger phase diagnostics:
  - Register `404`: charger-reported phase register, shown as `Charger Phase Register 404`. This is diagnostic only. Field testing showed it can report `1P` while the charger is physically charging on 3 phases, so it is not used as a hard phase-switch capability block.
  - Register `405`: experimental phase-switch mode register, shown as `Phase Switch Mode Raw` and `Phase Switch Mode`.
  - Known historical write values for register `405` are `0 = 1P` and `1 = 3P`.
- `Observed Session Phase Usage` is observed from measured phase currents during active charging and can be `Observed 1P`, `Observed 3P` or `Unknown`. This is diagnostic only and is not a vehicle capability claim.
- `Phase Switch Available` and `Phase Switch Block Reason` indicate whether the basic preconditions appear suitable for manual switching.
- `Phase Switch State` shows the current step, for example `Pausing`, `Waiting For Pause`, `Writing Phase Register`, `Verifying Phase Register`, `Waiting Before Resume`, `Retrying Phase Switch`, `Retry Pausing`, `Retry Writing Phase Register`, `Retry Waiting Before Resume`, `Retry Resuming`, `Observing Physical Phases`, `Register Verified`, `Physical Verified`, `Physical Timeout`, `Register Reverted` or `Pause Not Confirmed`.
- `Last Phase Switch Result = Register Verified` means only register `405` confirmed the requested value. `Physical Verified` means measured active phases also matched the request after charging resumed. `Pause Not Confirmed` means charging did not actually drop low enough after the pause request, so the integration did not write the phase register. `Vehicle Did Not Resume` means charging did not restart after two full bounded phase-switch sequences. `Physical Timeout` means charging did resume, but the active session did not move to the requested phase count after two full bounded phase-switch sequences. `Register Reverted` means register `405` fell back away from the requested value.
- `Phase Policy Decision` and the `Phase Policy Auto ...` sensors show whether Solar automatic phase switching is ready after stable phase-target timing, cooldown and session-count guards. They write register `405` only when `Phase Switching Mode = Automatic Solar` and `Integration Charging Control = Enabled`.
- Automatic Solar requires the same phase target to remain stable before switching: 120 seconds for 3P -> 1P and 600 seconds for 1P -> 3P.
- Automatic Solar uses a 10 minute cooldown after a switch, limits automatic switching to 5 switches per connected session and requires about 300 W above the calculated 3P minimum before switching from 1P to 3P.
- In `Eco Solar`, 3P -> 1P is requested only when surplus can support at least the 1P minimum. In `Smart Solar` and `Solar Boost`, 3P -> 1P can also be requested below the 1P minimum because these modes intentionally allow baseline charging.

Manual switch requests are blocked when:

- `Phase Switching Mode` is `Off`.
- `Integration Charging Control` is `Monitoring Only`.
- The charger is unavailable.
- No vehicle is connected.
- The phase-switch register `405` cannot be read.
- The integration itself is configured as `1P`.

`Restore Default Phase Mode` is the exception to the vehicle-connected requirement. It is intended to put register `405` back to the configured `Charger Configuration` after manual testing or a future temporary phase session.

If automatic restore fails, `Phase Restore Pending` remains active in diagnostics.

After a Home Assistant restart or integration reload, the integration compares register `405` with `Charger Configuration`. If no vehicle is connected and register `404` confirms the charger is 3P-capable when needed, it restores `405` to `Charger Configuration`. If a vehicle is connected, it does not switch blindly and only marks `Phase Restore Pending`.

The charger may still have its own physical or firmware-level phase configuration. Treat manual phase switching as experimental and verify behavior on your own charger before using it in automations.

## EVCC via Home Assistant

EVCC can use this charger through Home Assistant entities. This integration then acts as the Home Assistant bridge to the Webasto/Ampure Modbus connection.

Recommended setup when EVCC is the active charging manager:

- Set `Integration Charging Control` to `External Controller`.
- Set `Default Mode` to `Normal`.
- Keep this integration's Solar and DLB control disabled unless you intentionally want an additional local safety cap.
- Do not run EVCC Solar control and this integration's Solar control at the same time.
- Do not run EVCC Solar control and this integration's Automatic Solar phase switching at the same time. In `External Controller` mode, EVCC may request phase switches through the `Phase Switch` select, but this integration's own Automatic Solar policy does not run.

In `External Controller` mode the integration still reads the charger, sends keepalive and exposes Home Assistant control entities. The integration's own Solar/DLB/fixed-current controller does not write automatic current targets. EVCC can write through `Charging On/Off` and `External Requested Current`.

Relevant entities:

- `IEC 61851 State`: charger status for EVCC (`A`, `B`, `C`, `E`, `F`).
- `Charging On/Off`: enable/disable switch.
- `External Requested Current`: number entity for EVCC `setMaxCurrent`.
- `Maximum Current`: configured upper current limit. EVCC requests above this value are rejected.
- `Active Power`: measured charger power.
- `Current L1`, `Current L2`, `Current L3`: phase currents.
- `EVCC Status`: diagnostic support sensor with stable attributes.

EVCC may send fractional current values such as `6.82 A`. The integration accepts those on `External Requested Current` and the `set_current` service, then rounds to the nearest whole ampere before writing to the Webasto/Ampure current register. The charger itself is controlled in whole amperes.

Example `evcc.yaml` charger section. Replace every entity ID with the actual entity ID from your Home Assistant instance:

```yaml
chargers:
  - name: webasto_unite_ha
    type: template
    template: homeassistant
    uri: http://homeassistant.local:8123
    token: ${HA_TOKEN}
    status: sensor.webasto_unite_iec_61851_state
    enabled: switch.webasto_unite_charging_allowed
    enable: switch.webasto_unite_charging_allowed
    setMaxCurrent: number.webasto_unite_requested_current
    power: sensor.webasto_unite_active_power
    energy: sensor.webasto_unite_energy_meter
    currentL1: sensor.webasto_unite_current_l1
    currentL2: sensor.webasto_unite_current_l2
    currentL3: sensor.webasto_unite_current_l3
    voltageL1: sensor.webasto_unite_voltage_l1
    voltageL2: sensor.webasto_unite_voltage_l2
    voltageL3: sensor.webasto_unite_voltage_l3
    phaseswitch: select.webasto_unite_phase_switch
```

If EVCC cannot enable charging, verify that `Charging On/Off` is available in Home Assistant. It is unavailable when `Integration Charging Control` is `Monitoring Only`.

Existing Home Assistant installations can have different entity IDs because Home Assistant preserves entity registry names. Always check the actual entity IDs before copying the EVCC example.

Configure EVCC `phaseswitch` only if you want EVCC to control 1P/3P switching. Use the `Phase Switch` select and verify the actual entity ID in Home Assistant.

## Important entities

Daily-use entities:

- `Charge Mode`: selected base charging mode.
- `Charging On/Off`: user switch for whether charging is allowed. Unavailable when `Integration Charging Control` is `Monitoring Only`.
- `Pause Charging`: one-shot button to disable charging through the existing current-control flow.
- `Resume Charging`: one-shot button to enable charging through the existing current-control flow.
- `Solar Until Unplug`: temporary Solar session override.
- `Fixed Current Until Unplug`: temporary fixed-current session override.
- `Maximum Current (A)`: normal target current and configured upper limit.
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
- `EVCC Status`

## Troubleshooting basics

If setup or updates fail:

- Confirm that no other Modbus client is connected to the charger.
- Confirm that the charger IP address is fixed and reachable.
- Confirm that `Modbus/TCP` is enabled in the charger web interface.
- Confirm that the Modbus port is `502` unless you intentionally changed it.
- Try unit ID `255` if you are unsure which unit ID the charger uses.
- Start with `Integration Charging Control = Monitoring Only` before enabling active charging control.
- Check `Client Error`, `Connected` and `Sensor Invalid Reason`.

If charging is unexpectedly limited:

- Check `Final Target` to see what current the integration is requesting.
- Check `Control Reason` to see which mode or limiter made that decision.
- Check `Fallback Active` and `Sensor Invalid Reason` to see whether the integration distrusts a DLB or Solar sensor.
- Check `DLB Limit` if Dynamic Load Balancing is enabled.
- Check `Solar Input State` and `Solar Surplus Input` if Solar charging is enabled.
- Check whether P1, grid power or template sensors are still updating. A sensor can show a plausible old value while its timestamp is stale.

If Solar input looks wrong:

- For `Solar Surplus Sensor`, confirm that the selected sensor already reports available surplus as positive power.
- For `Signed Grid Power Sensor`, confirm that `Grid Power Direction` matches your sensor while exporting and not charging.
- Use power sensors in `W` or `kW`, not daily energy sensors in `Wh` or `kWh`.
