# Diagnostics

The integration exposes diagnostic entities to make charger behavior, Solar control, DLB and EVCC compatibility easier to debug.

Diagnostic meanings follow the [Behavior contract](behavior_contract.md). Phase-policy diagnostics show both the decision state and whether Automatic Solar is ready to execute a guarded phase switch.

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
- `Last Control Write Reason`: why that write was made, for example Fixed Current, Solar, DLB, fallback or `Vehicle Disconnected`.
- `Last Control Write Register`: register used for the write.
- `Last Control Write Age`: how long ago the last current write happened.
- `Last Control Write Blocked Reason`: why a calculated write was not sent, for example `Monitoring Only`, `External Controller Mode` or `Vehicle Not Connected`.

`Last Control Write` also exposes verification attributes. The integration compares the last written current with the next normally polled `Reported Current Limit`; it does not perform an extra Modbus read just for verification. The status can be `pending`, `accepted`, `mismatch` or `unavailable`.

In `Monitoring Only`, the integration may still calculate `Final Target`, but `Control Writes Enabled` is false and writes are not sent to the charger.

In `External Controller` mode, the integration's own automatic controller also does not write calculated targets. External writes through `Charging Enabled`, `External Requested Current` or the `set_current` service are still allowed and are recorded as `External Controller`. During a phase switch, the latest external current request is deferred until the phase-switch sequence is finished.

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
- `Observed Phase`
- `Actual Phase Current`
- `Active Power`

`EVCC Status` exposes machine-oriented attributes for compatibility and support.

## REST Diagnostics

REST diagnostics are optional and read-only. They are only created when `Enable REST Diagnostics` is enabled in the integration settings.

Useful entities:

- `REST Status`
- `REST API Version`
- `HMI Version`
- `REST Wallbox Model`
- `REST Identifier`
- `REST Installation Current Limiter`
- `REST Installation Phase`
- `REST OCPP Phase Switching Supported`
- `REST OCPP Free Mode Active`
- `REST Configuration Field Count`

The integration currently uses `/api/system-information` and `/api/configuration-fields`. REST diagnostics are separate from Modbus control. If REST is unavailable, Modbus monitoring and charging control continue to work.

The optional `Soft Reset Charger` action uses the charger's classic WebUI form flow, not the read-only diagnostics endpoints. It requires the same WebUI credentials and is only run when the user explicitly presses the button or calls the service.

## Phase Diagnostics

Phase switching is experimental and off by default. `Manual Only` exposes explicit controls and the EVCC-compatible phase select. `Automatic Solar` can write register `405` when the Solar phase target is stable and cooldown/session-count guards allow it.

Phase switching uses register `404` only as charger configuration/capability context and register `405` as the writable phase-switch mode. Measured active phases are observed session behavior, not a definitive vehicle capability.

Phase switching uses three main diagnostic entities:

- `Requested Phase`: the requested phase mode from register `405`.
- `Observed Phase`: physical phase usage derived from measured L1/L2/L3 current.
- `Phase Recovery State`: the active switch/recovery state, or `idle` when no recovery is active.

Everything else is intentionally exposed as attributes on these sensors or in diagnostics snapshots. This keeps the entity list smaller while still preserving the information needed for support.

Phase switching reports separate requested and observed state:

- `Requested Phase` follows register `405`.
- `Observed Phase` follows measured L1/L2/L3 current usage.
- `Phase Recovery State` shows whether a manual phase request was written and is settling.

This distinction matters because some chargers can accept the register write before the current charging session physically changes phases. The integration reports that mismatch; it does not automatically correct it.

Useful phase attributes:

- On `Requested Phase`: default phase, raw register `405`, phase switching mode, session override/target, restore pending and policy target/decision.
- On `Observed Phase`: measured active phases, observed session phase usage, offer state, phase consistency, register `404` and register `405`.
- On `Phase Recovery State`: current reason, recovery warning, switch state, last result/target/block reason, switch availability, policy block reason, cooldown, stable-target timing and session switch count.

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
