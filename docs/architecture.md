# Architecture

## Overview

```text
Home Assistant
      |
      v
Coordinator
      |
      +--> Controller
      |       +--> Solar logic
      |       +--> DLB logic
      |       +--> Current target decision
      |
      +--> Core runtime state
      |       +--> Mode runtime
      |       +--> Session runtime
      |
      +--> Control orchestration
      |       +--> Write ownership / permission checks
      |
      +--> Runtime guards
      |
      +--> Write runtime / write queue
      |
      +--> Wallbox reader
              |
              v
        Modbus/TCP client
              |
              v
       Webasto / Ampure Unite
```

## Design Goals

- local-first operation
- stability-first current control
- conservative fallback behavior
- predictable restart behavior
- clear diagnostics
- Home Assistant-native entities and services
- EVCC compatibility through Home Assistant entities

The public runtime behavior is defined in [Behavior contract](behavior_contract.md). Architecture changes should preserve that contract unless the contract is intentionally updated first.

## Main Components

### Modbus Layer

Owns the charger protocol boundary:

- `modbus/registers.py`: register definitions and known Webasto/Ampure mapping.
- `modbus/client.py`: Modbus/TCP communication, retries and pymodbus compatibility handling.
- `modbus/reader.py`: reads charger registers and translates them into a `WallboxState`.

The root-level `registers.py`, `modbus_client.py` and `wallbox_reader.py` files remain compatibility import layers.

### Controller

Pure control logic. It decides the target current based on mode, Solar input, DLB input and charger state.

`features/solar.py` owns Solar calculation, Solar runtime state and Solar phase calculation-context helpers. The root-level `solar.py` remains as a compatibility import layer.

`features/dlb.py` owns Dynamic Load Balancing current-limit calculation. The root-level `dlb.py` remains as a compatibility import layer.

`features/control_cycle.py` owns the coordinator polling cycle: session transition handling, Home Assistant input reading, phase policy evaluation, runtime guards, control writes and snapshot construction.

### Core Runtime State

Small HA-independent runtime helpers own state transitions that previously lived directly in the coordinator:

- `core/config.py`: builds `ControlConfig` from merged config/options while preserving legacy compatibility aliases.
- `core/capabilities.py`: builds capability diagnostics from the current charger snapshot.
- `core/limits.py`: combines mode, DLB, charger, cable and EV limits into one final current target.
- `core/mode.py`: selected charge mode, temporary session overrides, pause/resume state and effective mode calculation.
- `core/session.py`: vehicle plug/unplug transition detection.
- `core/snapshot.py`: maps the current control-cycle state into the public `RuntimeSnapshot`.
- `core/status.py`: maps internal mode/decision state to the public operating state.

The coordinator still exposes backward-compatible attributes for tests and existing internal call sites, but the state ownership has started moving into `core`.

### Control Inputs

Reads Home Assistant sensors for DLB and Solar.

### Control Orchestration

`control/orchestrator.py` owns write permission decisions:

- whether the integration's automatic controller may write current targets
- whether direct current commands are allowed
- whether static register sync is allowed
- which block reason should be reported

`control/inputs.py` owns Home Assistant sensor input reading and validation for Solar and DLB. The root-level `control_inputs.py` remains as a compatibility import layer.

`control/write_queue.py`, `control/write_runtime.py` and `control/runtime_guards.py` own queued writes, keepalive/write diagnostics and transient startup/charge-start guards. Their root-level modules remain compatibility import layers.

`control/current.py` owns current-write stability, throttling and meaningful-change decisions.

This keeps `Monitoring Only`, `Enabled`, `External Controller` and `Phase Switch in Progress` behavior in one place instead of duplicating it throughout the coordinator.

### Runtime Guards

Applies startup and transient protections around controller decisions.

### Write Runtime

Owns queued writes, keepalive timing and write-result bookkeeping.

### Coordinator

Orchestrates Home Assistant polling, reads, control decisions, writes and runtime snapshots.

The coordinator is still the main orchestrator. Current refactoring has made phase actions less implicit, moved mode/session state to `core`, and moved write-permission decisions to `control/orchestrator.py`. It has not yet been fully split into feature-level orchestrators.

The coordinator update cycle is intentionally structured as explicit private steps: session transition handling, input reading, controller/policy evaluation, phase-action selection, runtime guards, write enqueueing and snapshot building.

### EVCC Compatibility

Builds stable compatibility attributes and derived charger status for EVCC-style use through Home Assistant.

### Phase Observer / Phase Engine

Phase observer exposes diagnostic phase information. It reports observed session phase usage and phase offer state; it does not infer a permanent vehicle capability from one session.

`features/phase_runtime.py` owns mutable phase-switch/policy runtime state. `features/phase_observer.py` owns observed/requested phase diagnostics. `features/phase_policy.py` owns Solar phase target evaluation. `features/phase_switch.py` owns small phase helper functions and the runtime facade for phase diagnostics/session override bookkeeping. `features/phase_engine.py` owns the actual locked register-405 write. The root-level phase modules remain compatibility import layers.

Phase engine supports experimental manual phase-switch execution. It owns a locked direct write to register `405` (`0 = 1P`, `1 = 3P`) and then marks the phase state as settling. This deliberately follows EVCC's Vestel/Webasto approach more closely: the engine does not pause charging, does not resume charging, does not retry, and does not treat physical phase observation as a hard success/failure condition. Register `404` is exposed as diagnostic context only, because field testing showed it can report `1P` while the charger is physically charging on 3 phases. Physical phase usage remains a diagnostic signal from the normal polling loop.

Phase restore is explicit: live unplug only clears integration runtime/session state and does not write register `405`, because the charger may still be closing the session internally. New plug-in and restart paths no longer trigger automatic 3P restore or mismatch recovery. Use the manual `Restore Configured Phase` control if you want to write the configured phase mode.

Phase policy evaluates whether Solar conditions justify 1P or 3P, tracks how long the same target has remained stable, applies cooldown/session-count guards, and can trigger the same direct register-405 write path as manual switching when `Phase Switching Mode = Automatic Solar`. A `Requested 3P, Observed 1P` state is reported diagnostically; the integration keeps charging instead of starting automatic recovery. In `External Controller` mode, EVCC may request phase switches through the phase select and the integration's own Automatic Solar phase switching does not run.

## Safety Philosophy

If input data is stale, unavailable or inconsistent, the integration should avoid aggressive behavior.

Typical conservative actions:

- reduce to fallback current
- pause Solar charging
- avoid phase switching
- keep diagnostics visible
- avoid repeated writes without evidence

## Public Interfaces

The intended public interface is:

- Home Assistant entities
- Home Assistant services
- config/options flow
- diagnostics

Internal modules may change between releases.
