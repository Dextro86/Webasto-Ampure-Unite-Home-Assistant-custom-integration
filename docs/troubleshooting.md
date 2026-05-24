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

Use `Integration Charging Control = External Controller` when EVCC or another controller should write current targets through `Charging On/Off` and `Maximum Current`. In that mode this integration's own Solar/DLB/fixed-current controller does not write automatic current targets.

Useful diagnostics for this case:

- `Control Owner`
- `Control Writes Enabled`
- `Last Control Write`
- `Last Control Write Reason`
- `Last Control Write Age`
- `Last Control Write Blocked Reason`

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

Manual phase switching is experimental and off by default.

Check:

- `Phase Switching Mode = Manual Only`
- `Integration Charging Control = Enabled`
- charger is connected and available
- vehicle is connected
- `Phase Switch Available`
- `Phase Switch Block Reason`
- `Vehicle Phase Capability`

Requests to 3P are blocked when the current session is observed as `Likely 1P`.
