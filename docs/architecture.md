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

Phase engine currently supports only experimental manual phase-switch decisions. Automatic phase switching is intentionally not included yet.

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
