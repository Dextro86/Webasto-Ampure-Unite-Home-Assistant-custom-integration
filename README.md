# Webasto Unite Home Assistant custom integration

This repository contains a work-in-progress Home Assistant custom integration for
the `Ampure Unite` / `Webasto Unite` wallbox family over local `Modbus/TCP`.

The aim is to grow this into a reusable integration that can later be distributed
through HACS for:

- wallbox monitoring
- dynamic load balancing
- PV surplus charging
- manual session control

## Disclosure

This integration was developed with significant AI assistance during design,
implementation and documentation.

That does not automatically make it unsafe, but it does mean:

- the code and register assumptions should be reviewed critically
- not every modeled behavior has been confirmed on real hardware yet
- community and documentation-derived assumptions may still contain mistakes

At the time of writing, this integration has **not** been broadly validated in
real-world day-to-day charging use. It should currently be treated as:

- experimental
- not yet field-proven
- used at your own risk

The most important remaining real-world validation items are:

- session command register `5006`
- candidate phase-switch register `405`
- behavior across multiple Unite / Ampure firmware versions

## Current status

The integration already contains:

- a Home Assistant config flow and options flow
- base Home Assistant strings/translations for config and options flows
- a Modbus client using `pymodbus`
- sensors, buttons, switches, numbers and selects
- a controller for `normal`, `pv`, `fixed_current` and `off` charge modes
- a write queue for controlled Modbus writes
- unit tests for controller logic, queue behavior and safety controls

The current v1 entity model is intentionally small and should be treated as the
provisional v1 baseline unless there is a clear functional reason to change it:

- operational control entities:
  - charge mode select
  - current limit number
  - charging allowed switch
  - `PV until unplug` switch
  - `Fixed current until unplug` switch
  - start/cancel session buttons
- operational state entities:
  - charging behavior
  - active mode
  - vehicle connected
  - charging enabled
  - active power
  - per-phase active power
  - actual current
  - per-phase voltages
  - configured limit
  - safe current
  - hardware max current
  - session energy
  - energy meter
  - session duration
- diagnostics:
  - connected
  - refresh / reconnect buttons
  - capability summary
  - firmware version
  - charge point phases
  - DLB limit
  - final target
  - control reason
  - dominant limit
  - sensor invalid reason
  - fallback active
  - queue depth
  - pending write
  - client error

The register map now combines:

- the official `Modbus Specification Webasto UNITE` draft (`revision 1.00`, dated `28.06.2022`)
- a real working Home Assistant Modbus setup from the target Webasto Unite
- community findings around control, timeout and keepalive registers
- conservative fallback assumptions where Unite-specific evidence is still incomplete

It still needs broader validation against real Webasto Unite / Ampure Unite
firmware before this should be treated as production-ready.

If this repository is published before that validation is complete, the
recommended positioning is:

- experimental custom integration
- AI-assisted implementation
- limited hardware validation so far

## What the user sees in Home Assistant

After adding the integration, Home Assistant creates one `Webasto Unite` device.
The user will typically see:

- a charge mode select with:
  - `off`
  - `normal`
  - `pv`
  - `fixed_current`
- an `Active mode` sensor
- a `Charging behavior` sensor
- a current limit number entity
- a charging allowed switch for temporary pause/resume
- a `PV until unplug` switch for temporary PV override
- a `Fixed current until unplug` switch for temporary fixed-current override
- start/cancel session buttons
- operational status entities such as:
  - vehicle connected
  - charging enabled
  - active power
  - active power per phase
  - phase voltages
  - actual current
  - configured limit
  - session energy
  - energy meter
  - session duration
- diagnostics such as:
  - capability summary
  - firmware version
  - reported number of phases
  - DLB limit
  - final target
  - control reason
  - dominant limit
  - fallback active
  - pending write
  - client error

In practice, the most important runtime sensors for understanding controller
behavior are:

- `Charging behavior`
- `Active mode`
- `Final target`
- `DLB limit`

During setup, the user explicitly chooses whether the charging installation
should be treated as `1p` or `3p` for control calculations. Register `404`
remains available as a reported charger status, but it no longer drives DLB/PV
calculation decisions by itself.

Static identification data such as serial number, charge point ID, brand and
model remain available through device info and diagnostics, but are no longer
exposed as standard sensor entities. Session start/end timestamps also remain
available in diagnostics rather than as default entities.

The integration also exposes a diagnostic `Capability summary` sensor. This is
meant to communicate how complete the current register validation is for the
connected charger.

## Dashboard example

The integration does not automatically change a user's Home Assistant dashboard.
Instead, this repository now includes ready-to-use Lovelace examples in:

- [`examples/lovelace_dashboard.yaml`](examples/lovelace_dashboard.yaml)
- [`examples/lovelace_basic.yaml`](examples/lovelace_basic.yaml)
- [`examples/lovelace_advanced.yaml`](examples/lovelace_advanced.yaml)
- [`examples/lovelace_troubleshooting.yaml`](examples/lovelace_troubleshooting.yaml)

The example is designed around the entity model above and explicitly includes:

- the selected base `charge_mode`
- the temporary `PV until unplug` switch
- the temporary `Fixed current until unplug` switch
- the `charging_allowed` pause/resume switch
- an `Active mode` sensor so the user can see what the integration is
  actually doing right now

That matters because temporary overrides can make the active behavior differ
from the selected base mode. For example:

- base `charge_mode = normal`
- `PV until unplug = on`
- `Active mode = pv`

The dashboard example also includes `Charging behavior`, which is intended as the
human-friendly summary of what the integration is doing right now. Typical
values include:

- `normal`
- `pv`
- `min_plus_surplus`
- `pv_until_unplug`
- `waiting_for_surplus`
- `paused`
- `off`
- `fallback`
- `dlb_limited`

Suggested use:

- `lovelace_basic.yaml`: everyday household use
- `lovelace_advanced.yaml`: deeper live insight into DLB/PV behavior
- `lovelace_troubleshooting.yaml`: validation and support cases

The repository also includes simple service-call automation examples:

- [`examples/automation_enable_pv_until_unplug.yaml`](examples/automation_enable_pv_until_unplug.yaml)
- [`examples/automation_disable_pv_until_unplug.yaml`](examples/automation_disable_pv_until_unplug.yaml)
- [`examples/automation_enable_fixed_current_until_unplug.yaml`](examples/automation_enable_fixed_current_until_unplug.yaml)
- [`examples/automation_disable_fixed_current_until_unplug.yaml`](examples/automation_disable_fixed_current_until_unplug.yaml)

## Which entities matter

For everyday use, the most important entities are:

- `Charging behavior`
- `Active mode`
- `Charge mode`
- `Charging allowed`
- `PV until unplug`
- `Fixed current until unplug`
- `Current limit`
- `Fixed current`
- `Active power`
- `Actual current`
- `Session energy`

For PV and DLB insight, these matter most:

- `DLB limit`
- `Final target`
- `Control reason`
- `Dominant limit`
- `Fallback active`
- per-phase power and voltage sensors

For troubleshooting, start with:

- `Capability summary`
- `Keepalive overdue`
- `Keepalive age`
- `Pending write`
- `Client error`
- `Firmware version`
- `Charge point phases`

## Supported control modes

The integration now exposes a separate `control_mode` option in the integration
settings. This decides how much write access the integration is allowed to use.

### `keepalive_only`

Use this as the minimum operational mode for Webasto Unite when you want stable
Modbus/EMS session maintenance without letting Home Assistant control charge
behavior.

- Reads wallbox state
- Can send keepalive writes to the life-bit register if keepalive is enabled
- Does not write charge current
- Does not send start/cancel session commands
- Does not sync `safe_current` or `communication_timeout`

For Webasto Unite / Ampure Unite, this is often the safest first active mode to
validate because community reports indicate that the wallbox may require regular
keepalive traffic to keep the EMS/Modbus control path alive.

### `managed_control`

Use this only after the register map has been validated for the actual charger.

- Reads wallbox state
- Can send keepalive writes
- Can write charge current targets
- Can send start/cancel session commands
- Syncs `safe_current` and `communication_timeout` on setup/reconnect

## Charge modes and DLB behavior

Inside `managed_control`, the charge mode select controls the target behavior:

- `normal`: use the configured user current limit
- `pv`: derive target current from PV surplus or grid import/export behavior
- `fixed_current`: target the configured fixed current
- `off`: fail closed in the integration logic and request session cancel when
  transitioning into `off`

The `charging_allowed` switch is a temporary override on top of the selected
charge mode:

- turning it `off` pauses charging by moving the active mode to `off`
- turning it back `on` restores the last active non-`off` mode
- so if the user was in `pv` or `fixed_current`, toggling the switch off/on
  returns to that mode

The `PV until unplug` switch is a separate temporary override:

- it does not change the selected base `charge_mode`
- if base mode is `normal`, turning it on makes the active mode `pv`
- it remains active while the vehicle stays connected
- it resets automatically when the vehicle is unplugged
- if base mode is `off`, `off` still remains dominant

The same temporary PV override is also available through services:

- `webasto_unite.enable_pv_until_unplug`
- `webasto_unite.disable_pv_until_unplug`

This makes it easy to trigger temporary PV behavior from Home Assistant
automations or scripts without relying only on the dashboard switch.

The integration also provides a separate `Fixed current until unplug` switch:

- it does not change the selected base `charge_mode`
- if base mode is `normal`, turning it on makes the active mode `fixed_current`
- it remains active while the vehicle stays connected
- it resets automatically when the vehicle is unplugged
- it is mutually exclusive with `PV until unplug`

The temporary override can also use its own PV strategy through the options
setting `pv_until_unplug_strategy`:

- `inherit`: use the normal `pv_control_strategy`
- `surplus`: force temporary PV to use surplus logic
- `min_plus_surplus`: force temporary PV to use minimum-plus-surplus behavior

This makes it possible, for example, to keep the normal PV mode on `surplus`
while making the temporary `PV until unplug` action behave more practically as
`min_plus_surplus`.

Dynamic Load Balancing is not exposed as a separate user mode in this design.
Instead, DLB acts as a constraint layer on active charging modes:

- in `normal`, the controller charges up to the configured limit, still bounded
  by DLB, hardware, cable and EV limits
- in `pv`, the controller follows PV surplus logic, also still bounded by DLB
- in `fixed_current`, the controller targets the configured fixed current, also
  still bounded by DLB, hardware, cable and EV limits

PV mode now supports two internal strategies:

- `surplus`: calculate charging current from available PV surplus power
- `min_plus_surplus`: always charge at least at `pv_min_current`, and increase
  above that when additional PV surplus is available

Important: `off` is now stricter than before, but it still depends on the
charger honoring the documented session command semantics for register `5006`.
That behavior must be confirmed on real Unite firmware.

## Safety behavior

Several safeguards are implemented in the current code:

- DLB remains a hard limiter across active charge modes
- hardware, cable and EV current limits are all folded into the final target
- current writes are rate-limited and coalesced through a queue
- unsupported sensor units are ignored instead of silently being treated as raw values
- options flow values are range-validated before they are stored
- no stale-data cutoff is enforced yet purely because a sensor value did not change;
  invalid-state handling is currently based on availability, numeric parsing and unit support
- keepalive writes run on their own background cadence instead of depending only
  on the main poll cycle
- keepalive writes are prioritized ahead of current-limit writes in the queue

The runtime model now explicitly distinguishes between:

- raw wallbox state read from Modbus
- validated Home Assistant sensor inputs for DLB/PV logic
- a control decision for the current cycle
- a published runtime snapshot for entities and diagnostics

The control decision carries a primary `control_reason`, plus optional extra
detail such as:

- `dominant_limit_reason`
- `fallback_active`
- `sensor_invalid_reason`

This is intended to make diagnostics and support cases much easier to explain.
`control_reason` is the primary public explanation field; the other values are
supporting diagnostics.

The options flow currently accepts common units for runtime sensor values:

- current sensors: `A`, `mA`, `kA`
- power sensors: `W`, `mW`, `kW`, `MW`

Sensors with unsupported units are ignored and logged as warnings.

## Keepalive behavior

This wallbox appears to be sensitive to missing Modbus keepalive traffic.
The official Unite Modbus document and community reports both indicate that
register `6000` must be written periodically, and that missing keepalive
traffic can cause the wallbox to drop control or fall back to failsafe
behavior.

The integration therefore treats keepalive as a first-class runtime mechanism:

- keepalive uses register `6000`
- the integration periodically writes the value `1` to `6000`
- the default keepalive interval is `10 s`
- keepalive is scheduled independently from the main poll cycle
- keepalive writes have higher queue priority than current-limit writes
- runtime diagnostics expose keepalive age, overdue state, sent count and write failures

The official document states:

- `6000` is an `Alive Register`
- the EMS/master writes `1`
- the EVSE/slave writes `0`
- the EVSE checks this register every `(Failsafe Timeout)/2`
- if no timeout is configured, the fallback check period is `20 s`
- the period cannot be lower than `3 s`

That is why this integration treats keepalive as a hard runtime requirement,
not as a best-effort side effect of polling.

This also matches the user's existing Home Assistant automation, which keeps the
charger stable by writing `1` to register `6000` every `15` seconds.

Practical recommendation:

- use `10 s` as the default keepalive interval unless your hardware testing
  proves a different value is safe
- avoid setting keepalive too close to `20 s`
- use `keepalive_only` as an early real-world validation mode before enabling
  full managed charging control

## Units used by the integration

This integration uses two kinds of values:

- current values in `A`
- power values in `W`

### Values configured or controlled in `A`

These are current-based settings:

- `current_limit`
- `fixed_current`
- `safe_current`
- `min_current`
- `max_current`
- `main_fuse`
- `safety_margin`
- `pv_min_current`
- `fixed_current`

The wallbox itself is ultimately controlled with a current target in `A`.

### Values read as power in `W`

These are power-based sensor inputs:

- `pv_surplus_sensor`
- `dlb_grid_power_sensor`

If the selected Home Assistant sensor reports:

- `W`
- `mW`
- `kW`
- `MW`

the integration normalizes that to `W` internally.

### Values read as current in `A`

These are current-based sensor inputs:

- `dlb_l1_sensor`
- `dlb_l2_sensor`
- `dlb_l3_sensor`

If the selected Home Assistant sensor reports:

- `A`
- `mA`
- `kA`

the integration normalizes that to `A` internally.

### Power-to-current conversion

When PV or DLB uses power-based input, the integration converts `W` to `A`
using a nominal `230 V` phase voltage.

So approximately:

- single-phase: `A = W / 230`
- three-phase: `A = W / (3 * 230)`

This is a practical control approximation for v1, not a full electrical model.

## Relevant Modbus assumptions

The current implementation assumes these addresses behave similarly to Webasto NEXT:

- `1000`: charge point state
- `1001`: charge state
- `1002`: EVSE state
- `1004`: cable state
- `1006`: error code
- `1008`, `1010`, `1012`: measured phase currents
- `1014`, `1016`, `1018`: phase voltages
- `1020`: total active charging power
- `1024`, `1028`, `1032`: active power per phase
- `1036`: energy meter (`uint32`, official document says `0.1 kWh`)
- `100`, `130`, `190`, `210`, `230`: serial / charge point ID / brand / model / firmware
- `400`: charge point power
- `404`: number of phases
- `1100`..`1106`: confirmed hardware, EVSE and cable current limits
- `1108`: assumed EV current limit from NEXT-style maps, not yet confirmed by the
  provided Unite screenshots
- `1502`: session energy (`uint32`, scale `0.001`, exposed as `kWh`)
- `1504`, `1508`, `1512`: session start / duration / end
- `2000`: safe current
- `2002`: communication timeout
- `5004`: charge current target, confirmed writable and readable in the user's
  working Home Assistant Modbus configuration
- `5006`: session start/cancel command
- `6000`: life-bit keepalive

The current implementation also follows the user's working configuration on an
important transport detail:

- most runtime status and measurement registers in the `1000`, `1020`, `1100`
  and `1500` ranges are read as Modbus input registers
- the control registers `2000`, `2002` and `5004` remain holding registers

The remaining unconfirmed assumptions are still the most important area to
verify on real hardware.

To make that visible in Home Assistant, the integration now tracks a small
compatibility model:

- `confirmed`: validated by the official Unite PDF, the user's working HA setup,
  or both
- `optional_absent`: a register appears optional and was not present on this
  charger during runtime reads
- `unconfirmed`: still assumed by community/NEXT-style mappings and not yet
  validated on the target charger

Examples in the current implementation:

- `5004` and `6000`: `confirmed`
- `1108`: usually `optional_absent` unless the charger reports it
- `5006`: currently `unconfirmed`

One remaining discrepancy between sources is worth calling out:

- the provided Home Assistant YAML scaled `1036` as `0.001 kWh`
- the official Unite PDF specifies `1036` as `0.1 kWh`

The integration now follows the official PDF for `1036`.

## Integration options

The options flow currently exposes:

- polling interval
- Modbus timeout
- retry count
- `control_mode`
- keepalive mode and interval
- safe/min/max/user current values
- main fuse and safety margin
- DLB sensor inputs
- PV sensor inputs, thresholds and control strategy

The options flow now also validates important cross-field constraints before
storing settings, including:

- `min_current <= user_limit <= max_current`
- `min_current <= safe_current <= max_current`
- `pv_stop_threshold <= pv_start_threshold`
- required sensor selection for the chosen DLB input model
- required sensor selection for the chosen PV input model when PV uses surplus-based control

The integration also exposes service calls for runtime control:

- `set_mode`
- `set_user_limit`
- `trigger_reconnect`
- `start_session`
- `cancel_session`
- `enable_pv_until_unplug`
- `disable_pv_until_unplug`
- `enable_fixed_current_until_unplug`
- `disable_fixed_current_until_unplug`

For PV specifically:

- `pv_control_strategy = surplus` means the integration expects PV-related power
  input in `W` and converts that to charging current using a nominal `230 V`
  phase voltage
- `pv_control_strategy = min_plus_surplus` means the integration always targets
  at least `pv_min_current` in `A`, and increases above that when surplus power
  supports it
- in all cases, DLB still limits the final charging current in `A`

For fixed-current mode specifically:

- `charge_mode = fixed_current` means the integration directly targets the
  configured `fixed_current` in `A`
- `fixed_current until unplug` uses that same configured current as a temporary
  per-session override
- the `Fixed current` number entity exposes that value directly on the dashboard

For `surplus`, the integration can also apply timing guards:

- `pv_start_delay`: surplus must stay above the start threshold for this many
  seconds before charging begins
- `pv_stop_delay`: surplus must stay below the stop threshold for this many
  seconds before charging stops
- `pv_min_runtime`: once PV charging has started, keep it running for at least
  this many seconds before allowing a stop
- `pv_min_pause`: after PV charging stops, wait at least this many seconds
  before allowing a new start

These timing settings default to `0`, which preserves the previous immediate
threshold behavior until a user explicitly tunes them.

### PV surplus and phase count

PV surplus behavior depends strongly on whether charging is effectively
single-phase or three-phase.

Because the charger is controlled in `A`, the minimum practical surplus depends
on the minimum charging current and the number of phases:

- single-phase at `6 A` is roughly `6 * 230 = 1380 W`
- three-phase at `6 A` is roughly `3 * 6 * 230 = 4140 W`

That means:

- low PV thresholds such as `1800 / 1200 W` are reasonable for single-phase use
- the same thresholds are usually too low for true three-phase PV surplus charging
- on a three-phase setup, PV surplus charging often only becomes practical once
  surplus is closer to `4 kW` or more

If your charger or vehicle effectively remains in three-phase operation, you may
get more predictable results from either:

- higher PV thresholds
- `charge_mode = fixed_current`
- or `pv_control_strategy = min_plus_surplus`

## Example setups

### Example 1: DLB using P1 phase currents

This is a strong v1 setup if your P1 meter exposes total current per phase.

Use:

- `dlb_input_model = phase_currents`
- `dlb_l1_sensor = sensor.p1_current_l1`
- `dlb_l2_sensor = sensor.p1_current_l2`
- `dlb_l3_sensor = sensor.p1_current_l3`
- `main_fuse = 25`
- `safety_margin = 2`

Important:

- those P1 phase sensors should represent current in `A`
- they may already include EV charging current, which is usually acceptable for
  main-fuse protection because the goal is to protect the total grid connection

How it is used internally:

- each phase current is treated as total phase load in `A`
- available phase headroom is calculated in `A`
- the lowest available phase becomes the DLB limit in `A`
- the final charger current is still bounded by charger and EV limits

### Example 2: PV mode using surplus-based charging

Use this if you want charging current to follow available PV surplus.

This example is best suited to single-phase behavior or installations where
PV surplus regularly exceeds the minimum charging power by a comfortable margin.

Use:

- `charge_mode = pv`
- `pv_control_strategy = surplus`
- `pv_input_model = surplus_sensor`
- `pv_surplus_sensor = sensor.pv_surplus_power`
- `pv_start_threshold = 1800`
- `pv_stop_threshold = 1200`
- `pv_min_current = 6`

Units:

- the surplus sensor should provide power in `W` or a supported power unit
- thresholds are configured in `W`
- `pv_min_current` is in `A`

Behavior:

- the integration converts surplus power to a target current in `A`
- if surplus is too low, charging can pause
- optional start/stop delays and minimum runtime/pause can smooth out cloud
  flicker and threshold flapping
- DLB still limits the final charging current

For true three-phase charging, consider starting with higher thresholds such as:

- `pv_start_threshold = 4500`
- `pv_stop_threshold = 3600`

That better matches the practical power needed to sustain roughly `6 A` across
three phases.

### Example 3: Fixed-current mode

Use this if you want a fixed low-current charging mode, for example always
charging at `6 A`, without depending on live surplus.

This is often the more practical starting point for three-phase setups where
pure surplus-based charging would otherwise require high PV thresholds.

Use:

- `charge_mode = fixed_current`
- `fixed_current = 6`

Optional:

- still configure DLB so the charger backs off if household load becomes too high

Units:

- `fixed_current` is configured in `A`

Behavior:

- the fixed-current mode target becomes the configured fixed current in `A`
- no PV surplus sensor is required for this mode
- DLB, hardware, cable and EV limits still bound the final target

### Example 4: PV mode using minimum current plus surplus

Use this if you want PV mode to keep charging at a minimum current even in
winter or mixed weather, while still scaling upward when surplus increases.

Use:

- `charge_mode = pv`
- `pv_control_strategy = min_plus_surplus`
- `pv_input_model = surplus_sensor`
- `pv_surplus_sensor = sensor.pv_surplus_power`
- `pv_min_current = 6`

Units:

- the surplus sensor should provide power in `W` or a supported power unit
- `pv_min_current` is configured in `A`

Behavior:

- the PV mode target is never lower than `pv_min_current`
- when surplus rises, the target current rises with it
- if the surplus sensor is temporarily unavailable, the integration still aims
  for `pv_min_current`
- DLB, hardware, cable and EV limits still bound the final target

This strategy is also a strong candidate for `pv_until_unplug_strategy` when
you want temporary PV behavior to remain useful in winter or mixed-weather
conditions, even if the normal PV strategy stays on `surplus`.

### Recommended timing settings for surplus mode

If surplus-based charging feels too nervous, a practical starting point is:

- `pv_start_delay = 60`
- `pv_stop_delay = 120`
- `pv_min_runtime = 300`
- `pv_min_pause = 60`

These values are not required, but they often make PV charging behavior feel
closer to a dedicated EMS by avoiding rapid start/stop flapping.

Recommended rollout for real testing:

1. Start with `keepalive_only`
2. Validate read registers, keepalive behavior and sensor values
3. Move to `managed_control` only after confirming write behavior

## Services

The integration defines these services:

- `webasto_unite.set_mode`
- `webasto_unite.set_user_limit`
- `webasto_unite.trigger_reconnect`
- `webasto_unite.start_session`
- `webasto_unite.cancel_session`
- `webasto_unite.enable_pv_until_unplug`
- `webasto_unite.disable_pv_until_unplug`
- `webasto_unite.enable_fixed_current_until_unplug`
- `webasto_unite.disable_fixed_current_until_unplug`

Write-oriented services only take effect in `managed_control`.

## Diagnostics

Diagnostics now expose:

- the full published runtime snapshot
- a wallbox summary
- a control summary
- Modbus client connection statistics

The control summary is intended to answer the practical support question:
"why is the charger behaving like this right now?"

## Development notes

Notable recent changes in this codebase:

- Added startup/reconnect sync for `safe_current` and `communication_timeout`,
  now limited to `managed_control`
- Added explicit `control_mode` selection for safer staged rollout
- Hardened `off` handling to clear pending current writes and issue a cancel
  request on transition into `off`
- Added input range validation in the options flow
- Added runtime unit normalization for DLB and PV sensors
- Added a more explicit runtime/control datamodel so controller decisions can
  expose the dominant limiting factor and fallback state

## Remaining work before HACS-grade release

- Validate the register map and command semantics against real Unite hardware
- Confirm the exact behavior of session command register `5006`
- Add fuller integration tests around coordinator behavior in a Home Assistant runtime
- Add better diagnostics for firmware identification and register compatibility
- Decide on migration strategy and defaults for new users
- Maintain repository metadata and release process:
  - `manifest.json` version and repository links
  - repository license and release workflow

## Known limitations

At the current stage, these limitations should be assumed:

- session command register `5006` is still treated as unconfirmed until it has
  been validated on real Unite hardware
- EV max current register `1108` appears optional and may be absent on some
  chargers or firmware variants
- keepalive behavior is strongly supported by the official Unite PDF and the
  user's working Home Assistant setup, but has not yet been validated across
  multiple Unite firmware versions
- phase switching between 1-phase and 3-phase likely exists on Unite/Vestel
  firmware families, but the relevant control path is not yet validated in this
  integration and reports from EVCC suggest that long-running stability may be
  a concern; current public evidence points to `404` as phase status and `405`
  as the likely manual phase-switch register
- power-based DLB and PV calculations still use a nominal `230 V` conversion,
  which is practical for control but not a full electrical model
- the integration currently assumes that the official Unite draft PDF, the
  user's working HA mapping and observed charger behavior are close enough to be
  combined into one compatibility model
- dashboard examples and automation examples are provided as static files; the
  integration does not automatically create or manage Lovelace dashboards

## Hardware validation plan

When real charger validation becomes possible, the recommended order is:

1. Validate read-only identity and measurement registers.
   Check `100`, `130`, `190`, `210`, `230`, `404`, `1000`..`1036`, `1100`..`1106`, `1502`..`1512`.
2. Validate keepalive behavior on `6000`.
   Confirm that writing `1` at the configured cadence keeps the control path stable.
3. Validate current control on `5004`.
   Confirm that written current values are accepted, reflected in reads, and applied by the charger.
4. Validate failsafe registers `2000` and `2002`.
   Confirm timeout behavior and fallback current on communication loss.
5. Validate `5006` session command behavior.
   Confirm whether start/cancel semantics match the current integration assumptions.
6. Validate candidate phase switching registers and behavior.
   In particular, validate whether `404` reflects actual phase state and whether
   `405` can safely switch between `1p` and `3p`, including allowed timing and
   read-back behavior.
7. Validate user-facing runtime behavior.
   Test `charge_mode`, `charging_allowed`, `PV until unplug`, `Active mode`, and `Charging behavior`.
8. Validate DLB and PV strategies under real changing load.
   Test phase-current DLB, grid-power DLB, PV surplus mode, and fixed-current PV mode.

For each validation step, capture:

- charger firmware version
- whether the register read/write succeeded
- observed charger behavior
- whether Home Assistant entities matched the real charger behavior

## Future feature candidates

Features that appear plausible but are intentionally not enabled yet include:

- manual `1p` / `3p` switching if Unite register `405` is confirmed as a safe
  control register
- later, only after that is stable, optional automatic phase switching for PV
  surplus use cases

This is informed in part by public EVCC discussions around Webasto Unite phase
switching behavior, which suggest the capability is real but may not be stable
enough to treat as plug-and-play without hardware validation first:

- [EVCC issue #23734: "Phase switch not stable anymore for Webasto Unite"](https://github.com/evcc-io/evcc/issues/23734)
- [Home Assistant community thread with `404`/`405` Vestel/Webasto mapping](https://community.home-assistant.io/t/webasto-unite-wallbox-change-1-3-phase-operation-and-current-limit-from-ha-now-ampure-vestel/845860)

### Experimental phase switching design

If manual phase switching is added later, the intended model is:

- only available when the integration is configured as a `3p` installation
- a user-facing `select.phase_mode` with `1p` and `3p`
- a `requested_phase_mode` tracked by the integration
- a `reported_phase_mode` derived from register `404`
- an `effective_phase_mode` used for PV/DLB calculations

The key design rule is that `effective_phase_mode` should follow reported
charger behavior, not just the user's request. In practice that means:

- register `405` would be treated as the write candidate for requesting `1p` or
  `3p`
- register `404` would remain the preferred read-back source for the currently
  reported phase mode
- PV and DLB calculations should keep using the reported/effective phase mode
  until read-back confirms that the charger actually changed

The first safe version of this feature should also be conservative:

- manual only, not automatic
- disabled by default or clearly marked experimental
- hidden or disabled entirely for installations configured as `1p`
- blocked while actively charging unless real hardware validation proves that
  live switching is stable
- explicit status feedback such as `pending`, `applied`,
  `blocked_while_charging`, or `readback_timeout`

Only after that manual path is validated should automatic PV-driven phase
switching be considered.

## Warning

This integration can influence charge behavior when `managed_control` is enabled.
Do not enable active control on a live installation until the Modbus behavior has
been verified against your charger model and firmware.
