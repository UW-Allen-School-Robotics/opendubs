# Control Authority and Modes

## Goals

- Define who is allowed to command actuators at any moment.
- Avoid ambiguous behavior when autonomy and RC both provide inputs.
- Keep safety interlocks as highest-priority authority.

## Command Source Precedence

Highest to lowest authority:

1. Safety interlocks and fault latches
2. Robot supervisor
3. Autonomous planner/controller
4. RC operator inputs

## Core Policy

- RC is the primary source for manual repositioning workflows.
- In autonomous modes, RC drive and mast commands are ignored by default.
- RC can only command drive/mast during autonomy when explicit override mode is active.

## Mode Taxonomy (Documentation Baseline)

- `manual_drive`
- `assisted_drive`
- `autonomous_nav`
- `autonomous_override`
- `service`
- `fault_recovery`

## Subsystem Commandability by Mode

| Mode | Mobile Base | Mast | Left Arm | Right Arm | Notes |
|---|---|---|---|---|---|
| manual_drive | RC primary | RC optional | disabled/default | disabled/default | Manual repositioning focus |
| assisted_drive | RC + guardrails | limited | disabled/default | disabled/default | Adds software limits |
| autonomous_nav | autonomy primary | autonomy primary | optional by plan | optional by plan | RC ignored unless override |
| autonomous_override | RC temporary | RC temporary | unchanged | unchanged | Audited/operator-confirmed entry |
| service | disabled or local jog | local jog | local test | local test | Maintenance/test context |
| fault_recovery | constrained | constrained | constrained | constrained | Safety-first limited motion |

## Open Decisions

- Exact guard conditions to enter `autonomous_override`
- Timeout/auto-exit policy for override mode
- Logging/audit payload fields for authority transition events
