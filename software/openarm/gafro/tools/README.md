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
(`torque_probe.py`) print a summary and write a CSV under `~/`; paste the summary, or `scp` the CSV back (data only: the code never travels that way).

## Before any run that moves an arm

Arm supported, e-stop in hand, head tracking OFF and the neck centred, the other arm and anything near the path clear, CH5 on MANUAL, nothing driving the base.
In the shell: `export ARM_CODING=~/coding; source software/openarm/gafro/env.sh; export ARM_HW_CONFIG=/etc/opendubs/openarm-gafro-right-only.yaml; export ARM_ASSETS=~/coding/src/openarm_assets`
(the right-only config makes the left arm a `fake` bus: the left arm is out until its shoulder motors are looked at).

## The tools

All are run as `python software/openarm/gafro/tools/<tool>.py ...` with `--side right --supervised --model description/openarm/gafro/openarm_bimanual.xml
--hardware-config $ARM_HW_CONFIG --assets $ARM_ASSETS` plus their own arguments. `--help` says the rest. "Typed" is what you type back at the plan.

| Tool | What | Typed | Run on the arm |
|---|---|---|---|
| `arm_angles.sh [right\|left] [s]` | read-only: the seven joint angles, line by line, and `STIFF(n)` or `limp` | | yes |
| `arm_direction_check.sh [joint ...]` | read-only, arm limp: say how to move each joint by hand, report whether its number rose or fell as the model says | | yes: all seven rose on the right arm |
| `goto_stow.py` | position mode to the stow file's pose, from within 25 deg | `stow` | yes |
| `goto_hand.py --offset-cm dx,dy,dz` | hand moved by an offset (model frame: +x forward, +y left, +z up), orientation held; planned with gafro's iLQR (`hand_plan.py`), driven in position mode | `hand` | yes: 40 mm forward, 4 mm short |
| `track_hand.py --controller velocity` | the same target through a spec-built velocity controller with a pose reference (`--integral` adds a PID term) | `hand` | yes: needs `kd` near 5 (see below) |
| `make_config.py --base --out --set g.k=v` | a COPY of the hardware config with keys changed, checked against the driver's real key list; never writes the base | | yes |
| `move_probe.py`, `engage_probe.py` | `bringup.py` / `engage.py` unchanged plus a status line every 0.5 s (joints, torques, permit, deadman, driver status) | as the tool | yes |
| `torque_probe.py --mode hold\|park` | `engage.py` unchanged plus a ~480 Hz recorder and a read-out of the first moments; `--nf-scale` scales the controllers' natural frequencies; `--park` is added if missing (without it engage moves to the ready pose) | as engage | yes: see below |

## What is known (right arm, 2026-10-23)

- Position mode (kp 120/120/60/60/20/20/20, driver gravity feed-forward) is the verified path: holds, moves, lapses, a loaded elbow move, `goto_stow`, `goto_hand`.
  Joints stop up to ~0.01 rad short (joint friction, no integral action): about 4 to 10 mm at the hand.
- Velocity mode sends kp = 0 and the command under kd: at the stock kd 2 the arm stalls completely; with kd [5,5,5,5,2,2,2] (a config copy with `use_model_gains: false`,
  since the driver takes gains from the model by default) the hand moved 34 of 40 mm after ~1 s of dead time; `--integral 5` overshot by 14 mm.
- The torque controller (`engage`/`track`, kp = 0 so its stiffness is a loop closed over CAN at 250 Hz) is unstable at its design natural frequency and too soft below it:
  `torque_probe.py --mode hold` (nothing asked to move) at 40 rad/s ran away within 0.1 s (joint 7 to 6.1 rad/s, tripping the driver; joints 1 and 4 swinging +-5 N m at ~13 Hz);
  at 10 rad/s the hold was quiet (0.10 rad/s) but the park move did not move at all (joint 7 stayed 0.096 rad away, peak torque 0.06 N m). No gain fixes both. The driver gives
  torque mode no gravity feed-forward and a flat `torque_kd` of 0.1; the official ROS driver instead sends {kp, kd, q_ref, dq_ref, tau_ff} so the stiff part is the motor's own PD.
  See `notes/design/arm-mit-feedforward.md`. `track_hand.py` refuses `--controller track`; do not run torque-mode `engage` on the arm.
- The simulator has no gravity feed-forward in its servo: it cannot judge position-hold sag, velocity mode or torque dynamics. It checks plumbing, plans and clearance.

## Tests

`python3 software/openarm/tests/test_bringup_tools.py` (needs gafro; prints SKIP without it): the refusals, that each tool's gate calls sit in order before the first
permit, and that the torque probe's read-out finds a 6 rad/s spike. They run off the robot.
