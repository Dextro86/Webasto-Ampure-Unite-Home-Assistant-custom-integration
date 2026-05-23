# Diagnostics

The integration exposes diagnostic entities to make charger behavior, Solar control, DLB and EVCC compatibility easier to debug.

## Core Diagnostics

Useful entities:

- `Connected`
- `Client Error`
- `Reconnect`
- `Refresh`
- `Control Reason`
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

Useful diagnostic entities:

- `Charger Reported Phases`
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
