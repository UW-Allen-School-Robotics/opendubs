# Arms: running without a part, with the operator's approval

**Status: design, 2026-10-20. Not built.** One decision for the user is in §5.

## Why

On 2026-10-17 the left gripper's connector broke during an event. The gripper (arm_left motor id 8)
went silent. `openarm-up activate` refused the whole left arm at its 8-of-8 check, and the show
runner refused every show, because its arms gate wants no motor missing. The way out was
`OPENARM_LEFT_HAND=false` (#357): a per-robot file on the Thor and a restart of the arm stack, run
from a shell. It worked, but it needed a shell, a file edit on the robot and a restart that drops
both arms limp. That isn't something an operator can do from the tablet mid-event.

This design turns that into a path the operator takes from the tablet:

1. **Be sure the part is really silent.** On this robot a silent motor has been a parser bug before
   (#210: a held motor read as missing).
2. **Say plainly** which part is gone and what still works.
3. **Offer** to run without it. The operator approves, per part and per session, and the approval
   stays on screen while it holds.

## 1. What can be left out

| Part | Offered? | Why |
|---|---|---|
| A **gripper** (motor 8 of either arm) | **yes** | The last motor in the chain. Without it the arm's seven joints still hold and move. The fingers go limp, which is light and harmless. |
| A **joint** (motors 1–7) | **no** | A silent joint is an unpowered joint: that link hangs under gravity, and the arm can't hold a pose or be commanded safely. The offer is the next one down. |
| A **whole arm** | **yes** | The other arm activates on its own, and the failed one stays inactive and limp. That's what ACTIVATE L or R already does. The new part is saying so and keeping the show gate from refusing the healthy arm. |

## 2. Is it really silent?

When activation stops on "N of 8 motors do not answer", `openarm-up` probes each silent id on that
arm's bus before it offers anything. This is read-only: a passive listen, then the refresh query.

| What the bus shows for the id | Verdict | What the operator sees |
|---|---|---|
| no frame at all on its reply ID, passive or queried | **SILENT** | the offer (§3) |
| frames on its reply ID that the parser rejects | **SOFTWARE** | "motor 8 answers, but the software can't read it". No offer; it's a bug, code `ARM_PART_UNREAD` |
| a status frame with an error nibble | **FAULTED** | the existing fault path: power-cycle the arms with the e-stop. No offer |

The probe is the passive listen and raw query from 2026-10-17, as an `openarm-motors probe <arm> <id>`
subcommand. It sends only the read-only refresh query, never DISABLE, and only on the failed arm's
bus.

## 3. The offer and the approval

On the TELEOP page (`teleop.js`, Arms), in place of the activation failure:

> **LEFT GRIPPER is not answering.** Its motor (id 8) sends nothing on the left arm's bus.
> The left arm's seven joints are fine. Shows that use the left gripper won't run.
> **[ RUN THE LEFT ARM WITHOUT ITS GRIPPER ]**   (this session only)

- **Approval** is one confirmed tap. It records `{arm: left, part: gripper, at, by: tablet}` in
  `/run/opendubs/arm-degraded.json`. `/run` is tmpfs, so a reboot clears it: that is what "per
  session" means.
- **Visible while active:** a fixed banner on the TELEOP page, "DEGRADED: left gripper excluded
  (approved 14:02) [RESTORE]". `/api/arms/status` carries the same fact, so the Operate page's ARMS
  tile (Tablet lane) and the show runner can show it too.
- **Restore** clears the entry, and the next ACTIVATE expects all 8 again. If the part answers at a
  later activation while excluded, the page offers "left gripper answers again: restore it?". It is
  never restored silently.
- **Per part:** each exclusion is approved on its own; approving one never covers another.
- **The per-robot file stays** (`/etc/opendubs/openarm-arms.conf`, #357) for a part known to be out
  of service across sessions. Both feed one exclusion set: the file says "this robot", the runtime
  entry says "tonight".

## 4. What excluding changes

- **`openarm-motors`** leaves excluded ids out of the counts. It lists them as
  `id 8 (gripper) EXCLUDED (operator, 14:02)`, and `summary` gains `excluded=N`. A healthy degraded
  robot reads `missing=0`. The dashboard's parser ignores keys it doesn't know.
- **`openarm-up activate`** expects 7 motors on that arm and doesn't start its gripper controller.
  Every guard stays: activation holds the measured pose, and all seven joint motors must answer and
  enable.
- **`/api/arms/status`** gains:
  - `excluded`: the list of excluded parts;
  - `effectors`: the per-part status agreed with Choreography (§6).

## 5. Decision for the user: restart, or exclude at runtime?

The left gripper is part of the arm model (`robot_description`) that the stack builds at launch.

- **A. Restart-based.** Reuse #357's switch. Approval sets it in the runtime entry, and `openarm-up`
  restarts the stack with the left side's gripper switched off.
  - Small, and already proven on the robot.
  - **But a restart drops BOTH arms limp for about a minute.** That's harmless only if both are at
    stow. If the healthy arm is up mid-show, it falls. So A must refuse unless both arms are at stow;
    getting them there is commanded motion, and that's the operator's call.
- **B. Runtime exclusion in the hardware interface.** An overlay on `openarm_ros2`'s hardware (the
  file A1 already patches) reads the exclusion set at `on_activate`. It doesn't enable, command or
  require an excluded motor, and reports its joint state as unavailable. Approval is then followed by
  an ordinary ACTIVATE of that one arm.
  - No restart, and the other arm is untouched.
  - Costs a C++ change and a colcon build on the robot, as A1 had.

**Recommended: B.** The case it exists for is an event mid-show, with one arm possibly up. A's
"both arms limp" is the hazard to avoid there. Until B is built, A works as it did on 2026-10-17, by
shell.

## 6. The interface with the shows (Choreography lane)

The show side is Choreography's: a show's needs are derived from its cues, a show may list
`can_skip:` parts, and preflight refuses, or offers an approved variant, when a needed part isn't ok.
Agreed with Choreography on 2026-10-20: the Arms side reports per part, in Choreography's part
names, through `openarm.effector_status()` (`backend/openarm.py`). The dashboard proxy (theirs) adds
the lift, light and neck, and hands the runner one `effectors` map, replacing `arms_from_summary()`.

```
{"arm_left":   {"state": "ok" | "down", "why": "J6 (motor 6) no reply since 14:02" | null,
                "pose_deg": [7 angles] | null},
 "arm_right":  {...},
 "grip_left":  {"state": "ok" | "down", "why": "...",
                "excluded": {"by": "operator" | "robot config", "at": "14:02"} | null},
 "grip_right": {...}}
```

- **An arm is `ok`** when its seven joint motors are all ENABLED with no fault. Otherwise it's
  `down`, with `why`: a silent joint, a fault, or not activated. `state` never reads `degraded` for an
  arm, because a joint is never excluded (§1). `pose_deg` is the measured pose, read even from a
  limp arm (the motors answer the read-only query while disabled), so the runner can check a skipped
  arm hangs at stow.
- **A gripper is `ok`** when its motor is ENABLED and its controller runs. An excluded gripper is
  `down`, with `excluded` set and `why` naming the silent motor.
- **Two approvals, about different things.** The Arms approval (§3) lets the arm stack run without
  the part at all. The runner's "Run without grip_left" approves one show's variant. The first
  doesn't imply the second.
- **An excluded gripper's controller doesn't run**, so it has no action server. The runner must not
  send it a goal; a variant without it never does.
- **The arm controller always takes seven joint names.** There is no six-joint mode, and none is
  needed, since a joint is never excluded.
- An excluded part never counts as missing in `openarm-motors summary`, so the old gate also passes
  while the two coexist.

## 7. Codes

New entries in `docs/reference/registry/arms.yaml`:

- `ARM_PART_SILENT`: the offer.
- `ARM_PART_UNREAD`: frames arrive but aren't parsed, so it's a bug and no offer is made.
- `ARM_DEGRADED_ACTIVE`: the banner while an exclusion holds.

## Build order, once §5 is decided

1. `openarm-motors probe` and the exclusion set: a read-only probe, plus counting that honours both
   the per-robot file and `/run`.
2. `openarm-up degrade|restore <arm> gripper`, the activation messages, and the backend endpoint and
   status fields (`backend/openarm.py`).
3. The TELEOP page offer and banner.
4. B's hardware overlay, with its colcon build on the robot under the user's word.
