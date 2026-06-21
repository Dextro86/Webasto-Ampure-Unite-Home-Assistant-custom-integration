# Behavior contract

This document defines the intended runtime behavior of the integration. It is the reference for future refactors and feature work.

## Control modes

`Integration Charging Control` determines who is allowed to write charging current.

| Mode | Behavior |
|---|---|
| `Monitoring Only` | Reads charger state and sends keepalive only. The calculated targets are diagnostic and are not written to the charger. |
| `Enabled` | The integration owns charging-current control for Normal, Fixed Current, Solar and DLB. |
| `External Controller` | EVCC or another controller owns current control through Home Assistant entities/services. The integration monitors, sends keepalive and exposes controls, but its own Solar/DLB/fixed-current controller does not write automatic targets. |

Only one active controller should manage charging current. Do not run EVCC Solar control and this integration's Solar control at the same time.

## Current control

The charger is controlled through the Modbus current-limit register.

- `0 A` means charging is paused by current control.
- Whole amperes are written to the charger.
- Fractional external requests are accepted for EVCC compatibility and rounded before writing.
- The integration does not currently use a separate charger session start/stop command.

`Charging Enabled` is therefore a current-control helper, not a real session lifecycle command.

## Charge modes

`Charge Mode` selects the runtime mode.

| Mode | Behavior |
|---|---|
| `Off` | Writes/keeps `0 A` when control writes are enabled. |
| `Normal` | Targets the configured `Maximum Current`. |
| `Fixed Current` | Targets the configured fixed current. |
| `Eco Solar` | Starts only when enough Solar surplus is available and pauses when Solar conditions are insufficient. |
| `Smart Solar` | Uses at least `Solar Minimum Current` when Solar input is valid, then increases when surplus supports more. |
| `Solar Boost` | Uses `Solar Minimum Current` plus available Solar surplus. |

At vehicle unplug, temporary runtime mode choices are cleared and the next session starts from `Default Mode`.

## Session lifecycle

Vehicle plug/unplug is treated as session context, not as an instruction to change charger phase configuration.

On unplug:

- reset temporary session state
- reset observed session phase usage
- clear phase session override diagnostics
- reset runtime charge mode to `Default Mode`
- do not write register `405`

When no vehicle is connected:

- automatic control does not write current targets
- do not write register `405`

On new plug-in:

- reset current write state
- reset observed session phase usage
- start a short phase/session settle window
- do not automatically write register `405`

This avoids writing phase registers while the charger is still closing or opening a session internally.

## Phase switching

Phase switching is experimental and explicit.

| Feature | Behavior |
|---|---|
| Manual phase buttons/services/select | May write register `405` through the explicit phase-switch path. |
| Restore Configured Phase | Explicitly writes the phase mode derived from `Charger Configuration`. |
| Automatic Solar phase switching | May write register `405` only after the Solar phase target is stable and cooldown/session-count guards allow it. |
| Plug/unplug restore | Disabled. Plug/unplug never writes register `405`. |
| Mismatch recovery | Disabled. Mismatch is reported, not automatically corrected. |

Manual, EVCC and Automatic Solar phase switching use the same EVCC-like Vestel/Webasto write model:

1. Validate that phase switching is enabled and register `405` is available.
2. Write register `405` directly (`0 = 1P`, `1 = 3P`).
3. Mark phase switching as settling.
4. Reset observed session phase diagnostics.
5. Let the normal control loop continue current control.
6. Report physical phase usage only as diagnostics.

`Requested Phase` means register `405`. `Observed Phase` means measured L1/L2/L3 current usage. These are deliberately separate because the charger can accept a register value while the physical charging session remains on another phase.

An explicit manual/EVCC phase request may write register `405` even when `Requested Phase` already shows the same target. This keeps the behavior predictable for users when the register and observed physical phase do not match.

## Solar phase policy

Solar phase policy is the only automatic phase-switch decision path.

It may report:

- `Would Request 1P`
- `Would Request 3P`
- required surplus values
- stable target time
- cooldown/session counters
- auto block reason

When `Phase Switching Mode = Automatic Solar`, `auto_ready = true` triggers the same direct register-405 write path as a manual request. Automatic execution does not run in `External Controller` mode.

## DLB behavior

DLB is a safety limiter. When enabled and valid, it can reduce the final current target below the active mode target.

If required DLB inputs are missing, stale or invalid, the integration uses `Fallback Current` instead of trusting unsafe data.

## EVCC behavior

In `External Controller` mode:

- EVCC should use `External Requested Current` / `set_current` for current commands.
- EVCC may use `Charging Enabled` for enable/disable.
- EVCC may use the `Phase Switch` select if phase switching is intentionally enabled.
- The integration's own automatic Solar phase switching does not run in `External Controller` mode and does not compete with EVCC.
- `0 A` external requests are always allowed; positive external current requests are blocked when the latest charger snapshot explicitly says no vehicle is connected.

During manual/EVCC phase switching, external current requests are deferred only while the phase-register write task is running.

## Design rule for future changes

New behavior must follow these rules:

- No hidden phase writes from plug/unplug, restart, Solar mode selection or mismatch detection.
- No automatic recovery loop that repeatedly writes register `405`.
- Every write path must have one clear owner and reason.
- User-visible controls must describe what they actually do.
- Diagnostics may report advisory state, but advisory state must not silently become control behavior.
