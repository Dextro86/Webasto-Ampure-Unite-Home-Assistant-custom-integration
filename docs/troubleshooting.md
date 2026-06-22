# Troubleshooting

When behavior is unclear, first compare it with the [Behavior contract](behavior_contract.md). The integration intentionally does not write phase changes during plug/unplug. Automatic Solar phase switching only writes after stable-target, cooldown and session-count guards pass.

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
- configured `Integration Fallback Current`
- configured `Main Fuse`
- configured `Safety Margin`

If a required DLB sensor stops updating, the integration falls back instead of trusting stale values.

## Final Target Is Higher Than Reported Current Limit

If `Charging Behavior` is `Monitoring Only - Not Writing`, this is expected.

In Monitoring Only mode the integration still calculates `Final Target` for diagnostics, but it does not write that target to the charger. The charger keeps its own current limit, often `6 A` if that was already configured or if the charger is in its own fallback behavior.

Set `Integration Charging Control` to `Enabled` if you want the integration to write the calculated current target to the charger.

Use `Integration Charging Control = External Controller` when EVCC or another controller should write current targets through `Charging Enabled`, `External Requested Current` or the `set_current` service. In that mode this integration's own Solar/DLB/fixed-current controller does not write automatic current targets.

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
- `Phase Switch State`

Measured active phases are diagnostic only. A 1P vehicle on a 3P charger is normal and is not treated as a vehicle capability claim.

If `Phase Switch State` has attribute `switch_block_reason = Charger Preconfigured 1P`, register `404` reports that the charger itself is configured as 1P. Treat this as diagnostic context; phase switching depends on the explicit register `405` path.

Use `Restore Configured Phase` if register `405` was manually changed and you want to return to the configured `Charger Configuration`.

If the `last_result` attribute on `Phase Switch State` says `Phase Register Written`, the integration wrote register `405`. This is intentionally not the same as physical verification. Check `Requested Phase` and `Observed Phase` together to see whether the active charging session actually followed the request.

If `Observed Phase` is `1P` while `Requested Phase` is `3P`, the integration does not know whether the connected vehicle is 1P-only or whether the charger/session is stuck on 1P. It reports the mismatch and keeps charging; it does not start automatic recovery.

After unplug the integration clears its own runtime/session state, resets the runtime charge mode to `Default Mode` and schedules a short delayed Modbus reconnect. It intentionally does not write register `405` while the charger is closing the session. In a new settled non-Solar managed session, it may restore configured 3P if register `405` is still 1P.

After a restart, phase diagnostics can show that register `405` differs from `Charger Configuration`. The integration avoids treating the first read after startup as a fresh plug-in event and does not automatically write register `405`.
