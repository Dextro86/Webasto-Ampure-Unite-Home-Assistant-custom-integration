# EVCC Compatibility

This integration exposes charger entities and diagnostics that can be used by EVCC through Home Assistant.

Use only one active charging controller. Do not let EVCC and this integration both run Solar surplus control at the same time.

## Recommended Setup

When EVCC controls charging:

- Set `Integration Charging Control` to `Enabled`.
- Set `Default Mode` to `Normal`.
- Keep this integration's Solar control disabled.
- Keep this integration's DLB disabled unless you intentionally want an additional local safety cap.
- Do not use experimental manual phase switching for EVCC automation yet.

## Relevant Entities

Entity names depend on the Home Assistant entity registry, but these are the intended purposes:

| Purpose | Entity |
|---|---|
| Charger status | `sensor.webasto_unite_iec_61851_state` |
| Enable/disable charging | `switch.webasto_unite_allow_charging` |
| Pause charging | `button.webasto_unite_pause_charging` |
| Resume charging | `button.webasto_unite_resume_charging` |
| Set charging current | `number.webasto_unite_current_limit` |
| Active power | `sensor.webasto_unite_active_power` |
| Current L1 | `sensor.webasto_unite_current_l1` |
| Current L2 | `sensor.webasto_unite_current_l2` |
| Current L3 | `sensor.webasto_unite_current_l3` |
| Session energy | `sensor.webasto_unite_session_energy` |
| Observed active phases | `sensor.webasto_unite_effective_active_phases` |
| Compatibility diagnostics | `sensor.webasto_unite_evcc_status` |

## Example EVCC Charger Configuration

Replace entity IDs with the actual entity IDs from your Home Assistant instance.

```yaml
chargers:
  - name: webasto_unite_ha
    type: homeassistant
    uri: http://homeassistant.local:8123
    token: ${HA_TOKEN}
    status: sensor.webasto_unite_iec_61851_state
    enabled: switch.webasto_unite_allow_charging
    setMaxCurrent: number.webasto_unite_current_limit
    power: sensor.webasto_unite_active_power
    currents:
      - sensor.webasto_unite_current_l1
      - sensor.webasto_unite_current_l2
      - sensor.webasto_unite_current_l3
```

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
