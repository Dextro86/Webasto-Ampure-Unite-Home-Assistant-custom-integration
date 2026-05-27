# EVCC Compatibility

This integration exposes charger entities and diagnostics that can be used by EVCC through Home Assistant.

Use only one active charging controller. Do not let EVCC and this integration both run Solar surplus control at the same time.

## Recommended Setup

When EVCC controls charging:

- Set `Integration Charging Control` to `External Controller`.
- Set `Default Mode` to `Normal`.
- Keep this integration's Solar control disabled. EVCC should be the Solar/loadpoint brain.
- Keep this integration's DLB disabled unless you intentionally want an additional local safety cap.
- Do not use experimental manual phase switching for EVCC automation yet.

In `External Controller` mode the integration still reads the charger, sends keepalive and exposes control entities. It does not let its own Solar/DLB/fixed-current controller write automatic current targets. EVCC can use `Charging On/Off` and `External Requested Current` as the external control path.

Important current-control distinction:

- `External Requested Current` is the active current command for EVCC. Use this for EVCC `setMaxCurrent`.
- `Maximum Current` is only the configured upper safety bound. Do not use it as the EVCC current command.

## Relevant Entities

Entity IDs depend on the Home Assistant entity registry. Always check the actual entity IDs in Home Assistant before copying the example below.

| Purpose | Entity |
|---|---|
| Charger status | `sensor.webasto_unite_iec_61851_state` |
| Enable/disable charging | `switch.webasto_unite_charging_allowed` |
| Pause charging | `button.webasto_unite_pause_charging` |
| Resume charging | `button.webasto_unite_resume_charging` |
| Set charging current | `number.webasto_unite_requested_current` (`External Requested Current`) |
| Maximum allowed current | configured in integration settings |
| Active power | `sensor.webasto_unite_active_power` |
| Current L1 | `sensor.webasto_unite_current_l1` |
| Current L2 | `sensor.webasto_unite_current_l2` |
| Current L3 | `sensor.webasto_unite_current_l3` |
| Session energy | `sensor.webasto_unite_session_energy` |
| Observed active phases | `sensor.webasto_unite_effective_active_phases` |
| Compatibility diagnostics | `sensor.webasto_unite_evcc_status` |

## Example EVCC Charger Configuration

Replace every entity ID with the actual entity ID from your Home Assistant instance.

Existing Home Assistant installations can keep older entity IDs in the entity registry. For example, your charging switch may not be named exactly like the example below. Always verify the entity IDs under `Settings` -> `Devices & services` -> `Entities`.

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
```

The same example is available as [examples/evcc_home_assistant.yaml](../examples/evcc_home_assistant.yaml).

`Maximum Current` also exists as a legacy/config number entity, but it is disabled by default and should not be used as the EVCC current command.

Do not configure `phaseswitch` for this integration yet. Manual phase switching is experimental and is not exposed as the EVCC Home Assistant phase-switch select with options `1` and `3`.

## IEC 61851 State

The `IEC 61851 State` entity is derived from charger status registers. It is intended as a compatibility state for tooling such as EVCC.

Typical values:

- `A`: no vehicle connected
- `B`: vehicle connected
- `C`: charging
- `E`: unavailable or disabled
- `F`: faulted

## EVCC Status Sensor

`EVCC Status` is a diagnostic sensor with stable attributes for integration and support use.

Attributes include:

- control owner
- charger state
- IEC 61851 state
- offered current
- actual current
- actual power
- session energy
- active phases observed
- vehicle connected
- charging
- faulted
- unavailable reason

Attributes ending in `_label` are intended for human reading. Other attributes are intended to stay machine-friendly.

## Limitations

- Automatic phase switching is not available.
- Manual phase switching is experimental and not intended for EVCC automation yet.
- EVCC and this integration should not both manage Solar charging at the same time.
- `Monitoring Only` is not suitable for EVCC control because the control entities do not write current targets in that mode.
