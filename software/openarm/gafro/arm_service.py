#!/usr/bin/env python3
"""The arm service on gafro: what `ros2_control` was to the arms, taken over a slice at a time. Slices 1-2: one arm held, its state on ROS, its lifecycle by command.

    $PY software/openarm/gafro/arm_service.py --side right --hardware-config /etc/opendubs/hardware.yaml --supervised
    ros2 topic echo /joint_states
    $PY software/openarm/gafro/arm_service.py ctl --side right status|activate|park|follow|hold|down|supervise|unsupervise    # to the running service, as `openarm-up` took them
    $PY software/openarm/gafro/arm_service.py ctl --side right activate --move-seconds 5      # this move's time (activate, park); the service's own is `run --move-seconds`, 5 s

The service owns the RobotInterface. It holds the arm where it is (the driver enables and holds on `initialize()`, with no permit, so nothing is commanded)
and publishes `/joint_states` through gafro-ri-ros2-bridge, which is only the ROS side and knows nothing of a permit. The runner and the dashboard can read gafro's
state with no change of their own. At start the bus must be to ourselves (bringup.py's `hold` guard: the existing stack not running, no other sender).

Built so far / next (the order is in notes/design/arm-gafro.md):
  1. one arm held, `/joint_states`.
  2. lifecycle: `ctl activate` takes the arm to `ready` and `ctl park` to the stow file's pose, as engage.py's checked straight line but in POSITION mode (engage.py's own torque
     controller does not move this arm), `ctl hold` stops what is running (the permit
     lapses, the arm holds where it is), `ctl down` ends the service and is refused unless the arm is at stow (`--drop` to override). Every command that moves the arm
     is gated as engage.py is: `--supervised` at start, CH5 / e-stop / CH6 held at the command and before every permit renewal, the arm starting near stow or ready.
     The commands go over a unix socket per arm (arm_client.py; `--socket-mode` 600 by default), so ROS is not needed to run them. The dashboard's park, hold, activate and
     down buttons use the same socket (kiosk-dashboard/backend/openarm.py) and have no other way to move an arm: with the service in a directory shared with the dashboard's
     user (`OPENDUBS_ARM_SOCKET_DIR`, `--socket-mode 660`) they work, and with no service they say so.
  3. `FollowJointTrajectory` on `/<side>_controller/follow_joint_trajectory` (ros_actions.py, over trajectory.py), so the show runner plays on gafro unchanged:
     the same gate and stops as a lifecycle command, and `ctl hold` cancels it. 4. `GripperCommand` on `/<side>_gripper_controller/gripper_cmd` (gripper.py), position in finger metres, a close that stops on something aborts as stalled, and the finger
     joints on `/joint_states`; it runs beside a trajectory, so the permit is one shared keeper (SharedPermit), not one per command. 5. live pose targets (this too, `ctl follow`: pose_follow.py runs the `follow_pose` position controller of controllers.yaml on `<side>/target`, PoseStamped in, `<side>/status` out;
     needs the arm near `ready` on a real bus, ends on `hold`, on a withheld permit, or on the torque / vibration stops). Then teleop, which is not in this repo: the WebXR bridge
     is `openarm.demos.real_teleop_demo` in the container image, against the impedance controllers; it moves onto `<side>/target` or this service's own input. 6. the left arm, then the container goes.

Two differences from `openarm-up`, both the driver's: there is no limp (the driver exposes no disable, so the motors stay enabled and stiff while the service runs and
after it ends: the e-stop power cycle releases them), and starting the service already enables them, where `openarm-up` started with the arms inactive.
`--backend mujoco` runs the same on the simulator. The joint names are the model's; that they equal the old stack's (`openarm_right_joint1`...) is not checked.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import sys
import threading
import time

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bringup  # noqa: E402
import engage  # noqa: E402
import arm_client  # noqa: E402
import gripper  # noqa: E402
import guards  # noqa: E402
import pose_follow  # noqa: E402
import trajectory  # noqa: E402
from arm_client import send, socket_for  # noqa: E402,F401  -- `ctl`, and the tests
from engage import DEFAULT_MODEL, EngageRefused, PermitKeeper, permit_groups  # noqa: E402

# How long `activate` and `park` take, seconds, whatever the distance. Stow to ready (joint 4 travels 1.8 rad, the most) peaks at 1.875 * 1.8 / 5 = 0.68 rad/s. Measured on the
# arm (2026-10-26): 11.2 s and 7 s track within 10 mrad, 5 s within 11 mrad, and 3 s does not: the path asks 1.15 rad/s and the driver tops out at 1.0, so the arm lags by up to
# 79 mrad. trajectory.Limits therefore refuses a path over 1.0 rad/s (stow to ready: under about 3.4 s), so a time the arm cannot follow is refused, not played.
MOVE_SECONDS = 5.0
COMMANDS = ("status", "activate", "park", "follow", "hold", "down", "supervise", "unsupervise")


def line_fraction(q, a, b, tolerance):
    """How far along the straight line from `a` to `b` the arm `q` is (0 to 1), if every joint is within `tolerance` of that line, else None. The stow to ready line is the one the
    moves travel and the check has cleared, so an arm stopped on it can go on from there; None when a pose is missing."""
    if a is None or b is None:
        return None
    q, a, b = (np.asarray(x, dtype=float) for x in (q, a, b))
    span = b - a
    if not np.any(span):
        return None
    fraction = float(np.clip(np.dot(q - a, span) / np.dot(span, span), 0.0, 1.0))
    return fraction if float(np.abs(q - (a + fraction * span)).max()) <= tolerance else None


class SharedPermit:
    """One permit keeper for the arm, shared by whatever moves it at the moment (a lifecycle command, a trajectory, a gripper command): started by the first lease, stopped (the
    permit revoked) when the last ends, and revoked at once by `revoke` (`hold`). Without it a gripper cue finishing in the middle of a show would revoke the arm's permit."""

    class Lease:
        def __init__(self, shared):
            self.shared, self.epoch, self.held = shared, None, False

        def start(self):
            self.epoch = self.shared._acquire()
            self.held = True
            return self

        def stop(self):
            if self.held:
                self.held = False
                self.shared._release(self.epoch)

        __enter__ = start

        def __exit__(self, *exc):
            self.stop()

    def __init__(self, groups, gate):
        self.groups, self.gate = groups, gate
        self.keeper, self.users, self.epoch = None, 0, 0
        self.lock = threading.Lock()

    def lease(self):
        return self.Lease(self)

    def _acquire(self):
        with self.lock:
            if self.users == 0:
                keeper = PermitKeeper(self.groups, allowed=self.gate)
                keeper.start()                 # the gate answers here; Refused if CH5, the e-stop or CH6 say no
                self.keeper = keeper
            self.users += 1
            return self.epoch

    def _release(self, epoch):
        with self.lock:
            if epoch != self.epoch:
                return                         # revoked since: already counted out
            self.users -= 1
            if self.users == 0 and self.keeper is not None:
                self.keeper.stop()
                self.keeper = None

    def revoke(self):
        """Stop renewing and revoke now, for every lease."""
        with self.lock:
            if self.keeper is not None:
                self.keeper.stop()
                self.keeper = None
            self.users, self.epoch = 0, self.epoch + 1

    @property
    def active(self):
        return self.users > 0

    @property
    def withheld(self):
        """How many renewals the gate has refused since the keeper started (0 with no keeper)."""
        return self.keeper.withheld if self.keeper is not None else 0


class ArmService:
    """One arm's lifecycle. Commands that move the arm run one at a time on their own thread; `hold` and `status` answer at once."""

    def __init__(self, robot, args):
        self.robot, self.args = robot, args
        self.groups = permit_groups(args.side) if args.backend == "hardware" else ()
        self.real = False
        if args.backend == "hardware":
            _, self.real = guards.buses_of(args.hardware_config, self.groups)
        self.source = None             # the pose reference `<side>/target` feeds (set by `run` when ROS is up)
        self.reach = None              # the arm's reach (reach.py), where ssik and self_motion_charts are installed: a target outside it is not followed
        self.behaviour = ""            # what `follow` is doing about its target now ("following", "holding: ..."), while it runs
        self.running = None            # the name of the command in progress (a gripper command does not count: it runs beside the others)
        self.permit = SharedPermit(self.groups, guards.motion_gate(args.dashboard) if self.real else None)
        self.last = ""                 # how the last command ended
        self.cancel = threading.Event()   # ends a trajectory in progress
        self.grip_cancel = threading.Event()   # ends a gripper command in progress
        self.quit = threading.Event()
        self.lock = threading.Lock()

    # -- what a command may start from ---------------------------------------------------------------------------------------------------

    def _stow(self):
        return engage.stow_pose(self.args.side, self.args.stow_conf)

    def _position(self):
        indices = np.asarray(self.robot.get_system().get_task_space(self.args.side).get_joint_indices())
        return np.asarray(self.robot.get_state().get_position(), dtype=float)[indices]

    def _gate(self):
        """What a moving command needs on a real bus, before it grants anything: the same as engage.py's. Returns (known_starts, tolerance, gate)."""
        if not self.real:
            return None, None, None
        guards.require_motion_permitted(self.args.supervised, self.args.dashboard, deadman=True)
        stow = self._stow()
        if stow is None:
            raise guards.Refused(f"no stow pose configured in {self.args.stow_conf or engage.STOW_CONF}: a move on a real bus starts near stow or ready")
        return {"stow": stow}, float(np.radians(self.args.start_tolerance_deg)), guards.motion_gate(self.args.dashboard)

    # -- commands ------------------------------------------------------------------------------------------------------------------------

    def start(self, name, move_seconds=None, allow_large=False):
        """Run `activate`, `park` or `follow` on a thread; the answer is whether it was started. `move_seconds` (activate, park): this move's time, instead of the service's."""
        if move_seconds is not None and not (name in ("activate", "park") and 0.0 < move_seconds < float("inf")):
            return f"refused: a move time is a positive number of seconds for activate or park, not {move_seconds!r} for {name}"
        with self.lock:
            if self.running:
                return f"refused: {self.running} is running (`hold` stops it)"
            if name == "follow" and self.source is None:
                return "refused: no pose reference to follow (the service runs without ROS, or has not registered `<side>/target`)"
            try:
                known, tolerance, gate = self._gate()
                if name == "follow" and self.real:
                    ready = np.asarray(self.robot.get_system().get_keyframe("ready").qpos, dtype=float)
                    indices = np.asarray(self.robot.get_system().get_task_space(self.args.side).get_joint_indices())
                    guards.require_known_start(self._position(), {"ready": ready[indices]}, tolerance)     # the pose stage from a singular stow would swing the arm
            except guards.Refused as refused:
                return f"refused: {refused}"
            self.cancel.clear()
            self.running = name
        threading.Thread(target=self._run, args=(name, known, tolerance, gate, move_seconds, allow_large), daemon=True).start()
        return f"{name} started"

    def _run(self, name, known, tolerance, gate, move_seconds=None, allow_large=False):
        side, model = self.args.side, self.args.model
        try:
            if name == "follow":
                with open(os.path.join(HERE, "controllers.yaml")) as stream:
                    controllers = yaml.safe_load(stream)["controllers"]
                self.behaviour = ""
                self.last = "following: " + pose_follow.follow(self.robot, side, controllers, self.source, self.permit.lease(), self.cancel, lambda: self.permit.withheld,
                                                               log=self._log, reach=self.reach, status=self._behaviour)
                return
            self._move(name, known, tolerance, move_seconds, allow_large)
            self.last = f"{name} done"
        except (guards.Refused, EngageRefused) as refused:
            self.last = f"{name} stopped: {refused}"
        except Exception as error:  # noqa: BLE001 -- the arm holds; the service stays up to say so
            self.last = f"{name} failed: {type(error).__name__}: {error}"
        finally:
            self.running = None
        print(self.last, flush=True)

    def _recording(self, name):
        """Where this move's samples go (a CSV), or None: `--record-dir`."""
        directory = getattr(self.args, "record_dir", None)
        return os.path.join(directory, f"arm_service-{self.args.side}-{name}-{time.strftime('%Y%m%d-%H%M%S')}.csv") if directory else None

    def _move(self, name, known, tolerance, move_seconds=None, allow_large=False):
        """`activate` (to the model's `ready`) or `park` (to the stow file's pose) as a straight minimum-jerk line in joint space, in POSITION mode.

        The line is engage.py's, checked against the collision geometry as engage.py checks it, but it is played by trajectory.follow (the position controller of
        `play_show`, which passed on the arm) and NOT by engage.py's torque controller: that one does not move this arm (tools/README.md, "What is known"; on the Thor
        it clicked and stayed put). Raises EngageRefused with the reason; the arm then holds."""
        robot, side = self.robot, self.args.side
        system = robot.get_system()
        indices = np.asarray(system.get_task_space(side).get_joint_indices())
        start_all = np.asarray(robot.get_state().get_position(), dtype=float).copy()
        start = start_all[indices]
        ready = system.get_keyframe("ready")
        if name == "park":
            target, label = self._stow(), "stow"
            if target is None:
                raise EngageRefused(f"no stow pose configured in {self.args.stow_conf or engage.STOW_CONF} (a line `{side.upper()}_STOW=<7 angles in degrees>`)")
        else:
            if ready is None:
                raise EngageRefused("the model has no keyframe 'ready'")
            target, label = np.asarray(ready.qpos, dtype=float)[: len(start_all)][indices], "ready"
        if tolerance is not None:       # a real bus: the collision check has no head in it, so a move starts only where the line is known to be clear
            starts = dict(known or {})
            if ready is not None:
                starts["ready"] = np.asarray(ready.qpos, dtype=float)[: len(start_all)][indices]
            try:
                self._log(f"the arm starts near {guards.require_known_start(start, starts, tolerance)}")
            except guards.Refused as refused:
                along = line_fraction(start, starts.get("stow"), starts.get("ready"), tolerance)
                if along is not None:
                    self._log(f"the arm is on the stow-ready line, {along:.0%} of the way from stow: the line is known to be clear")
                elif allow_large:
                    self._log("--allow-large: the arm is not near stow, ready or the line between them; the check has no head in its model, so the path is yours to judge by eye")
                else:
                    raise EngageRefused(f"{refused} Or pass --allow-large, with a person at the e-stop, to move from here anyway.") from None
        lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
        upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
        if np.any(target < lower) or np.any(target > upper):
            raise EngageRefused(f"the {label} pose is outside the {side} arm's joint limits")
        end_all = start_all.copy()
        end_all[indices] = target
        refusal = engage.PathCheck(self.args.model, [a.joint_name for a in system.get_actuators()]).refusal(start_all, end_all)
        if refusal:
            raise EngageRefused(f"the straight line to {label} is not clear: {refusal}")

        duration = float(move_seconds if move_seconds is not None else getattr(self.args, "move_seconds", MOVE_SECONDS))
        self._log(f"{name} {side}: {np.round(start, 2)} -> {np.round(target, 2)} in {duration:.1f} s, in position mode")
        times = np.linspace(0.0, duration, 41)
        fraction = times / duration
        shape = fraction**3 * (10.0 - 15.0 * fraction + 6.0 * fraction**2)
        slope = 30.0 * fraction**2 * (1.0 - fraction) ** 2 / duration
        result = trajectory.follow(robot, side, times, start + np.outer(shape, target - start), np.outer(slope, target - start), self.permit.lease(), self.cancel,
                                   log=self._log, record=self._recording(name))
        if result.code != trajectory.SUCCESSFUL or result.cancelled:
            raise EngageRefused(result.message)

    def follow(self, times, positions, velocities=None):
        """Play a joint trajectory (the show runner's goal) and return a trajectory.Result. Refused while anything else runs; gated as `activate` is, and the gate is asked
        again before every permit renewal. Blocks until the path ends, is cancelled (`hold`) or is stopped."""
        with self.lock:
            if self.running:
                return trajectory.Result(trajectory.INVALID_GOAL, f"{self.running} is running")
            try:
                _, _, gate = self._gate()
            except guards.Refused as refused:
                return trajectory.Result(trajectory.INVALID_GOAL, str(refused))
            self.running = "trajectory"
            self.cancel.clear()
        try:
            result = trajectory.follow(self.robot, self.args.side, times, positions, velocities, self.permit.lease(), self.cancel, log=self._log,
                                       record=self._recording("trajectory"))
        except Exception as error:  # noqa: BLE001 -- the arm holds; the goal fails and the service stays up
            result = trajectory.Result(trajectory.INVALID_GOAL, f"{type(error).__name__}: {error}")
        finally:
            self.running = None
        self.last = f"trajectory: {result.message}"
        return result

    def grip(self, metres):
        """Move the gripper's finger to `metres` (a gripper.Result). Runs beside an arm command: the permit is shared. Gated as the others are."""
        try:
            self._gate()
        except guards.Refused as refused:
            return gripper.Result(message=str(refused))
        self.grip_cancel.clear()
        try:
            result = gripper.grip(self.robot, self.args.side, metres, self.permit.lease(), self.grip_cancel)
        except Exception as error:  # noqa: BLE001 -- the gripper holds; the goal fails and the service stays up
            result = gripper.Result(message=f"{type(error).__name__}: {error}")
        self.last = f"gripper: {result.message}"
        return result

    def _behaviour(self, text):
        self.behaviour = text

    def _log(self, text):
        print(text, flush=True)

    def hold(self):
        """Stop renewing the permit and revoke it: whatever is running ends, and the arm holds where it is."""
        active = self.permit.active
        self.cancel.set()
        self.grip_cancel.set()
        self.permit.revoke()
        return "holding" if active else "nothing was running; the arm is held"

    def down(self, drop=False):
        if self.running:
            return f"refused: {self.running} is running (`hold` first)"
        stow = self._stow()
        near = stow is not None and float(np.abs(self._position() - stow).max()) <= np.radians(self.args.start_tolerance_deg)
        if not (near or drop or not self.real):
            return ("refused: the arm is not at its stow pose, and the motors stay stiff after the service ends, so an e-stop there lets it fall. "
                    "`park` first, or `down --drop` to end anyway")
        self.quit.set()
        return "down"

    def state(self):
        """What the arm service is doing, for the topic and the viewer: {state: held | <the running command>, supervised: yes | no, last: how the last command ended}."""
        return {"state": self.running or "held", "supervised": "yes" if self.args.supervised else "no", "last": self.last or "none",
                "behaviour": self.behaviour if self.running == "follow" else ""}

    def status(self):
        q = self._position()
        return (f"{self.args.side} arm, {self.args.backend}: " + (f"{self.running} running" if self.running else "held") + f"; last: {self.last or 'none'}\n"
                + "joints (deg) " + " ".join(f"{np.degrees(v):+.1f}" for v in q) + f"\nsupervised: {'yes' if self.args.supervised else 'no'}")

    def handle(self, request):
        command = request.get("command")
        if command == "status":
            return self.status()
        if command in ("activate", "park", "follow"):
            return self.start(command, request.get("move_seconds"), bool(request.get("allow_large")))
        if command in ("supervise", "unsupervise"):
            self.args.supervised = command == "supervise"
            return ("supervised: a person is at the e-stop (head tracking off, neck centred); moving commands are allowed while CH5, the e-stop and CH6 permit"
                    if self.args.supervised else "unsupervised: moving commands are refused on a real bus")
        if command == "hold":
            return self.hold()
        if command == "down":
            return self.down(bool(request.get("drop")))
        return f"refused: unknown command {command!r} (one of {', '.join(COMMANDS)})"


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            reply = self.server.service.handle(json.loads(self.rfile.readline()))
        except Exception as error:  # noqa: BLE001
            reply = f"refused: {type(error).__name__}: {error}"
        self.wfile.write((reply + "\n").encode())


def listen(service, path, mode=0o600):
    """Serve the commands on a unix socket only its owner can use; returns the server (shut it down to stop)."""
    if os.path.exists(path):
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(path)
            raise RuntimeError(f"another arm service is listening on {path}")
        except ConnectionRefusedError:
            os.unlink(path)    # left behind by one that died
        finally:
            probe.close()
    server = socketserver.ThreadingUnixStreamServer(path, _Handler)
    os.chmod(path, mode)
    server.service = service
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode")
    ctl = sub.add_parser("ctl", help="send a command to the running service")
    ctl.add_argument("command", choices=COMMANDS)
    ctl.add_argument("--drop", action="store_true", help="down: end although the arm is not at stow")
    ctl.add_argument("--move-seconds", type=float, default=None, help="activate, park: this move's time in seconds, instead of the service's --move-seconds")
    ctl.add_argument("--allow-large", action="store_true", help="activate, park: move even though the arm is not near stow, ready or the line between them (a person at the e-stop judges the path)")
    ctl.add_argument("--side", choices=("left", "right"), default="right")
    ctl.add_argument("--socket", default=None, help="default: the side's")
    run = sub.add_parser("run", help="run the service (the default)")
    for p in (parser, run):
        p.add_argument("--side", choices=("left", "right"), default="right")
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
        p.add_argument("--hardware-config", default="")
        p.add_argument("--state-rate", type=float, default=100.0, help="Hz of /joint_states")
        p.add_argument("--supervised", action="store_true", help="a person is at the e-stop, head tracking off and the neck centred (a moving command needs it on a real bus)")
        p.add_argument("--dashboard", default=guards.DASHBOARD, help="where CH5, the e-stop and CH6 are read")
        p.add_argument("--stow-conf", default=None)
        p.add_argument("--start-tolerance-deg", type=float, default=float(np.degrees(guards.START_TOLERANCE)))
        p.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS, help="how long to listen for another sender")
        p.add_argument("--socket", default=None, help="default: the side's, in $OPENDUBS_ARM_SOCKET_DIR (one service per arm)")
        p.add_argument("--socket-mode", default="600", help="the socket's permissions, octal: 660 lets the dashboard's user (the directory's group) send commands")
        p.add_argument("--move-seconds", type=float, default=MOVE_SECONDS, help="how long `activate` and `park` take, whatever the distance (default 5 s from stow to ready; under about 3.4 s the arm cannot follow and the move is refused)")
        p.add_argument("--record-dir", default=None, help="write each move's samples (activate, park, a trajectory) there as a CSV, to see how it tracked")
        p.add_argument("--no-ros", action="store_true", help="no /joint_states or action server: the lifecycle only (they need ROS)")
    args = parser.parse_args()

    args.socket = args.socket or socket_for(args.side)
    if args.mode == "ctl":
        try:
            print(send(args.command, args.drop, args.socket, args.move_seconds, args.allow_large))
        except OSError as error:
            print(f"no arm service at {args.socket} ({error})")
            return 1
        return 0

    if bringup.gate(args, motion=False):    # the bus must be to ourselves; nothing moves until a command, which is gated again
        return 1

    robot, _, _ = bringup.connect(args)
    service = ArmService(robot, args)
    server = listen(service, args.socket, int(args.socket_mode, 8))
    try:
        print(f"holding the {args.side} arm; commands: `arm_service.py ctl {{{','.join(COMMANDS)}}}`", flush=True)
        if args.no_ros:
            service.quit.wait()
        else:
            from gafro_ri_ros2_bridge import node
            import ros_actions

            ros_actions.serve_trajectory(service)    # /<side>_controller/follow_joint_trajectory, on its own node and thread
            service.source = pose_follow.reference_source(robot, args.side)
            node.serve(robot, source=service.source, name=args.side,
                       bridge_settings={"node_name": f"openarm_{args.side}", "state_rate": args.state_rate, "publish_state": False},     # the state is ros_actions.serve_state's
                       running=lambda: not service.quit.is_set())     # `<side>/target` (PoseStamped) in, `<side>/status` out
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        if os.path.exists(args.socket):
            os.unlink(args.socket)
        service.hold()
        robot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
