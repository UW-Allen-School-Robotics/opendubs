# The torque controller as a feed-forward around the motor's PD

**Status:** proposal, written 2026-10-23 from the right-arm bring-up on opendubs2. Not built; nothing here changes behaviour. **Decision wanted from:** the Arms lane and
the owner of `openarm-driver` and gafro's `ControllerBase`, because it adds a command type that crosses all three.

## What was measured

Right arm, driver at `kp 120/120/60/60/20/20/20`, `kd 2/2/1.5/1.5/0.5/0.5/0.5`, all through the gates of `software/openarm/gafro/tools/`.

| Path | Result |
|---|---|
| Position mode (the motor's MIT PD, driver gravity feed-forward) | Holds, moves, lapses, a loaded elbow move (1.56 N m), `goto_stow`, and a hand moved by an iLQR plan (36 of 40 mm). Stops about 0.01 rad short: joint friction (~0.6 N m at joint 3, kp 60) over the firmware stiffness. |
| Velocity mode (kp = 0, command under kd) | Stock `kd 2`: a complete stall. `kd 5` on joints 1 to 4: moves, after about 1 s of dead time. |
| Torque mode, `engage` posture controller, natural frequency 40 rad/s | **Unstable.** `torque_probe.py --mode hold` (nothing asked to move): the handover gives joint 1 a first ~1 N m step, then every joint enters a growing oscillation (joints 1 and 4 in phase at ~13 Hz, torque +1.2, -1.0, -2.3, -4.1, -5.1, +4.3 N m; joint 7 faster, to 6.35 rad/s) until the driver's speed trip holds the arm. |
| Torque mode at 10 rad/s (`--nf-scale 0.25`) | The hold is quiet (fastest joint 0.10 rad/s) but the **park does not move**: joint 7 stays 0.096 rad away and its largest torque is 0.06 N m in 3.7 s. |

## Why (inferred, not measured)

In a torque frame the driver sends `kp = 0, kd = torque_kd (0.1)`, so all the stiffness is a loop closed **over CAN at 250 Hz**: sensing, the round trip, the controller, and the
driver's torque slew sit inside it. Its stiffness is inertia times the squared natural frequency, so it is also tiny on a light joint: with the wrist's "few thousandths" kg m²
(the estimate in `controllers.yaml`) it is 0.3 N m/rad at 10 rad/s and 4.8 at 40, against a friction of the order of 0.5 N m. The delay limits how high the frequency can go
(unstable at 40, so somewhere below it), and the friction needs a high one. In position mode the stiffness is the motor's own PD, evaluated in the drive at its own rate with no
CAN delay in that inner loop: that is why it is stable at `kp 20` on the same wrist, a stiffness of 20 N m/rad. The inertia and friction figures are estimates; the instability
and the stall are measurements.

The official ROS driver never closes a loop this way. `openarm_ros2` (`openarm_hardware`, pinned `c15982d9`) sends, every cycle and for every joint, one MIT frame
`{kp, kd, pos_cmd, vel_cmd, tau_cmd}` with `kp`/`kd` fixed per joint (`control_gains.yaml`: kp 70, 70, 70, 60, 10, 10, 10; kd 2.75, 2.5, 2.0, 2.0, 0.7, 0.6, 0.5), so a controller's
torque is a feed-forward around a firmware PD.

## Proposal

Add a fourth command, **impedance**: `{q_ref, dq_ref, tau_ff}`, applied as the MIT frame `{kp_j, kd_j, q_ref, dq_ref, tau_ff}` with the gains the driver already has for
position mode. A controller then says where the joint should be, how fast, and what torque the model says that takes; the motor closes the error.

- **Gravity.** Position and velocity frames already carry the driver's gravity feed-forward (`makeCommands`: `kind != TORQUE`). Proposed: impedance carries it too, and `tau_ff` is
  the dynamic part only (`M(q) qdd + C(q, qd) qd`), so a controller that asks for nothing holds the arm up exactly as position mode does. *Open: the other split, with `tau_ff`
  the whole torque, is also coherent; it makes the controller responsible for gravity again.*
- **Limits.** Unchanged: the position range and margin, the velocity clamp, `tau_ff` clamped to `torque_fraction` of the motor's limit and slewed at `torque_slew`, the per-joint
  trips, the permit, and (for the bring-up tools) the deadman. A frame is built only from a fresh, permitted command, as now.
- **Transition.** Position to impedance changes no structure: the frame at the handover equals the hold frame plus `tau_ff`. The step that started the growing oscillation above
  (the hold's firmware stiffness replaced by a CAN loop) does not exist.

### Where it lands

| Layer | Change |
|---|---|
| gafro core (`src/gafro/control/ControllerBase.hpp`) | `JointImpedance<double>` as a role type; a fourth alternative of `JointCommand`; `CommandMode<JointImpedance>`; an `Actuator::Mode` for it (today `NONE, POSITION, VELOCITY, FORCE, TORQUE`); the python binding. |
| `gafro_robot_interface` | carry the alternative across the boundary (three vectors, not one) and let an actuator declare the mode. |
| `openarm-driver` | `Kind::IMPEDANCE` in `makeCommands`, `matchesMode`, and tests on the fake bus. No new configuration keys. |
| controllers | one that follows a planned trajectory: iLQR on `DoubleIntegratorSystem` (state `[q; qd]`, control `qdd`) gives `q, qd, qdd`; `tau_ff = system.compute_inverse_dynamics(q, qd, qdd)` less gravity. The hand-pose planning of `tools/hand_plan.py` is the front half. |

### Slices, each shippable and green before the next

1. gafro core: the type, the variant alternative, the mode, binding tests. No behaviour change anywhere else.
2. RI carries it; a fake-backend test that an impedance command reaches the driver intact.
3. Driver: the frame, clamps and slew, fake-bus tests (the cases that exist for position mode, plus: `tau_ff` alone moves a loaded fake joint; a zero `tau_ff` equals position hold).
4. The trajectory-tracking controller, in the simulator against position mode (the sim has no gravity feed-forward in its servo, so it can judge tracking, not sag).
5. The arm, in the tools' usual ladder under the same gates: an impedance hold with `tau_ff = 0` against the position hold; a small move; then the same hand move as `goto_hand` with and without `tau_ff`.

## What it does not fix

- The friction dead band (about 0.01 rad at the joints) stays: the firmware stiffness sets it. Feed-forward improves dynamic tracking, not stiction.
- Velocity mode still wants `kd` near 5. This does not change velocity mode.
- Position mode already gives pose control (`tools/goto_hand.py`); this adds dynamics feed-forward on top of it, which only matters for fast moves.

## Alternatives considered

- **Keep torque mode and tune.** Measured: unstable at 40 rad/s, stalled at 10.
- **Raise `torque_kd`.** Damping adds no stiffness, and a large `kd` on a light wrist is itself delay-unstable (`kd / I` times the delay).
- **A faster or delay-compensated loop.** Unquantified gain, and it leaves the stiffness limited by what the host can close; the firmware route removes the host from the inner loop.
- **Only position mode.** Fine for slow moves, and the tools do this today; it leaves no way to apply a planned feed-forward torque.

## Open questions

- The gravity split above.
- Whether impedance gains should be separate from position mode's (`kp`/`kd` are one set today; the official driver uses lower `kp` on joints 1 and 2, 70 against our 120).
- Whether `openarm_impedance_control` (the lane's teleop package) already holds a decision about this: its gains and command interface were not read.
- The real wrist inertia and friction, which would turn the estimates above into numbers: a system-identification pass on the arm.

Evidence: `software/openarm/gafro/tools/README.md`, `torque_probe.py`; raw samples of the two hold runs and the park run are `~/torque_probe-20261023-184637.csv`, `-184845.csv` and
`-185040.csv` on opendubs2.
