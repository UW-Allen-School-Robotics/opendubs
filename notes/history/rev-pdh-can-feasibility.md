> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this feasibility question is
> resolved. The PDH ↔ StampPLC CAN path is **implemented**: the firmware sniffs `[PDH]` telemetry
> (bus voltage / current / power) and has `PDH_SWITCH` / `PDH_CLEAR_FAULTS` commands. The only live
> question is a cost/utility **option** — the REV PDH **or** a cheaper fuse block — which lives on
> the Power Bay build page (`docs/build/power-bay.md`), not here.

# REV PDH CAN Feasibility (StampPLC Path)

## Objective

Evaluate bringing REV PDH telemetry and switchable-channel control into the
StampPLC/robot telemetry stack.

Target for phase 1:

- PDH telemetry ingestion
- Switchable-channel control path

## Integration Approaches

### Approach A: Direct REV-compatible integration

- Implement against available REV-documented interfaces and tooling semantics.
- Prefer this path when protocol/API details are sufficiently available.

Pros:
- Lowest architecture complexity
- Cleaner on-robot deployment

Risks:
- Documentation/API surface availability
- Version compatibility

### Approach B: Semantics-aligned custom adapter on TWAI

- Implement custom CAN adapter preserving PowerDistribution-like semantics.
- Expose standardized local topics and commands to AGX.

Pros:
- Full control of local interface contract
- Independent of WPILib runtime

Risks:
- Protocol interpretation mistakes
- More implementation burden

### Approach C: Offboard bridge

- Use external process/device as protocol bridge to robot bus.

Pros:
- Fast experimentation when embedded support is incomplete

Risks:
- Additional latency and failure points
- Operational complexity

## Recommendation

- Start with REV-documented interfaces as implementation basis.
- Use WPILib `PowerDistribution` behavior as semantic reference, not dependency.
- Gate deployment with hardware-in-loop validation before enabling control writes.

## Semantic Mapping Targets

Map these PDH semantics into local telemetry model:

- bus voltage
- temperature
- total current
- per-channel current
- switchable channel state

Potential topic additions:

- `/telemetry/power/pdh`
- `/telemetry/power/pdh/channels`
- `/command/power/pdh/switchable`

## Prerequisites and Risks

- Final CAN bitrate and ID policy for shared bus
- Error handling for CAN timeout/device absence
- Safety policy for comms degradation
- Verification strategy for command write acknowledgment

## Phase-1 Exit Criteria

1. Reliable periodic PDH telemetry visible in local telemetry stream.
2. Switchable channel can be commanded with explicit safety guardrails.
3. Fault scenarios (device missing, stale data, bus errors) handled predictably.
4. Documentation updated with validated behavior and remaining gaps.
