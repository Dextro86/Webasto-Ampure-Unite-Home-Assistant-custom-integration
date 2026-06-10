# Troubleshooting

## Charger Unavailable

Check:

- charger IP address
- Home Assistant network access to the charger
- Modbus/TCP enabled in the charger web interface
- TCP port `502`
- configured Unit ID, usually `255`
- whether another Modbus master is already connected

The Unite appears to behave best with only one active Modbus/TCP master connection.

## Solar Charging Does Not Start

Check:

- `Solar Control Strategy`
- `Solar Input Source`
- selected Solar sensor entities
- `Solar Input State`
- `Solar Raw Input`
- `Solar Filtered Input`
- `Solar Surplus Input`
- `Solar Start Threshold`
- `Solar Start Delay`
- `Solar Minimum Pause`
- `DLB Limit`

If `Solar Input State` is stale or unavailable, check the selected Home Assistant sensor itself.

## Solar Shows 0 W While There Is Production

This often happens when a sensor reports production/export separately, but the integration is configured as if the sensor reports surplus.

For P1/DSMR setups with separate production and consumption sensors, use `DSMR Import/Export Sensors`.

For one signed sensor, use `Signed Grid Power Sensor` and set `Grid Power Direction` correctly.

## Unexpected Current Reduction

Check:

- `Control Reason`
- `Dominant Limit`
- `DLB Limit`
- `Fallback Active`
- `Sensor Invalid Reason`
- configured `Fallback Current`
- configured `Main Fuse`
- configured `Safety Margin`

If a required DLB sensor stops updating, the integration falls back instead of trusting stale values.

## Final Target Is Higher Than Reported Current Limit

If `Charging Behavior` is `Monitoring Only - Not Writing`, this is expected.

In Monitoring Only mode the integration still calculates `Final Target` for diagnostics, but it does not write that target to the charger. The charger keeps its own current limit, often `6 A` if that was already configured or if the charger is in its own fallback behavior.

Set `Integration Charging Control` to `Enabled` if you want the integration to write the calculated current target to the charger.

Use `Integration Charging Control = External Controller` when EVCC or another controller should write current targets through `Charging On/Off`, `External Requested Current` or the `set_current` service. In that mode this integration's own Solar/DLB/fixed-current controller does not write automatic current targets.

Useful diagnostics for this case:

- `Control Owner`
- `Control Writes Enabled`
- `Last Control Write`
- `Last Control Write Reason`
- `Last Control Write Age`
- `Last Control Write Blocked Reason`

If `Last Control Write` has attribute `verification_status = mismatch`, the integration wrote a current target to register `5004`, but the charger did not report that current back within the verification window. Compare `Final Target`, `Reported Current Limit`, `Session Max Current`, cable limit and EVSE/vehicle limits.

## Charging Stops On Solar Sensor Failure

This is expected for `Eco Solar`.

For `Smart Solar` and `Solar Boost`, behavior depends on `Solar Sensor Failure Behavior`:

- `Pause charging`: safest default.
- `Continue at Solar Minimum Current`: keeps charging at Solar Minimum Current and may use grid power.

## Frequent Reconnects

Check:

- Wi-Fi or Ethernet stability
- charger IP address stability
- firewall or VLAN rules
- whether another Modbus client is connected
- `Client Error`
- debug logs

## Energy Dashboard

Use an energy sensor, not active power.

Recommended:

- `Energy Meter`
- `Session Energy` for session-specific information

Do not use `Active Power` directly as an energy source.

## Manual Phase Switching Blocked

Phase switching is experimental and off by default.

Check:

- `Phase Switching Mode` is `Manual Only` or `Automatic Solar`
- `Integration Charging Control` is `Enabled` or `External Controller`
- charger is connected and available
- vehicle is connected
- `Requested Phase`
- `Observed Phase`
- `Phase Recovery State`

Measured active phases are diagnostic only. A 1P vehicle on a 3P charger is normal and is not treated as a vehicle capability claim.

If `Phase Recovery State` has attribute `switch_block_reason = Charger Preconfigured 1P`, register `404` reports that the charger itself is configured as 1P. In that case the integration blocks 1P/3P switching.

Use `Restore Default Phase Mode` if register `405` was manually changed and you want to return to the configured `Charger Configuration`.

If `Last Phase Switch Result` says `Pause Not Confirmed`, the integration wrote `0 A` but the charger kept drawing current. In that case the integration intentionally did not write register `405`.

If `Last Phase Switch Result` says `Vehicle Did Not Resume`, register `405` accepted and held the requested value but the vehicle did not start charging again after two full bounded phase-switch sequences. In practice this usually means the car needs a physical reconnect or a longer CP/session reset than the integration can safely do automatically.

If `Last Phase Switch Result` says `Physical Timeout`, register `405` accepted and held the requested value and charging did resume, but the active charging session did not physically move to the requested phase count after two full bounded phase-switch sequences. This is useful test information; do not enable Automatic Solar phase switching for that charger/vehicle combination until manual switching is reliable.

If `Last Phase Switch Result` says `Register Reverted`, the charger accepted register `405` briefly but later reported a different value again.

If `Observed Phase` is `1P` while `Requested Phase` is `3P`, the integration does not know whether the connected vehicle is 1P-only or whether the charger/session is stuck on 1P. In Automatic Solar mode it may try one bounded 3P recovery only when 3P is clearly intended for this session. If recovery fails, it keeps charging and exposes the warning on `Phase Recovery State`.

If `Phase Recovery State` or the `Requested Phase` attributes show `restore_pending` after unplug, check the charger connection and the last block reason attributes, then use `Restore Default Phase Mode` manually.

After a restart, `restore_pending` can also mean register `405` differs from `Charger Configuration` while a vehicle is already connected. The integration avoids treating the first read after startup as a fresh plug-in event. A real plug-in event while Home Assistant is running can trigger one bounded 3P start normalization on 3P installations when phase switching is enabled.
