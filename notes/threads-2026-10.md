# Open threads, October 2026

Six threads raised together and deliberately not started together, each large enough for its own
conversation. This holds enough for one to start cold. **Where they connect is the part worth
recording** — three share a deadline, and two are the same architectural question.

| | Thread | Goal — what is true when it is done |
|---|---|---|
| **A** | Arm vendor portability | A second, non-OpenArm 6-DOF arm set runs on **opendubs1** and needed *only* a new adapter — no change to the authority model |
| **B** | Choreography authoring + playback | An operator composes a timed sequence and plays it from the tablet, under the existing lease and preemption rules |
| **C1** | Face: interaction intent | We can say what the face is *for* — legibility, attention, turn-taking — from evidence rather than taste |
| **C2** | Face: render budget | We know what the panel sustains, so C1's ambitions are bounded by measurement |
| **D** | Teleop on upstream IK | Teleop runs on upstream's own teleop interface; we gain frame selection, status-with-reasons and redundancy, and delete local clutch/IK code |
| **E** | Public demonstration kiosk | opendubs2 safely runs public interaction, with children present, on a fixed date |
| **F** | Force-triggered compliance | An arm enters freedrive because it is being pushed, not because a button was pressed |

**A and E are the same question.** A new arm vendor and a kiosk's behaviour API both resolve to
*what is the stable contract beneath the arms* — goal 4's adapter contract. Two conversations, one
answer, and they must agree.

**B and C are not independent of E.** They are most of what the public actually experiences.

**D goes before F**, because it may delete the code F would otherwise be written against.

---

## D · Teleop on upstream IK — the near-term one

Upstream now ships a teleop interface (`ssik.teleop`) built around a `Clutch`, a `Tracker`, and a
`PoseSource` protocol — *anything yielding `(pose, time)` pairs*. Our WebXR bridge already yields
exactly that, so it becomes a PoseSource and a large amount of hand-rolled clutch and IK plumbing
is deleted. **Less local code is the point**, given how much has already drifted.

Four things it provides that we were otherwise going to build:

- **`Clutch(frame="world"|"tool")` is the frame selector.** Written up in
  [manual control](design/arm-manual-control.md) as the single idea most worth taking from
  industrial pendants — it arrives already implemented.
- **Status with reasons**: `OK` / `LIMITED` (arm lagging the target) / `HELD` (`unreachable`,
  `limits`, `jump`). Strictly better than the loop-health readout now on the TELEOP page, which
  can say the loop is late but not **why the arm stopped following the operator's hand**.
- **Redundancy control for 7-DOF** — elbow self-motion. Our arms are 7-DOF and nothing exposes
  this today.
- **A safety disclaimer that specifies the layer we already decided to build**: no collision
  checking, no acceleration or jerk limits, no watchdog, and run teleop through a controller with
  its own safety layer and a supervised stop. That is the arm-authority wrapper, arrived at
  independently. Live concern, not a formality: our arms can reach the robot's own head at
  J2 ≈ 182° with the elbow folded, measured.

### The prerequisite: build an artifact for **v1**

Performance is a non-issue — the shipped native backend solves OpenArm in **0.195 ms** against our
6.7 ms budget, 23× faster than the Python path. (An earlier concern here came from misreading a
benchmark for a different arm on the Python path; the correct figure is ~3% of budget.)

**But the shipped prebuilt is OpenArm v2.0 and this robot is v1.** That is not a detail:

- Every joint origin and axis differs — v1 runs **+Z** with lateral offsets and a −90° mount, v2
  runs **−Z** with none.
- The baked link names (`openarm_*_base_link` → `openarm_*_ee_base_link`) **do not exist** in our
  v1 tree, which has `openarm_*_link0` and `openarm_*_hand_tcp`.
- We have a parallel-link gripper past the flange.

Upstream's own guidance says to build your own when the URDF source differs, when the end effector
is not the flange, or when link names do not match — *especially on a 7R arm with a tool attached*.
We fail all three, so **`ssik build` against our v1 URDF is step one**, before any migration. It
accepts xacro directly.

!!! note "v1 versus v2 has now bitten twice in one week"
    Once in the dashboard arm view, which initially rendered v2 geometry, and once here. Treat
    "supports OpenArm" as meaning v2 unless stated otherwise.

---

## F · Force-triggered compliance

The pattern: no button — guide the arm and it complies, holds where it was led, then eventually
relaxes to a low-energy hold.

[Manual control](design/arm-manual-control.md) settled freedrive as latched, on-screen, per-arm,
CH5 `MANUAL`, specifically because the press-and-hold pendant pattern fails when both the
operator's hands are on the arm. Removing the button entirely is better still, and would dissolve
that note's open question about a physical button.

**It probably needs no force sensor.** Insistent force against an impedance controller appears as
**sustained tracking error** — the arm pushed away from its setpoint — and position error is
already available. The noise floor is measured: ~1° of steady-state error from imperfect gravity
compensation, so a threshold has a basis rather than a guess.

Two real difficulties, neither of them detection:

- **An arm that yields when pushed is an arm that yields to a collision it should resist.** The
  state machine is the hard part.
- **"Eventually relaxes" will not happen by itself.** The motors hold their last command
  indefinitely with no timeout (measured), so relaxing has to be commanded.

---

## E · Public demonstration kiosk — the only fixed date

Requirements are recordable now and do not depend on how it is implemented:

- A **fixed public date**, so the critical path matters more than the feature list.
- **Children present**, which makes the safety story a deliverable rather than a caveat. Worth
  naming in week one, not week four.
- Depends on **B** and **C**, a working arm stack, and an unattended-ish failure story.

The method is being developed separately and is not recorded here.
