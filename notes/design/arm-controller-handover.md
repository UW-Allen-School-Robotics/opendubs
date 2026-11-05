# Arms: one controller for both arms, and the handover between them

**Status: built, tested off the robot, 2026-11-04.** It closes gaps A1 and A2 of
[`arm-stack-contract.md`](arm-stack-contract.md), and supersedes #585's per-arm `openarm-up takeover`
(#619). Not yet run on a robot.

## The decisions (the user's, 2026-11-04)

1. **One controller for both arms, chosen robot-wide:** the ROS 2 stack, gafro, or none. There is no
   per-arm mixing.
2. **After a reboot no controller starts on its own.** The arms come up with no owner, and the
   operator picks one.
3. **One explicit handover in each direction,** with the deadman held. Each step says what the arms do
   (under Details on the page; the page itself shows only the step that is running).
   A failed step refuses cleanly and leaves the controller that was in charge.
4. **Only what the controller can do is shown.** A button it can't perform is hidden, and the backend
   refuses it in words; it is never sent to the other stack. Both stacks running, or no way to tell, is
   CONFLICT: no arm button shows until it is resolved. A button's second tap says what the arms do.
6. **Every move needs the deadman held at the press** (the user, 2026-11-05), and is refused at
   once, in one line, without it. When both arms already read at stow, a handover parks nothing, so it
   needs no deadman. Whether the press plus the deadman should also be gafro's supervision (no
   SUPERVISE button) is held until the gafro side is agreed; for now SUPERVISE stays as it was.
5. **The controller is shown on the Arms page and the Shows page,** with no banner on every page.
   The "Teleop" page is renamed "Arms". Both pages read the dashboard's `stack` field.

## What the controller is

It is whichever stack runs, read the same way as before (`backend/openarm.py`, `active_stack`):

| `stack.controller` | It means | Read from |
|---|---|---|
| `ros2` | the ROS 2 stack has both arms | `ros2_control_node` runs |
| `gafro` | the gafro arm service has them | an `opendubs-arm@` unit is active, or its socket answers |
| `none` | nothing has them; the arms are limp | neither |
| `conflict` | both run, or `pgrep` failed | both, or neither can be told |

Nothing is stored. A controller is chosen by starting it and released by stopping it, so the label
can't disagree with what runs.

**Nothing starts at boot.** The gafro units have no `[Install]`, and only `openarm-up` launches the
ROS 2 stack. The `openarm` container restarts at boot, but launches nothing. `provision-arm-service.sh`
now also removes any `opendubs-arm@*.service` link left in a `.wants/` directory by hand (gap A2's
2026-10-27 install).

## The buttons

`stack.unavailable` lists every action, with `null` when the controller can do it now, or the reason.
The page hides a button whose action is not `null`, and the backend refuses it with those words.
The page has one line naming the controller (and a short reason for a CONFLICT), the buttons, one
line for the running step or the last failure, and a collapsed **Details** with everything else.
An earlier version printed every reason and every step on the page; at the robot that was too much
text to use.

| Button (what the arms do) | ROS 2 | gafro | none |
|---|---|---|---|
| **Use ROS 2** / **Use gafro**: the arms stiffen where they are | off | off | on |
| **HAND TO GAFRO** / **HAND TO ROS 2**: park, then rest at stow | to gafro | to ROS 2 | off |
| **Release**: the arms go limp at stow (refused away from stow) | `openarm-up down` | stop, listen, disable | off |
| **Park**: the arms move to stow | `openarm-up park` | the service's park | off |
| **Hold**: stiff where they are | `openarm-up hold`; an inactive arm is activated in place | the service's hold | off |
| **Ready L/R**, **Follow L/R**, **Supervise**, **Prepare for a show** | off (gafro's) | on | off |
| Teleop **START** (the Quest bridge) | on | off (it drives ROS 2's controllers) | off |

Under CONFLICT every row is off; the page says to stop one stack at the robot's computer, and Details names them.

## The changes of controller

Each change runs as the dashboard's one operation at a time (`backend/arm_handover.py`). The page
shows its steps as they run, each with what the arms do. Before anything moves, each change refuses
while a show runs or the Quest teleop bridge runs.

**HAND TO ROS 2** (from gafro). Refused unless the e-stop is clear, CH5 is MANUAL or AUTO, and the
deadman is held, at the press: a press without it is refused at once with "Hold the deadman". The
gates are read for up to 0.7 s, so one of CH6's known ~0.6 s "released" blips does not refuse a
person holding it. If both arms already read at stow the park is skipped, as it is to gafro, and so
are the gates and gafro's supervision: nothing moves. Otherwise gafro must be supervised
(SUPERVISE on the page), and the handover refuses before anything moves if it is not.

| Step | What the arms do |
|---|---|
| 1. Park under gafro | **move** to stow |
| 2. Stop both `opendubs-arm@` units | hold, stiff, at stow (the DAMIAO motors keep their last command) |
| 3. Listen on both buses: must be quiet | hold at stow |
| 4. Disable both arms' motors | go limp at stow |
| 5. `openarm-up activate both` | stiffen at stow (activation holds the measured pose, A1) |

**HAND TO GAFRO** (from ROS 2). Same gates, skipped the same way at stow; gafro's supervision isn't needed to start it.

| Step | What the arms do |
|---|---|
| 1. `openarm-up park both` (skipped when both read at stow) | **move** to stow |
| 2. `openarm-up down` | go limp at stow |
| 3. Listen on both buses: must be quiet | rest limp at stow |
| 4. Start both `opendubs-arm@` units | stiffen at stow |

**When a step fails:**

- **The park fails** (step 1 of either change). The old controller is asked to hold, and nothing is
  stopped.
- **A later step fails.** A rollback step runs and appears in the list: stop whatever was started,
  disable the motors at stow, and start the old controller again. The arms go limp at stow, then
  stiffen at stow.
- **Something other than the two stacks is still commanding a bus.** The rollback refuses to start
  gafro on top of it and says so. The arms are left where they are.

**Use ROS 2 / Use gafro** (from none). No gate, because nothing moves. If the start fails, the started
stack is stopped again (`openarm-up down --drop`, or stop the units and disable the motors). The arms
go limp where they were, which is how they were before.

**Release** (to none). It is refused unless both arms read within 5 degrees of the stow file, the same
tolerance as `openarm-up`. Under ROS 2 it runs `openarm-up down`. Under gafro it stops the units,
listens, and disables the motors (steps 2–4 above), because gafro has no disable of its own.

## What is deliberately not done

- **The ROS 2 park isn't gated by the deadman mid-motion.** The ROS 2 stack has no gate of its own
  (contract §9). The deadman is checked before the park starts, the park takes about 4 s
  (`OPENARM_PARK_SECS`), and the e-stop stops it. The step's text says so. gafro gates its own park
  on CH6 for the whole move.
- **No shell command for the handover.** `openarm-up takeover` (#585) was per arm, which decision 1
  rules out. The steps are listed above for anyone at the robot's computer. `openarm-up` still refuses
  to start or activate beside a gafro unit, or beside any other sender (gap A1).
- **The shows page display is Choreography's** (`choreo.js`). It reads `stack.label` and
  `stack.controller` from the `opendubs-arms` event the Arms page already sends with every status poll,
  so it needs no new poller.

## Tests (by hand; the backend's tests are not in CI)

- `software/kiosk-dashboard/backend/tests/test_arm_handover.py`: the order of each change, the gates,
  a refused park, each rollback, a foreign sender, Release away from stow, and the CONFLICT matrix.
- `tests/test_openarm_service.py`: routing by controller, the stow read, and teleop under gafro.
- `software/openarm/tests/test_stack_exclusion.sh` and `test_motors_senders.py`: the `openarm-up`
  guard, and which CAN frames count as commands.
