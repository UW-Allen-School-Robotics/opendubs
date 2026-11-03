# Arms: what a show may rely on from any arm stack

**Status: contract, 2026-10-27.** Nothing in it is built yet beyond what each stack already does. The
gaps are in §10.

Two arm stacks can drive opendubs2's arms:

- **The ROS 2 stack:** `ros2_control` in the `openarm` container, started and stopped by `openarm-up`.
- **The gafro arm service:** `software/openarm/gafro/`, one `opendubs-arm@<side>` unit per arm (#474).

The show runner (`software/choreo/arm_output.py`) and ENCORE's player talk to the arms only through
what this note lists. A stack that serves all of it plays every show unchanged, and a player that
relies on nothing else runs on either stack. Anything a player needs that is not here gets added here
first.

## 1. One stack on the bus at a time

- Exactly one stack commands an arm's CAN bus. Two controllers on the same motors fight, and neither
  knows.
- **gafro refuses to start** while `ros2_control_node` runs, or while another sender puts command or
  register-write frames on the bus (`gafro/guards.py`). It checks once, at start.
- **`openarm-up` does not refuse while the gafro service runs.** That is gap A1.
- Which stack runs is read the same way by every tool:
  - the ROS stack: `ros2_control_node` running in the `openarm` container;
  - gafro: `systemctl is-active opendubs-arm@left` (or `@right`).
- Neither running: the arms are `down`. Both running: every arm is `down`, with that as the reason.
- Read-only status queries (`0x7FF`, `0xCC`, what `openarm-motors` sends) are allowed under either
  stack. gafro's guard lets them through.
- **A stack runs only from what a deploy installed**, which is `main`. A unit started from a checkout
  elsewhere is invisible to the deploy, to the dashboard and to whoever is at the robot. On
  2026-10-27 the gafro units ran from an unmerged branch in a home directory (gap A2).
- **A unit's name says which arm it drives.** `opendubs-arm@left` drives `arm_left` and nothing else.
  On 2026-10-27, `opendubs-arm@left` ran a config whose only real bus was `arm_right`. It commanded
  the right arm and blocked `opendubs-arm@right` from starting (gap T11).
- **A link that goes bus-off is restarted by the stack that runs**, and only with no arm energized on
  it. `openarm-up` does this at activation. Under gafro, nothing restarts it: on 2026-10-27,
  `arm_left` sat BUS-OFF with `restart-ms 0` (gap A3).

## 2. Names

| What | Name | Type |
|---|---|---|
| An arm's routine | `/<side>_controller/follow_joint_trajectory` | `control_msgs/FollowJointTrajectory` |
| A gripper | `/<side>_gripper_controller/gripper_cmd` | `control_msgs/GripperCommand` |
| Where the joints are | `/joint_states` | `sensor_msgs/JointState` |
| Joint ranges | `robot_description` on `/robot_state_publisher` | a URDF string parameter |
| Range margins | `joint_position_margin` on `/<side>_controller` | a double parameter, radians |

`<side>` is `left` or `right`. The arm joints are `openarm_<side>_joint1` to `openarm_<side>_joint7`.
The finger joints are `openarm_<side>_finger_joint1` and `openarm_<side>_finger_joint2`.

## 3. Units and sense

- **Radians** on the wire, metres for the fingers. The runner works in degrees and converts.
- **The sense is the motor's own angle, unmapped.** `openarm-up` checked this on 2026-10-02: after
  driving to zero, all seven joints read about 0 in `openarm-motors`. So three things agree:
  `/joint_states`, `openarm-motors status`, and the stow file (`/etc/opendubs/openarm-stow.conf`).
  A stack whose per-robot direction or zero is not the identity converts before it publishes.
- **The left arm's mirror** (joints 1, 2 and 3 travel the other way) is applied by the show export,
  `cue.py`'s `MIRRORED_JOINTS` and `pose_signed`. A stack never applies it.
- **Fingers** go in the controller's raw metres. The show's 0 to 1 "open" is mapped per robot, on the
  robot (`software/choreo/grippers.py`).
- **Checking a new stack's sense** takes one look per arm: park it to the stow file's pose. It must land
  in the usual stow position, and `/joint_states` must read the stow file's angles within 2 degrees.

## 4. `/joint_states`

- Every arm joint is published at least every 0.5 s. The runner treats older data as unknown
  (`JOINT_STATE_MAX_AGE_S`) and refuses or ends a run on it.
- Position is always given. Velocity and effort are given in full or not at all.
- **The joints may come in several messages**, for example one per arm and one per gripper, as gafro
  does. A consumer merges them by joint name and ages each joint separately. The runner today replaces
  its whole table with each message, so it sees only one arm at a time under gafro. That is gap C1.
- Each joint has exactly one publisher.
- A limp arm's joints are published if the stack can read them. Otherwise they are left out; a stale
  copy is never republished.

## 5. Ranges and margins

- **The runner clamps every waypoint into the range the stack admits.** That range is each arm joint's
  URDF `<limit>` in `robot_description`, narrowed by `joint_position_margin` (a negative margin widens
  it). The runner reads it once and retries after 60 s if the read fails. If it cannot read it, the
  runner sends unclamped and says so.
- **The admitted range is the same one the stack's own range check uses.** A clamped goal is then
  never rejected for its range.
- The ROS stack serves both parameters. gafro serves neither (gap T2): the runner then sends
  unclamped, and gafro rejects any goal that touches its own limits.
- The show validator's ranges are narrower (the arm-limits file), and J2 at most 155 degrees comes
  from the validator (DEC-30). The stack's model carries the head cap only as a backstop.

## 6. Trajectory goals

- **The goal:** one goal per arm per routine, with points every 20 ms from 0.02 s. The first point is
  next to where the arm was measured. Positions only, no velocities. `time_from_start` counts from
  when the goal is accepted.
- **Start on time.** The stack starts the path within 50 ms of accepting it, so the arms stay with the
  music. gafro starts 0.3 s late (gap T4).
- **Follow every path the validator passes.** Peaks run up to `cue.SPEED_CAP_DEG_S`: 90 deg/s on J1
  and J2, 180 on J3, and 135 on J4 to J7. The runner's move to the start pose (`prep_moves`) peaks at
  the same caps. These were measured on the ROS stack on 2026-10-08. gafro refuses any path over
  1.0 rad/s (57 deg/s) on any joint, so it refuses every prep move and most shows (gaps T1, C2, C3).
- **Rejections:** a stack rejects a goal with the wrong joints, outside its range, faster than it can
  follow, or not starting near the arm. The runner ends the show on a rejection, or on a goal not
  accepted in time.
- **One goal at a time.** The runner never sends an arm a goal while that arm's previous goal is still
  running: the routine goes out `START_LEAD_S` (2.0 s) after the prep move's planned end. So the
  stack must report each goal's result within 0.5 s of its last point. On a new goal, the ROS stack
  replaces the running one and gafro rejects it; the runner relies on neither. gafro reports 1.3 s
  after the last point, which still fits inside the 2.0 s lead (gap T5).
- **Results:**
  - `SUCCEEDED` (4): the arm followed the path.
  - `ABORTED` (6): the stack stopped it (tracking, torque, a gate), with the reason in
    `error_string`.
  - `CANCELED` (5): the runner asked.
  - After any result but success, the arm holds where it is.
- **A cancel holds the arm where it is, at once** (the runner's `ABORT_STATE`). The arm never goes limp
  and never returns to a pose.

## 7. Gripper goals

- `command.position` in metres, `max_effort` 5.0. The motor does not enforce the effort; it is
  position control only.
- **Reached:** `SUCCEEDED`.
- **A close that something stops: `ABORTED`, about 1 s after it stalls.** The runner reads that as
  "something is in there". A goal tolerance can also report a blocked close as `SUCCEEDED`, so the
  runner checks the finger joint on `/joint_states` too, and the stack must publish it.
- **A cancel** holds the gripper where it is.
- **A gripper that is not calibrated or is excluded** has no action server, and the dashboard reports
  it `down` (§8).

## 8. "Arms active": what the runner reads

- The runner reads the dashboard (`GET /api/choreo/robot`), never the stack directly:
  - per part, `effectors.arm_<side>.state == "ok"` (`arm-degraded-mode.md` §6);
  - or, from a dashboard that predates `effectors`, `arms == "active"`.
- **`ok` means this arm would accept and follow a goal now,** gates permitting:
  - the ROS stack: its seven joint motors are ENABLED with no fault, and its controller is active;
  - gafro: its service answers and is supervised (a person has said they are at the e-stop).
- `pose_deg`: the measured pose, even while the arm is limp.
- **The dashboard backend (`backend/openarm.py`) fills these from whichever stack runs** (§1). As #474
  stands, it reads only the gafro service. Under the ROS stack, every arm would read `down` and every
  show with arms would be refused (gap T3).

## 9. Gates

- **The runner gates every tick:** CH5 AUTO for a show, the deadman held, the e-stop clear, no fault.
  Any change cancels every goal, and the arms hold.
- **A stack may also gate, as a second and independent stop.** gafro refuses or ends motion unless CH5
  is MANUAL or AUTO, the e-stop is clear and CH6 (the deadman) is held. It checks before every permit
  renewal, so a release stops the arm within the 0.5 s lease. The ROS stack has no gate of its own for
  runner goals; it relies on the runner and the e-stop contactor.
- **The e-stop:** the contactor cuts the arms' power. Nothing is asked of the stack.
- **A stack's gate on a fact the runner cannot see is reported in `effectors`,** so a show is refused
  before it starts rather than stopped mid-routine. gafro's supervision is one example.
- **Clearance for a show goal is the runner's.** The show was validated against the twin, with the
  show's own head motion and J2 at most 155 degrees. A stack checks a runner goal only for range,
  speed and start. gafro's supervision statement ("head tracking off, the neck centred") covers its
  own moves (activate, park, follow), not a validated show that turns the head (gap T8).

### If a process dies

- **The runner dies mid-goal:** the stack finishes the goal it has, which ends at rest. That holds on
  both stacks. The e-stop is the backstop.
- **The stack dies:** the arm holds, stiff. On the ROS stack the motors keep their last command; on
  gafro the permit lapses and the driver holds. The runner sees `/joint_states` go stale and ends the
  show.

## 10. Gaps

These were found against #474 at `63acc623`, and on opendubs2 on 2026-10-27 (A2, A3, T11). Each
owner is a lane, or the gafro service's author (T):

| # | Gap | Owner |
|---|---|---|
| A1 | `openarm-up` does not refuse to start or activate while an `opendubs-arm@` unit is active, and gafro checks only at its own start. Add the check to `openarm-up` (start, activate, park, hold). | Arms |
| A2 | The gafro units were installed on opendubs2 as systemd units from an unmerged checkout, outside any deploy. Either the deploy installs them from `main`, or they are not installed. Whichever stack is not running is never left enabled to start on its own. | the maintainers; Arms for the deploy side |
| A3 | Under gafro, nothing restarts a bus-off link. Either the service restarts its own link when no arm is energized on it (as `openarm-up` does), or the dashboard says `down` with "link off" as the reason. | gafro author, with Arms |
| T11 | `opendubs-arm@<side>` doesn't check that its `--side` and its config's real bus are that side. It should refuse to start otherwise. | gafro author |
| T1 | gafro refuses paths over 1.0 rad/s on every joint ("the driver tops out at 1.0"). Is that the driver's ceiling, or a setting? Either reach the measured caps (90/180/135 deg/s), or publish each joint's ceiling for the runner and the validator. | gafro author |
| T2 | No `robot_description` or `joint_position_margin`. Serve a URDF with the 14 arm joints' `<limit>`, from the same model gafro checks against, and a margin of 0 on `/<side>_controller`. Then the runner clamps into gafro's own range. | gafro author |
| T3 | `backend/openarm.py` in #474 has no path to the ROS stack (`effector_status`, park, hold, activate, down). Merged as it is, it refuses every show and disables the TELEOP buttons on the ROS stack. It should pick by which stack runs (§1). Arms can write the ROS half. | gafro author, with Arms |
| T4 | The path starts 0.3 s after acceptance (a 0.1 s hold, then a 0.2 s `lead_in`), so the arms trail the music by 0.3 s. Start at acceptance, or play the path from 0.3 s in. | gafro author |
| T5 | On a path that runs to its end, the result comes 1.0 s after the end, plus the 0.3 s. End once the arm is within the goal tolerance. Today's margin inside the 2.0 s lead is 0.7 s. | gafro author |
| T6 | `ros_actions.py` says it has never been run, while `settle()` cites a run on the Thor on 2026-10-26. Say what has run. | gafro author |
| T7 | The head cap in `hardware.example.yaml` (182 degrees, a 25 margin) and the README (157) predate DEC-30 (contact 180, cap 155, the neck centred). Regenerate the model. | gafro author |
| T8 | The supervision statement includes "the neck centred", which an operator cannot truthfully make during a show that turns the head. Scope it to the service's own moves (§9). | gafro author |
| T9 | Grippers are off under gafro (`use_gripper: false`), so a show with gripper cues is refused, or needs approval to run without them. Calibration is rung 7 of its bring-up. | gafro author |
| T10 | The left arm's sense under gafro has not been checked (§3: park, look, compare). | gafro author |
| C1 | `RosArms._on_js` replaces its table with each `/joint_states` message. Under gafro (four publishers), `measured()` sees one arm at a time and `finger()` mostly reads None. Merge by name and age each joint separately. | Choreography |
| C2 | `prep_moves` times the start-pose move at the show caps. It has no reason to be fast: time it to the stack's ceiling as well (T1). | Choreography |
| C3 | If T1 leaves gafro slower than the show caps, the validator must check a show against the slower of the two, or such shows cannot play on gafro. **This one is the user's decision:** shows capped for both stacks, or validated per stack. | Choreography, the user |
| C4 | ENCORE's player relies on the same items as the runner (§4 merge, §8 status). | Choreography |
