# Choreography

A show is **data**, not code: a cue sheet listing what happens to which part of the robot, at
what time. It is validated before anything moves, and the same validation runs here, in CI, and
on the robot immediately before a show is allowed to start.

```bash
python3 cue.py validate shows/*.yaml --limits <arm software>/hw_validation/joint_limits.yaml
python3 tests/test_cue.py          # the checks, checked — no robot needed
```

## Slots

`ARM_L`, `ARM_R`, `GRIP_L`, `GRIP_R`, `HEAD`, `LIFT`, `SOUND`, `FACE`, `LIGHT`.

One slot per independently commandable thing, one owner at a time. Two cues driving the same
slot is a collision, not a blend — that is the bug class that made the lift relays chatter when
an idle head and a script both spoke to the Z-axis.

**`DRIVE` is deliberately absent.** The base does not move under a show until odometry can say
where the robot is: opendubs2 currently reports `y_mm` as NaN with a climbing failure count, so
"keep X and Y clear" is advice the robot cannot honour. Adding the slot is easy; earning it is
the work.

## What validation enforces

- Every waypoint inside the arm's **own** limits (read from the arm software's
  `joint_limits.yaml`, never copied into this repo) with a 5° margin.
- A per-joint speed cap — "over three seconds" hides a 45°/s elbow.
- Gripper travel **per side**: the two were zeroed in opposite physical states, so one range
  cannot serve both.
- **Shoulder/head clearance**, measured on the robot: the arm reaches the head at J2 182° with
  the elbow folded, and *nothing in the arm controller stops it* — its only Cartesian guard is a
  1.2 m box that pose satisfies. A show must leave 25°, and may not raise an extended arm past
  ~135° (measured: 140 accepted, 146 refused on the box).
- Sound inside the band the head's speaker actually carries.
- **No show may acknowledge the pinch guard.** Consent to descend through the zone is a human's
  decision, every time.

Angles for the shoulder joints are written in each arm's **own outward sense**, so a show reads
the same for both sides; the mirror is applied once, in the validator.

## Not here yet

Execution. The runner — slot claims with heartbeat leases, preflight built from each show's
`requires`, abort on e-stop, authority change, fault or watcher loss — lives on the Thor and is
the next piece. Design: `notes/design/behavior-api-scope.md`.
