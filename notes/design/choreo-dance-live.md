# DANCE on the real base

A show's DANCE cues are small dance steps of the base: sway, rock, twist, shuffle, box (`cue.DANCE_MOVES`).
Every one returns the base to where it started by construction. Until now they played in the twin only.
This is how a live run plays them on the robot. Owned by the Choreography lane. The tracker, the people
guard and the speed measurement are the Thor lane's (drive_bridge: #471 the guard, #472 the tracker).

## Fixed by the user

- **The deadman stays.** The base moves only in CH5 AUTO with the deadman held, under the firmware's
  0.35 command ceiling. Nothing here gates motion; it only sends targets.
- **No new sensors.** Relative motion comes from the Pinpoint odometry (`/odom`). The people guard uses
  the two LIDARs.
- **Free-form DRIVE stays twin-only.** Only DANCE moves, which return home, reach the robot.

## The interface (agreed with the Thor lane, 2026-10-21)

| topic | type | who | what |
|---|---|---|---|
| `/dance/origin` | `std_msgs/Empty` | runner | once per run, at the show's t0, the base at rest: the tracker latches the current `/odom` pose as the show's origin (`origin_seq` +1) |
| `/dance/target` | `nav_msgs/Odometry` | runner | 20 Hz while a move plays and for 1.5 s after it (the home target, zero twist, so the tracker closes the residual). `frame_id` `dance_origin`, `child_frame_id` `base_link`; pose = the intended base pose in the origin frame (x forward, y left, yaw); twist = the intended BODY velocity (m/s, rad/s) as feed-forward. Freshness by receipt time: older than 250 ms means stop |
| `/dance/stop` | `std_msgs/Empty` | runner | on every end but a completion (operator stop, abort, e-stop): stop at once, drop the stream |
| `/dance/status` | `std_msgs/String` (JSON, ~5 Hz) | tracker | `{"ready", "why", "origin", "origin_seq", "mode": idle\|tracking\|settling\|holding, "guard": clear\|stopped\|blind, "err_m", "err_deg"}`; `ready` covers `/odom` and the guard only, `origin` whether one is latched |

The tracker:

- never chases: corrections are capped at the 0.35 ceiling;
- past 0.3 m or 20° of error it HOLDS where it is and reports `mode: holding`;
- when a LIDAR scan is over 0.5 s old it is `blind` and refuses to move;
- refuses a target that arrives before any origin.


## The runner (`base_output.py`, `--base ros`)

- **Preflight, on a live run of a show with DANCE:** the tracker's status must be fresh and ready.
  - Not ready: the show runs WITHOUT its base moves and says why. The arms and everything else play as
    written; every move is relative to the base, so nothing collides.
  - A show with `dance: {required: true}` is refused instead (SHOW_BASE_NOT_READY).
- **The run:**
  - At t0 it publishes the origin.
  - Through each move it streams the same pose the twin draws (`sim_preview`'s model, the same speeds),
    with the segment's body velocity.
  - Nothing is sent between moves, so the tracker stops.
  - On completion the last move still gets its settle. On any other end it publishes `/dance/stop`.
- **Logging:** the guard (`stopped`: someone in the way; `blind`) and a `holding` tracker are logged.
  Neither aborts the show: the tracker decides what the base does.
- **Elsewhere:** a runner without `--base ros`, or a run that is not live, plays DANCE in the twin only.

## Speeds

The twist is in m/s and rad/s, and drive_bridge maps it to the wheels through its full scale, the speed
at DRIVE 1.0. In AUTO that is the firmware ceiling: 0.35 × the CH8 knob × full command.

- A dance unit of 1.0 is that full scale, so **shows assume CH8 at its maximum**. A lower knob makes the
  base lag, and the tracker holds rather than chase.
- `cue.BASE_M_PER_S_FULL`, `BASE_M_PER_S_STRAFE` and `BASE_RAD_PER_S_FULL` are still the simulator's
  placeholders (0.70 m/s, the same strafing, 1.40 rad/s). The Thor lane measures them on `/odom` (a short
  drive at the robot, in open space) and they are replaced then, strafing separately if it differs.

## Staging (also in every DANCE show's notices)

- **Glass.** Keep the show's declared floor radius clear of glass railings and glass walls: the LIDARs
  see through glass, so the guard does not see the edge.
- **Polished floors.** The odometry pods may skip on a polished floor: run a short out-and-back there
  first and compare `/odom` with where the base really went.
- **The floor test at the venue**, in this order:
  1. DANCE alone, measured with `/odom`: each move returns home within the tracker's error;
  2. then a full show.
