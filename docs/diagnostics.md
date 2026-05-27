# Diagnostics

The integration exposes diagnostic entities to make charger behavior, Solar control, DLB and EVCC compatibility easier to debug.

## Core Diagnostics

Useful entities:

- `Connected`
- `Client Error`
- `Reconnect`
- `Refresh`
- `Control Reason`
- `Control Writes Enabled`
- `Control Owner`
- `Last Control Write`
- `Last Control Write Reason`
- `Last Control Write Register`
- `Last Control Write Age`
- `Last Control Write Blocked Reason`
- `Dominant Limit`
- `Final Target`
- `Reported Current Limit`
- `Fallback Active`
- `Sensor Invalid Reason`
- `Keepalive Overdue`

## Charger State

Useful entities:

- `IEC 61851 State`
- `Charge Point State`
- `Charging State`
- `Equipment State`
- `Cable State`
- `EVSE Fault Code`
- `Vehicle Connected`
- `Charging Active`

## Solar Diagnostics

Useful entities:

- `Solar Input State`
- `Solar Surplus Input`
- `Solar Raw Input`
- `Solar Filtered Input`
- `Solar Target`
- `Solar Phase Count`
- `Solar Phase Source`
- `Solar Voltage Sum`

Use these to check whether Solar input is available, stale, filtered, or limited by phase assumptions.

## Write Diagnostics

Use these entities when `Final Target` and `Reported Current Limit` do not match:

- `Control Owner`: high-level source currently responsible for charging-current control, for example `Integration`, `External Controller`, `Solar`, `DLB`, `Fallback`, `Manual Pause` or `Monitoring Only`.
- `Control Writes Enabled`: whether charging-current writes through this integration are available. This is true in `Enabled` and `External Controller` mode.
- `Last Control Write`: last current value written to register `5004`.
- `Last Control Write Reason`: why that write was made, for example Fixed Current, Solar, DLB or fallback.
- `Last Control Write Register`: register used for the write.
- `Last Control Write Age`: how long ago the last current write happened.
- `Last Control Write Blocked Reason`: why a calculated write was not sent, for example `Monitoring Only` or `External Controller Mode`.

In `Monitoring Only`, the integration may still calculate `Final Target`, but `Control Writes Enabled` is false and writes are not sent to the charger.

In `External Controller` mode, the integration's own automatic controller also does not write calculated targets. External writes through `Charging On/Off`, `Requested Current` or the `set_current` service are still allowed and are recorded as `External Controller`.

## DLB Diagnostics

Useful entities:

- `DLB Limit`
- `Dominant Limit`
- `Fallback Active`
- `Sensor Invalid Reason`
- `Final Target`

If DLB unexpectedly reduces charging, first check `Sensor Invalid Reason` and `DLB Limit`.

## EVCC Diagnostics

Useful entities:

- `IEC 61851 State`
- `EVCC Status`
- `Effective Active Phases`
- `Actual Phase Current`
- `Active Power`

`EVCC Status` exposes machine-oriented attributes for compatibility and support.

## Phase Diagnostics

Automatic phase switching is not included. Manual phase switching is experimental and off by default.

Manual switching uses register `404` only as charger configuration/capability context and register `405` as the writable phase-switch mode. Measured active phases are diagnostic only.

`Default Phase Mode` is derived from `Charger Configuration` and used by the restore button/service.

`Phase Session Override`, `Phase Session Target` and `Phase Restore Pending` show whether a manual switch has temporarily moved register `405` away from `Charger Configuration` and whether restore still needs attention.

`Phase Policy Decision` is diagnostic-only. It shows what the future Solar phase-switching policy would request, but it never writes register `405`.

Useful diagnostic entities:

- `Charger Configured Phases`
- `Effective Active Phases`
- `Phase Switch Mode`
- `Phase Switch Mode Raw`
- `Phase Switch Available`
- `Phase Switch Block Reason`
- `Vehicle Phase Capability`
- `Last Phase Switch Result`
- `Last Phase Switch Block Reason`
- `Last Phase Switch Target`

## Debug Logging

Enable debug logging in Home Assistant:

```yaml
logger:
  logs:
    custom_components.webasto_unite: debug
```

Keep debug logging enabled only while troubleshooting.

## Issue Reports

For suspected bugs, include:

- integration version
- charger model
- firmware version
- Home Assistant version
- relevant settings
- relevant diagnostic sensor values
- a short timeline of what happened
- debug logs if available

Do not share passwords, tokens or public IP addresses.
