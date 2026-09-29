# Robot and Subsystem State Machines

A **finite state machine (FSM)** models a component as a fixed set of named **states** with
defined **transitions** between them — at any moment the component is in exactly one state, and it
moves to another only on a specific event or condition. We use FSMs so the robot's behavior
(especially its safety behavior) is explicit and reviewable rather than implicit.

This page defines the hierarchical FSM structure:

- One robot-level FSM
- One FSM per subsystem: mobile base, mast, left arm, right arm

## Robot-Level FSM (Draft)

States:

- `INIT`
- `SAFE_IDLE`
- `MANUAL_CONTROL`
- `AUTONOMOUS_CONTROL`
- `DEGRADED`
- `E_STOP_LATCH`

### Robot-Level Transition Intent

| From | To | Trigger | Guard |
|---|---|---|---|
| INIT | SAFE_IDLE | boot complete | no hard faults |
| SAFE_IDLE | MANUAL_CONTROL | operator mode set | RC link healthy |
| SAFE_IDLE | AUTONOMOUS_CONTROL | autonomy start | autonomy ready + safety clear |
| MANUAL_CONTROL | AUTONOMOUS_CONTROL | autonomy engage | policy allows transfer |
| AUTONOMOUS_CONTROL | MANUAL_CONTROL | operator reclaim/override | override accepted |
| ANY | DEGRADED | subsystem fault | fault is recoverable |
| ANY | E_STOP_LATCH | e-stop/fatal fault | none |
| DEGRADED | SAFE_IDLE | fault cleared | re-entry checks pass |

> **Implementation note (2026-06-11):** latching/readiness semantics are now live in
> `kiosk-dashboard/backend/supervisor.py` (the indicator FSM; enforcement still
> hardware+StamPLC). Concretely: **INIT** exits on real ready-checks (StamPLC telemetry
> fresh), not a timer, with a timeout→`TELEMETRY NOT READY` fault;
> **E_STOP_LATCH** truly latches on `BUTTON`/`SOFTWARE` and exit is a *guarded* re-arm
> (button released ∧ not stale ∧ not welded ∧ CH5=OFF); **DEGRADED** faults latch and
> exit only on an operator clear once the condition is absent (fatal faults — welded
> contactor, PDH hw — are non-clearable from the UI). Stale telemetry is a live,
> self-recovering source, not a latch. See `notes/state-machine-review.md` §7 for the
> full status (R6–R12 still open).

## Subsystem FSM Pattern

Each subsystem uses this base state set:

- `DISABLED`
- `READY`
- `ACTIVE`
- `FAULT`

### Parent-Child Constraint Rule

- Subsystem can be `ACTIVE` only when robot-level mode permits it.
- Robot-level `E_STOP_LATCH` forces all subsystems to `DISABLED` or local safe state.
- Robot-level `DEGRADED` allows only explicit subset of subsystem activity.

## Subsystem-Specific Scope

### Mobile Base FSM

- Must gate drive outputs on deadman and link health.
- Must force neutral output on disallow transitions.

### Mast FSM

- Must gate movement on limit switch validity and homing state.
- Must apply direction clamping near limits.

### Arm FSMs (Left/Right)

Current phase scope is intentionally high-level:

- Enable/disable and safety/fault states only
- Motion-level arm state modeling deferred to later phase

## Partial Enablement Matrix (Initial)

| Robot Mode | Base | Mast | Left Arm | Right Arm |
|---|---|---|---|---|
| SAFE_IDLE | disabled | disabled | disabled | disabled |
| MANUAL_CONTROL | enabled | optional | disabled/default | disabled/default |
| AUTONOMOUS_CONTROL | enabled | enabled | optional | optional |
| DEGRADED | conditional | conditional | conditional | conditional |
| E_STOP_LATCH | forced safe | forced safe | forced safe | forced safe |

## Events and Fault Domains

Global events:
- e-stop
- system watchdog timeout
- major power fault

Local events:
- RC frame loss (base control domain)
- mast limit switch inconsistency (mast domain)
- arm controller fault (arm domain)

The robot supervisor maps local faults to either local containment or global
state transition (for example to `DEGRADED` or `E_STOP_LATCH`).

## Generated Diagram Artifacts

Generated from this source file by `tools/generate_state_machine_mermaid.py`:

- Mermaid source: `docs/diagrams/state_machine_robot.mmd`
- Mermaid source: `docs/diagrams/state_machine_subsystem_pattern.mmd`

The live, implemented FSM is `kiosk-dashboard/backend/robot_state.py` (the
robot-supervisor view rendered on the dashboard's MASTER STATUS page).
