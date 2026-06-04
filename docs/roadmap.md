# Roadmap

## Current Focus

- stable local charger control
- reliable Solar surplus charging
- reliable Dynamic Load Balancing
- stale sensor protection
- EVCC compatibility through Home Assistant
- diagnostics for support and troubleshooting

## Planned / Possible Improvements

- more vehicle phase capability heuristics
- additional EVCC compatibility attributes if needed
- support snapshot service for issue reports
- more Home Assistant-native repair/diagnostic messages
- improved diagnostics UI/labels

## Phase Switching Direction

Phase switching should remain optional and conservative.

Current state:

- diagnostic phase observation exists
- experimental manual phase switching exists
- experimental Automatic Solar phase switching exists and is disabled by default

Automatic phase switching should remain:

- disabled by default
- session-aware
- guarded by Solar/DLB safety state
- cooldown-based
- easy to debug
- non-aggressive

## Stable 1.0 Direction

A future `1.0.0` should be based on a period of multi-user testing.

Before calling the integration stable:

- Normal charging should be predictable.
- DLB should behave safely across common sensor setups.
- Solar modes should be understandable and stable.
- Restart behavior should be boring and predictable.
- Documentation should match the actual product behavior.
- Known limitations should be explicit.

## Experimental Ideas

- conservative 1P/3P Solar switching
- vehicle capability learning
- charge planning based on forecast data
- richer EVCC examples

Experimental features should always prioritize safety, stability and predictable recovery over aggressive automation.
