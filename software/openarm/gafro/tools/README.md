# Bring-up tools for the arms on the gafro path

What was used to bring the right arm up on 2026-10-23, kept in the repo so the robot gets them with `git pull` and not by copying files. They sit on top of
`bringup.py`, `engage.py` and `guards.py` next door and reuse their gates; none of them re-derives a gate. **Every tool that moves an arm is refused unless**
the existing arm stack is not running, CH5 is MANUAL/AUTO, the e-stop is clear, `--supervised` says a person is at the e-stop with head tracking OFF and the neck
centred, and **CH6 (the deadman) is held**; the permit is renewed only while CH6 and CH5 hold and the e-stop is clear, so letting go stops a move within about half
a second and the arm holds where it is. The arm is left enabled and stiff when a run ends (the driver's `stop()`): the e-stop power cycle releases it.

## Getting them onto the robot, and back

```sh
cd ~/coding/src/opendubs && git pull --ff-only        # on arms/gafro-layer2, the branch of PR #474
```

Nothing is copied by hand. The tools are read from the checkout, so a pull is the whole update; `git log -1` says which version ran. Tools that record data
(`track_hand.py`, `play_show.py`) print a summary and write a CSV under `~/`; paste the summary, or `scp` the CSV back (data only: the code never travels that way).

## Before any run that moves an arm

Arm supported, e-stop in hand, head tracking OFF and the neck centred, the other arm and anything near the path clear, CH5 on MANUAL, nothing driving the base.
In the shell: `export ARM_CODING=~/coding; source software/openarm/gafro/env.sh; export ARM_HW_CONFIG=/etc/opendubs/openarm-gafro-right-only.yaml; export ARM_ASSETS=~/coding/src/openarm_assets`
(the right-only config makes the left arm a `fake` bus: the left arm is out until its shoulder motors are looked at; on 2026-10-29 it ran from its own config with tolerant settings, see [docs/openarm-arm-service.md](../../../../docs/openarm-arm-service.md)).

## The tools

All are run as `python software/openarm/gafro/tools/<tool>.py ...` with `--side right --supervised --model description/openarm/gafro/openarm_bimanual.xml
--hardware-config $ARM_HW_CONFIG --assets $ARM_ASSETS` plus their own arguments. `--help` says the rest. "Typed" is what you type back at the plan.

| Tool | What | Typed | Run on the arm |
|---|---|---|---|
| `arm_angles.sh [right\|left] [s]` | read-only: the seven joint angles, line by line, and `STIFF(n)` or `limp` | | yes |
| `arm_direction_check.sh [joint ...]` | read-only, arm limp: say how to move each joint by hand, report whether its number rose or fell as the model says | | yes: all seven rose on the right arm |
| `goto_stow.py` | position mode to the stow file's pose, from within 25 deg | `stow` | yes |
| `goto_hand.py --offset-cm dx,dy,dz` | hand moved by an offset (model frame: +x forward, +y left, +z up), orientation held; planned with gafro's iLQR (`hand_plan.py`), driven in position mode; prints the largest measured joint torque every 0.5 s and the peaks at the end, and `--max-torque N` (default 12 N m) stops renewing the permit if any joint reaches it | `hand` | yes: 40 mm forward, 4 mm short |
| `track_hand.py --controller velocity` | the same target through a spec-built velocity controller with a pose reference (`--integral` adds a PID term; `--lead S` shows the reference S seconds ahead, S = 1/gain removes the ramp lag of a proportional law). Records ~480 Hz and prints a report: breakaway time, time to halfway and 90 % against the reference, peak lag, overshoot, final error, per-joint peak speed and torque | `hand` | yes: needs `kd` near 5 (see below) |
| `track_hand.py --controller position` | the same target through a spec-built **position** controller (`type: position`): a posture stage that plays iLQR's planned **path**, not the straight line between its ends (so the bend guard does not apply and the twin clearance is checked along the path), the planned joint rate fed forward. The posture law runs on its own anchor (a virtual state its velocity moves, so no actuator is in that loop: `--position-gain` 20), the integrated anchor is the position command, kept within `--leash` (0.15 rad) of the measurement, plus a small offset (`--offset-gain` 2, up to `--max-lead` 0.02 rad) that closes what the actuator did not do. Needs gafro with `PositionIntegratorController` and `JointRateReferenceSource` (not in a wheel built before 2026-10-23); **not yet run on a real arm or in the simulator** | `hand` | no |
| `play_show.py --show S --side right` | one arm's part of a choreography show (the keyframes the runner sends ROS as one goal, along `motion.py`'s smoothed path) played through the position controller, the whole path and its analytic rate given at once; `--slow` (default 2) stretches it. Refuses a show that does not validate against the model's joint limits, an arm not at the path's start (run `goto_stow.py`), a path out of range or over `--max-speed`, a twin clearance under the floor along the whole path; stops on tracking error, torque or vibration. Same gates as `goto_hand`; typed `show`. **Not yet run in the simulator or on a real arm** | `show` | no |
| `limits.py` (library, used by `goto_hand`, `track_hand`, `play_show`) | the joint limits are the model's own: the `range` of each joint in the MJCF, which is also what the controllers' joint-limit stages and the simulator read. What this robot's surroundings allow beyond the arm (cables behind it, a rest, a mount) is written into the model's joint ranges, not a second file; `of_model(system)` gives the `{joint: (lower, upper)}` a show is validated against | | yes: a fake system |
| `make_config.py --base --out --set g.k=v` | a COPY of the hardware config with keys changed, checked against the driver's real key list; never writes the base | | yes |
| `move_probe.py` | `bringup.py` unchanged plus a status line every 0.5 s (joints, torques, permit, deadman, driver status) | as the tool | yes |
| `check_stack.sh` | read-only: is the whole gafro stack on the robot current and does it import (wheels against their remotes, `ssik` native, the driver's features, the ROS 2 bridge's age, services, CAN state) | | yes |
| `can_reply_rates.py rates\|registers` | read-only: how often each motor answers status requests at several rates, and what its registers say; with the arm's service stopped | | yes |
| `can_sniff.py [arm_left\|arm_right] [s]` | read-only: decodes the MIT commands and the replies on the link, flags a joint far from its command (a register-write acknowledgement decodes as -714 deg: not a state) | | yes |
| `move_summary.py [dir]` | reads the `--record-dir` CSVs of the arm service: deviation per joint, how old its feedback was, and a freeze of the whole arm mid-move | | no |

**Arguments.** `--model` defaults to this repository's `description/openarm/gafro/openarm_bimanual.xml`, `--hardware-config` to `$ARM_HW_CONFIG` (what `env.sh`'s `arm_*` functions use) and `--assets` to the repository's own `description/openarm/mjcf`, so `python goto_stow.py --side right --supervised` is a whole command. Give them to use another model, config or checkout.

## What is known (right arm, 2026-10-23)

- Position mode (kp 120/120/60/60/20/20/20, driver gravity feed-forward) is the verified path: holds, moves, lapses, a loaded elbow move, `goto_stow`, `goto_hand`.
  Joints stop up to ~0.01 rad short (joint friction, no integral action): about 4 to 10 mm at the hand.
- Velocity mode sends kp = 0 and the command under kd: at the stock kd 2 the arm stalls completely; with kd [5,5,5,5,2,2,2] (a config copy with `use_model_gains: false`,
  since the driver takes gains from the model by default) the hand moved 34 of 40 mm after ~1 s of dead time; `--integral 5` overshot by 14 mm.
- The torque controller (`engage`/`track`, kp = 0 so its stiffness is a loop closed over CAN at 250 Hz) is unstable at its design natural frequency and too soft below it:
  `torque_probe.py --mode hold` (nothing asked to move; the probe and the torque controllers are removed since) at 40 rad/s ran away within 0.1 s (joint 7 to 6.1 rad/s, tripping the driver; joints 1 and 4 swinging +-5 N m at ~13 Hz);
  at 10 rad/s the hold was quiet (0.10 rad/s) but the park move did not move at all (joint 7 stayed 0.096 rad away, peak torque 0.06 N m). No gain fixes both. The driver gives
  torque mode no gravity feed-forward and a flat `torque_kd` of 0.1; the official ROS driver instead sends {kp, kd, q_ref, dq_ref, tau_ff} so the stiff part is the motor's own PD.
  See `notes/design/arm-mit-feedforward.md`. The torque controllers (`track`, `engage`, `freedrive`), their sequence in engage.py and their probes were removed; every move is in position mode.
- **There are cables behind the right arm** (the model does not have them). Moving the hand backward from stow pushed joint 1 into them: its torque climbed to 0.8 to 1.2 N m and it moved at most 9 mrad, while forward
  from the same pose breakaway took 0.1 to 0.4 N m. `track_hand` now stops renewing the permit when a joint is held (far from its planned angle, moved under 3 mrad in a second, drawing 0.8 N m or more) or the hand is
  far behind its reference and not advancing under load; replayed on the recorded runs it stops the blocked ones at 1.0 s and never fires on the forward ones. Keep backward moves from stow out of the plans:
  a model whose right arm joint 1 `range` has its lower bound just below the stow reading makes the tools refuse them.
- **What the show runner commands (Choreography lane):** one `FollowJointTrajectory` goal per arm, positions only, a point every 0.02 s, to `OpenArmImpedanceController` (`left_controller`/`right_controller`, 400 Hz, the teleop
  author's `openarm_impedance_control`, pinned b5179993). Per joint it writes position `q_ref`, velocity `dq_ref` (the interpolated trajectory's) and effort = the model's gravity torque, and the official plugin sends them as one MIT frame
  with FIXED gains kp 70/70/70/60/10/10/10, kd 2.75/2.5/2.0/2.0/0.7/0.6/0.5 (`control_gains.yaml`). Its guard: the torque estimated from tracking error against 45/45/25/20/15/15/15 N m. It is the same structure as our driver's position
  mode (reference + gravity feed-forward around the motor's PD), softer (our kp is 120/120/60/60/20/20/20) and with a velocity feed-forward ours lacks. Stiffness against an obstruction is the motor's `kp` times the lag, not the controller.
- Velocity tuning (+4 cm forward from stow, kd 5 on joints 1 to 4): the best setting so far is gain 4, `--lead 0.25`, wrist kd 4: breakaway 0.50 s, peak lag 3.2 mm, 0.6 mm from the goal.
- The simulator has no gravity feed-forward in its servo: it cannot judge position-hold sag, velocity mode or torque dynamics. It checks plumbing, plans and clearance.

## Tests

`python3 software/openarm/tests/test_bringup_tools.py` (needs gafro; prints SKIP without it): the refusals, that each tool's gate calls sit in order before the first
permit, and that the torque probe's read-out finds a 6 rad/s spike. They run off the robot.
