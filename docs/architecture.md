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

## Main Components

### Modbus Client

Owns Modbus/TCP communication, retries and pymodbus compatibility handling.

### Wallbox Reader

Reads charger registers and translates them into a `WallboxState`.

### Controller

Pure control logic. It decides the target current based on mode, Solar input, DLB input and charger state.

### Control Inputs

Reads Home Assistant sensors for DLB and Solar.

### Runtime Guards

Applies startup and transient protections around controller decisions.

### Write Runtime

Owns queued writes, keepalive timing and write-result bookkeeping.

### Coordinator

Orchestrates Home Assistant polling, reads, control decisions, writes and runtime snapshots.

### EVCC Compatibility

Builds stable compatibility attributes and derived charger status for EVCC-style use through Home Assistant.

### Phase Observer / Phase Engine

Phase observer exposes diagnostic phase information. It reports observed session phase usage and phase offer state; it does not infer a permanent vehicle capability from one session.

Phase engine supports experimental manual and Automatic Solar phase-switch execution. It owns the locked pause-confirm/write/verify/resume/observe sequence for register `405`. Register `404` is exposed as diagnostic context only, because field testing showed it can report `1P` while the charger is physically charging on 3 phases. Pause and resume use the same internal charging-enabled semantics as the user-facing Pause/Resume controls. Pause confirmation, register verification and physical phase observation are intentionally separate, because a charger can keep charging after a pause request or accept register `405` before the active charging session physically changes phases. Active-session switching uses a conservative full-sequence retry: if the vehicle does not resume or the physical phases do not match after the first sequence, the engine performs one complete second pause/write/verify/resume/observe sequence and then stops. Manual `Request 3P Mode` always uses a `1P -> 3P` edge trigger to avoid trusting a register that already reports `3P` while the physical session is still 1P.

Phase restore is restart-aware: if register `405` differs from `Charger Configuration` and no vehicle is connected, the coordinator may restore it. A live unplug event only clears integration runtime/session state and does not write register `405`, because the charger may still be closing the session internally. A real new plug-in event on a 3P installation starts a 45-second settle period and can then trigger one bounded `1P -> 3P` edge trigger before normal charging control continues. If Home Assistant starts while a vehicle is already connected, the first read is not treated as a fresh plug-in event.

Phase policy evaluates whether Solar conditions justify 1P or 3P, tracks how long the same target has remained stable, applies cooldown/session-count guards, and reports whether automatic switching is ready. It executes only when `Phase Switching Mode = Automatic Solar` and `Integration Charging Control = Enabled`. A `Requested 3P, Observed 1P` state can trigger one bounded recovery only when 3P is clearly intended for the current session. In `External Controller` mode, EVCC may request phase switches through the phase select, but this integration's own Automatic Solar policy does not execute.

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
