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

In `External Controller` mode, the integration's own automatic controller also does not write calculated targets. External writes through `Charging On/Off`, `External Requested Current` or the `set_current` service are still allowed and are recorded as `External Controller`. During a phase switch, the latest external current request is deferred until the phase-switch sequence is finished.

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

Phase switching is experimental and off by default. `Manual Only` exposes explicit controls and the EVCC-compatible phase select. `Automatic Solar` allows this integration to switch phases only while it owns Solar control.

Phase switching uses register `404` only as charger configuration/capability context and register `405` as the writable phase-switch mode. Measured active phases are diagnostic only.

Phase switching reports separate checks:

- Pause verification: after writing `0 A`, charging must actually drop to a low/paused state before register `405` is written.
- Register verification: register `405` readback must hold the requested value for stable polls.
- Physical verification: after charging resumes, measured active phases must match the requested phase count within the observation window.

This distinction matters because some chargers can accept the register write before the current charging session physically changes phases.

`Default Phase Mode` is derived from `Charger Configuration` and used by the restore button/service.

`Phase Session Override`, `Phase Session Target` and `Phase Restore Pending` show whether a manual switch has temporarily moved register `405` away from `Charger Configuration` and whether restore still needs attention.

`Phase Policy Decision` shows what the Solar phase-switching policy would request. It writes register `405` only when `Phase Switching Mode = Automatic Solar` and this integration is in `Enabled` control mode.

The auto-policy diagnostics also expose:

- `Phase Policy Auto Ready`: true only when the same 1P/3P target has been stable long enough and no guard blocks it.
- `Phase Policy Auto Block Reason`: for example `Waiting For Stable Phase Target`, `Cooldown Active` or `Session Switch Limit Reached`.
- `Phase Policy Stable Target Time`: how long the current 1P/3P target has remained stable.
- `Phase Policy Required Target Time`: the required stability window before automatic switching is allowed.
- `Phase Policy Cooldown Remaining`: remaining cooldown after a phase switch.
- `Phase Policy Session Switch Count` and `Phase Policy Session Switch Limit`: protection against excessive automatic switching in one plug-in session.

Useful diagnostic entities:

- `Charger Phase Capability (Register 404)`
- `Effective Active Phases`
- `Phase Switch Mode (Register 405)`
- `Phase Switch Mode Raw (Register 405)`
- `Phase Switch Available`
- `Phase Switch Block Reason`
- `Observed Session Phase Usage`
- `Phase Consistency`
- `Last Phase Switch Result`
- `Last Phase Switch Block Reason`
- `Last Phase Switch Target`
- `Phase Switch State`

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
