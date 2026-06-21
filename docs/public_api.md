# Public API

This page defines the intended Home Assistant public interface of the integration.
It is used to keep entities, services, documentation and examples consistent.

Entity IDs can differ per Home Assistant installation. Always verify the actual
entity IDs in Home Assistant before using examples.

## User Entities

These entities are intended for normal dashboards and automations.

### Selects

- `Charge Mode`: runtime charging mode. Options include `Off`, `Normal`, configured Solar modes and `Fixed Current`.
- `Phase Switch`: EVCC-compatible explicit 1P/3P phase select. Experimental; only created when phase switching is enabled and `Integration Charging Control` is not `Monitoring Only`. Available when register `405` is readable.

### Switches

- `Charging Enabled`: single user-facing pause/resume control. This writes current-control state, not a real charger session stop/start command.
- `Solar Until Unplug`: temporary Solar override for the connected session.
- `Fixed Current Until Unplug`: temporary fixed-current override for the connected session.

### Numbers

- `Maximum Current`: configured upper current limit. Disabled by default in the entity registry because most users should set this through integration settings.
- `Fixed Current`: current used by Fixed Current mode.
- `External Requested Current`: active external current request. Only created in `External Controller` mode.

### Buttons

- `Switch to 1P`: explicit experimental request to switch register `405` to 1P.
- `Switch to 3P`: explicit experimental request to switch register `405` to 3P.
- `Restore Configured Phase`: explicit request to write the phase mode derived from `Charger Configuration`.

### Main Sensors

- `Charging Behavior`
- `Active Mode`
- `Active Power`
- `Active Power L1/L2/L3`
- `Current L1/L2/L3`
- `Actual Phase Current`
- `Voltage L1/L2/L3`
- `Reported Current Limit`
- `Safe Current`
- `Session Max Current`
- `Session Energy`
- `Energy Meter`
- `DLB Limit`
- `Final Target`

### Main Binary Sensors

- `Vehicle Connected`
- `Charging Active`

## Diagnostic Entities

These are intended for troubleshooting, support and advanced dashboards.

- charger state diagnostics: `Charge Point State`, `Charging State`, `IEC 61851 State`, `Equipment State`, `Cable State`, `EVSE Fault Code`
- phase diagnostics: `Requested Phase`, `Observed Phase`, `Phase Recovery State`
- Solar diagnostics: `Solar Surplus Input`, `Solar Raw Input`, `Solar Filtered Input`, `Solar Target`, `Solar Phase Count`, `Solar Phase Source`, `Solar Voltage Sum`, `Solar Input State`
- control diagnostics: `Control Owner`, `Control Reason`, `Control Writes Enabled`, `Last Control Write`, `Last Control Write Reason`, `Last Control Write Register`, `Last Control Write Age`, `Last Control Write Blocked Reason`, `Dominant Limit`, `Sensor Invalid Reason`, `Fallback Active`
- communication diagnostics: `Connected`, `Keepalive Overdue`, `Client Error`, `Reconnect`, `Refresh`
- EVCC support: `EVCC Status`
- diagnostic maintenance: `Reset Phase Switch State`

## Official Services

These services are part of the supported automation interface.

- `webasto_unite.set_mode`
- `webasto_unite.set_max_current`
- `webasto_unite.set_current`
- `webasto_unite.trigger_reconnect`
- `webasto_unite.enable_solar_until_unplug`
- `webasto_unite.disable_solar_until_unplug`
- `webasto_unite.enable_fixed_current_until_unplug`
- `webasto_unite.disable_fixed_current_until_unplug`
- `webasto_unite.request_phase_1p`
- `webasto_unite.request_phase_3p`
- `webasto_unite.restore_default_phase`
- `webasto_unite.reset_phase_switch_state`

## Legacy Services

These remain registered for backward compatibility, but they are not shown in
`services.yaml` and should not be used for new automations.

- `webasto_unite.set_user_limit`: legacy alias for `set_max_current`
- `webasto_unite.enable_pv_until_unplug`: legacy alias for `enable_solar_until_unplug`
- `webasto_unite.disable_pv_until_unplug`: legacy alias for `disable_solar_until_unplug`

## Ownership Rules

- In `Enabled` mode, the integration owns Normal, Fixed Current, Solar and DLB writes.
- In `External Controller` mode, external current writes through `External Requested Current` or `set_current` are allowed, while the integration's own automatic current controller does not write targets.
- In `Monitoring Only` mode, current-control writes are blocked.
- When the charger explicitly reports no connected vehicle, positive external current writes are blocked; `0 A` remains allowed for disable/pause behavior.
- When no vehicle is connected, automatic `Enabled` mode control does not write current or phase registers.
- Automatic Solar phase switching can write register `405` only when `Phase Switching Mode = Automatic Solar` and the stable-target, cooldown and session-count guards pass.
- Phase writes happen only through explicit manual phase controls, the EVCC `Phase Switch` select, or guarded Automatic Solar execution.
