# Example shows as format tests — findings (plan 7.3)

Four shows were written on 2026-10-07 to find what the format cannot say: a show that cannot be
written is a format finding, discovered without a robot. All four validate and are in
`software/choreo/shows/`, so CI checks them from now on.

| show | written by | what it tests | closest approach it creates (twin) |
|---|---|---|---|
| `halloween.yaml` | the team's session | seasonal palette, a flicker clip, the lift, the face | 66.5 mm, left arm base ↔ head |
| `christmas.yaml` | the team's session | red/green against the reserved colours, music grid, dance | 69.4 mm, same pair |
| `reaction.yaml` | the team's session | reacting to an outcome (PA-6.5) | 64.5 mm, same pair |
| `agent-written.yaml` | **an agent given only the format spec** | whether the spec is sufficient (PA-6.4) | — |

**The agent test passed.** Given only `choreo-format-v1.md` and the validator as a black box, an agent
wrote a valid, warning-free 30-second show using music, a reused clip, both arms, the head, the face,
sound, the lift, light and a dance, in **two validator rounds**. The spec gaps it reported are fixed in
the spec (below); everything it could only learn by tripping an error is now printed by
`cue.py describe`.

---

## Findings that change the format — new plan items

Ordered by how much they matter. Each is now a plan item (1.8–1.13).

| # | Finding | Evidence | Proposed |
|---|---|---|---|
| **L1 — DONE (plan 1.8)** | **A show can set the robot's state light to a reserved state colour** — E-STOP red, DEGRADED amber, SAFE IDLE green — and the validator says nothing. The light is how a bystander reads whether the robot is safe. The face's themes are barred from this (`face-config.md`, Reserved); `LIGHT` is not. | Halloween and Christmas avoided red, green and orange by hand. The agent, given only the spec, chose **orange** for its chorus. A test confirmed E-STOP's exact red and DEGRADED's exact amber pass. | **Done, 2026-10-07:** `LIGHT` colours within ±20° of hue of E-STOP red or DEGRADED amber, and saturated enough to read as them, are refused. Green was left available (decided). The Christmas gold and the agent's orange were changed. (1.8) |
| **R1 — DONE (plan 1.9)** | **A show cannot return the arms to rest.** The rest pose is all zero, and J4 = 0 is the elbow's raw lower limit — inside the validator's 5° margin, so "back to rest" is refused. The runner's start pose is the same zero. | Halloween and the agent both failed on exactly this, independently. | **Done, 2026-10-07:** `pose: rest` — every joint 0, clamped into the robot's own limits with no extra margin, resolved from its limits file; speed-checked as usual. Halloween ends at rest again. (1.9) |
| **T1** | **No cue selects a face theme.** Both seasonal shows wanted their theme and accessories; neither could ask for them. | Halloween, Christmas. | `FACE: { do: theme, name: … }`, once the head has the `THEME` verb (Tab5 plan TW2). (1.10) |
| **S1** | **A sound file must exist to validate, but a missing music file is only a notice** — and seasonal sounds, like seasonal music, would not be committed. | Halloween wanted a sound of its own; Christmas wanted sleigh bells. | Treat a sound file as music is treated — a notice when absent locally — while still refusing a name that is neither an earcon nor declared, so a typo stays an error. (1.11) |
| **M1** | **A clip cannot be mirrored onto the other arm.** "Wave the right arm the way the left did" means writing it twice. | Christmas; also `beat-demo`'s finding 2. | `mirror: true` on a placement, swapping `ARM_L`/`ARM_R` and `GRIP_L`/`GRIP_R` (the outward-sense convention already makes the joints read the same). (1.12) |
| **C1** | **A show cannot choose at run time.** The reaction show can express forethought — look, show intent, then reach — because that is timing; it cannot express "if the grip failed, play the failure reaction". Both reactions are written as clips; only one can be placed. | `reaction.yaml`. Reacting to failure is the cheapest legibility win in the literature (Takayama et al.). | A choice point in a show — a placement with alternatives that the runner resolves from an event (the grip's result). Needs design: it interacts with the runner, the second scheduler (PA-5.5, GO semantics) and the parked trigger graph (P2). (1.13) |

**Not a format finding, but recorded:** the perimeter band's motion has no cue either (Halloween
wanted it), and should not get one until the Tab5 plan's F2 decides what the band means.

---

## Spec fixes made from the agent's report

| Gap the agent hit | Fixed in |
|---|---|
| The zero start pose is illegal for J4, and the spec did not say | §1 *Defaults worth knowing*, and finding R1 |
| Limits, speed caps and the clearance rule were not given | §1 points to **`cue.py describe`**, which prints them from the validator's constants |
| §6 said `SOUND ms`; the field is `secs` | §6 |
| The `requires:` vocabulary and its rules were undocumented | §1 |
| `dance:` was missing from the file layout | §1 |
| Defaults unstated: a one-axis `HEAD` cue, `DANCE amp`, when a clip needs a length | §1, §6 |
| `instance` numbering (repeats within a placement, not placements) | §6 |
| How a show's length is worked out | §1 |
| Gripper `effort` had no unit | §7 (the controller's own units, unsourced) |

The agent also asked whether `strength` (which scales toward 90° for the head) applies to a cue
setting only one axis. It does, to that axis; recorded here rather than in the spec until the
capability layer (7.4) revisits `strength`.
