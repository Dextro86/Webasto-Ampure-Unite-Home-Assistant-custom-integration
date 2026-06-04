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

Phase observer exposes diagnostic phase information.

Phase engine supports experimental manual and Automatic Solar phase-switch execution. It owns the locked pause-confirm/write/verify/resume/observe sequence for register `405`. Register `404` is used as charger configuration/capability context. Pause and resume use the same internal charging-enabled semantics as the user-facing Pause/Resume controls. Pause confirmation, register verification and physical phase observation are intentionally separate, because a charger can keep charging after a pause request or accept register `405` before the active charging session physically changes phases. Active-session switching uses a conservative full-sequence retry: if the vehicle does not resume or the physical phases do not match after the first sequence, the engine performs one complete second pause/write/verify/resume/observe sequence and then stops.

Phase restore is restart-safe: if register `405` differs from `Charger Configuration` and no vehicle is connected, the coordinator may restore it. If a vehicle is connected, it only marks restore pending.

Phase policy evaluates whether Solar conditions justify 1P or 3P, tracks how long the same target has remained stable, applies cooldown/session-count guards, and reports whether automatic switching is ready. It executes only when `Phase Switching Mode = Automatic Solar` and `Integration Charging Control = Enabled`. In `External Controller` mode, EVCC may request phase switches through the phase select, but this integration's own Automatic Solar policy does not execute.

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
