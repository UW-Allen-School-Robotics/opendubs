"""The gafro backend's arm calls through Archit's trick-or-treat ROS 2 services (archit/treat-follow-ros:
software/perception/tools/treat_services.py, TREAT_SERVICES.md), not straight to the arm service.

gafro_robot.GafroRobot plans every move on the twin, as before; only how a goal reaches the arm changes:

    ready             /perception/treat/ready          the arm service's own `ready`
    a planned move    /perception/treat/move_joints    {"joints": the planned goal, radians}: a straight joint line,
                                                       checked for self-collision by the node, then played by the arm
                                                       service's follow_joint_trajectory. The twin's plan is a straight
                                                       joint line too, so the path is the one that was checked
    the gripper       /perception/treat/gripper_open   the arm service's open_gripper / close_gripper: fully open or
                      /perception/treat/gripper_close  shut, nothing between. A close that stops on something holds it;
                                                       one shut on nothing is reported, not an error (a gripper closed
                                                       to travel), so the program decides what a miss means
    the hand-off      get_hand or find_hand, plan_treat, move_joints, hold, gripper_open, ready   (TreatRobot)
    the whole visit   Archit's treat_sequence.py, run as it is (treat_sequence(), below): ready, the candy from the
                      bucket, his line on the Jabra, find_hand (his node runs the head), the hand-off, ready
    a cancel          /perception/treat/stop           the arm holds where it is

The node keeps its own gates (on the real arm: CH5, the e-stop and the deadman for every motion, a lost permit
latching a stop until `rearm`), and GafroRobot keeps its own (the deadman read before and during every call).
The services live on their own ROS domain (`domain`): the robot's (0) on the real arm, a scratch one beside a
simulated arm. /joint_states, the head and the hand still come from GafroRos on the arm's domain.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import threading
import time

PREFIX = "/perception/treat/"
SERVICES = ("ready", "move_joints", "gripper_open", "gripper_close", "get_hand", "find_hand", "plan_treat",
            "hold", "stop")
SUCCESS, NOT_FOUND = 0, 5
TRAVEL_M = 0.044                     # the finger's full travel (gafro/gripper.py TRAVEL)
OPEN_FROM = 0.5                      # an opening from this up is "open"; below it, "closed"
SPEED = 0.4                          # rad/s, the fastest joint's peak in move_joints (the node's default)
#: Archit's sequence (software/perception/tools/treat_sequence.py on daphne/treat-gripper), mounted by dev.sh
SEQUENCE = os.environ.get("OPENDUBS_ARCHITECT_TREAT_SEQUENCE", "/treat/software/perception/tools/treat_sequence.py")
#: Its exit codes (its docstring): 0 done, 1 a step failed, 2 no hand held out, 3 nobody found, 4 stopped or the
#: deadman not held, 130 interrupted (it calls `stop`)
NO_HAND, NOBODY, STOPPED, INTERRUPTED = 2, 3, 4, 130


class R:
    """A result in the shape GafroRobot reads from the arm service's own actions."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class Call:
    """One service call as a goal handle: done(), result(), cancel() (the node's `stop`)."""

    def __init__(self, calls, name: str, req: dict, convert):
        self.calls, self.name, self.convert = calls, name, convert
        self.future = calls.client(name).call_async(calls.Request(req=json.dumps(req)))

    def done(self) -> bool:
        return self.future.done()

    def result(self):
        r = self.future.result()
        data = json.loads(r.data) if r.data else {}
        return self.convert(r.result_code.result_code, r.result_code.message, data)

    def cancel(self) -> None:
        self.calls.stop()


class TreatCalls:
    """The services, on a ROS context of their own on `domain`; `arm` (GafroRos) answers everything else."""

    def __init__(self, arm, domain: int, speed: float = SPEED):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from robot_api_interfaces.srv import RobotCommand
        self.arm, self.speed, self.Request = arm, float(speed), RobotCommand.Request
        self.ctx = rclpy.Context()
        rclpy.init(context=self.ctx, domain_id=int(domain))
        self.node = rclpy.create_node("architect_treat_calls", context=self.ctx)
        self.clients = {n: self.node.create_client(RobotCommand, PREFIX + n) for n in SERVICES}
        ex = SingleThreadedExecutor(context=self.ctx)
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True).start()

    def __getattr__(self, name):                 # joints_deg, head_now, alive, ...: the arm's own topics
        return getattr(self.arm, name)

    def client(self, name):
        return self.clients[name]

    def wait_for_servers(self, timeout_s: float = 10.0) -> list:
        end = time.monotonic() + timeout_s
        missing = list(SERVICES)
        while missing and time.monotonic() < end:
            missing = [n for n in missing if not self.clients[n].service_is_ready()]
            time.sleep(0.1)
        return [PREFIX + n for n in missing] + self.arm.wait_for_servers(timeout_s)

    def call(self, name: str, req: dict, convert=lambda code, msg, data: (code, msg, data)):
        return Call(self, name, req, convert)

    def stop(self) -> None:
        self.clients["stop"].call_async(self.Request(req="{}"))

    # ---- what GafroRobot sends ------------------------------------------------------------------
    def command(self, side: str, name: str, params: str):
        if name != "ready":
            raise ValueError(f"the treat services run `ready` only, not {name!r}")
        return self.call("ready", {}, lambda code, msg, data: R(result_code=code, message=msg))

    def trajectory(self, side: str, points):
        """GafroRobot's planned move ([(seconds, 7 signed degrees)], the model's radians): its goal, as one
        move_joints. The node times it itself (minimum jerk at `speed`)."""
        goal = [math.radians(d) for d in points[-1][1]]
        return self.call("move_joints", {"joints": goal, "speed": self.speed},
                         lambda code, msg, data: R(error_code=0 if code == SUCCESS else -1, error_string=msg))

    def grip(self, side: str, metres: float):
        closing = metres < OPEN_FROM * TRAVEL_M

        def convert(code, msg, data):
            mm = data.get("position_mm")
            at = None if mm is None else float(mm) / 1000.0
            if code == SUCCESS:
                held = bool(data.get("holding"))
                return R(reached_goal=not held, stalled=held, position=at if at is not None else (0.0 if closing else TRAVEL_M),
                         message=msg)
            if closing and "closed on nothing" in msg:
                return R(reached_goal=True, stalled=False, position=0.0, message=msg)   # shut, nothing held
            return R(reached_goal=False, stalled=False, position=at if at is not None else float("nan"), message=msg)
        return self.call("gripper_close" if closing else "gripper_open", {}, convert)


def treat_robot_class():
    """TreatRobot, built on gafro_robot.GafroRobot (imported here: this module loads without the backend)."""
    from opendubs_architect.client import MotionRefused
    from opendubs_architect.gafro_robot import GafroRobot

    class TreatRobot(GafroRobot):
        """GafroRobot over TreatCalls, with the hand-off as Archit's sequence (TREAT_SERVICES.md): a still hand
        (find_hand, which also centres the person with the head, or get_hand without the node's head), plan_treat
        (jaws level, toward the palm), move_joints there, hold, the gripper open, ready."""

        find_hand = False                    # the node runs the head (find_hand) or not (get_hand)
        hold_s = 3.0                         # held at the hand before the gripper opens
        reach_tol_mm = 50.0                  # a plan ending farther than this from its goal is no hand-off: plan_treat
                                             # answers SUCCESS with the nearest pose it found (2026-10-29: 482 mm off, a
                                             # palm too low and too near, and the arm reached up and right, away from it)
        standoff_m = 0.30                    # short of the palm (plan_treat's standoff; on the real arm at least 0.20).
                                             # 2026-10-29: at 0.20 the arm, near straight 80 cm out with a candy, took
                                             # joint 1 to the arm service's 12 N m force limit and stopped 1-2 deg short

        def _service(self, what: str, name: str, req: dict) -> dict:
            code, msg, data = self._run_goal(what, lambda: self.ros.call(name, req))
            if code != SUCCESS:
                raise MotionRefused(f"{what}: {msg}") if code != NOT_FOUND else LookupError(msg)
            return data

        def get_hand_pose(self) -> dict:
            try:
                data = self._service("get_hand", "get_hand", {"timeout_s": 1.0})
            except LookupError:
                return {"hand": None}
            p = data.get("palm")
            return {"hand": {"position": dict(zip("xyz", p)), "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                             "stable": True, "frame": "arm_mount"}} if p else {"hand": None}

        def place_in_hand(self, arm: str, timeout_s: float = 15.0) -> dict:
            side = self._side(arm)
            try:
                if self.find_hand:
                    got = self._service("find_hand", "find_hand", {"find_s": float(timeout_s), "wait_s": 10.0})
                else:
                    got = self._service("get_hand", "get_hand", {"timeout_s": float(timeout_s)})
                plan = self._service("plan_treat", "plan_treat", {"palm": got["palm"], "jaws_level": True,
                                                                  "standoff": self.standoff_m})
                off = float(plan.get("mm_from_goal", 0.0))
                if off > self.reach_tol_mm:
                    raise LookupError(f"the hand at {got.get('palm')} is out of reach: the nearest pose is "
                                      f"{off:.0f} mm from the goal (at most {self.reach_tol_mm:g})")
            except LookupError as e:
                self.move_arm_to_named_pose(side, "ready")
                return {"released": False, "reason": str(e)}
            self.log({"event": "hand", "side": side, "palm": got.get("palm"), "joints": plan.get("joints")})
            self._service("to the hand", "move_joints", {"joints": plan["joints"], "speed": self.ros.speed})
            self._service(f"held {self.hold_s:g} s", "hold", {"seconds": self.hold_s})
            self.set_gripper_width(side, 1.0)
            self.move_arm_to_named_pose(side, "ready")
            return {"released": True, "reason": f"placed at the palm {got.get('palm')}"}

        def treat_sequence(self, say: str = "") -> dict:
            """Archit's whole visit, his script run as it is on the services' domain: its lines go to the log, a stop
            here is his Ctrl-C (he calls `stop`; the arm holds). Nobody or no hand: released False, the arm at ready
            with the candy; anything else that fails raises."""
            self._permit("treat_sequence")
            cmd = ["python3", "-u", SEQUENCE]
            cmd += ["--say", say, "--say-url", self.say_url] if say and self.say_url else ["--no-say"]
            env = dict(os.environ, ROS_DOMAIN_ID=str(self.treat_domain))
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []

            def read():
                for line in proc.stdout:
                    lines.append(line.rstrip())
                    self.log({"event": "treat_sequence", "line": line.rstrip()})
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            interrupted = False
            while proc.poll() is None:
                if self.stopped() and not interrupted:
                    proc.send_signal(signal.SIGINT)
                    interrupted = True
                time.sleep(0.1)
            reader.join(timeout=2.0)
            code, last = proc.returncode, (lines[-1] if lines else "")
            self.log({"event": "treat_sequence_done", "exit": code})
            if code == 0:
                return {"released": True, "reason": "handed over (treat_sequence.py)"}
            if code in (NO_HAND, NOBODY):
                return {"released": False, "reason": last or ("no hand held out" if code == NO_HAND else "nobody found")}
            if code in (STOPPED, INTERRUPTED) or interrupted:
                raise MotionRefused(f"treat_sequence: stopped ({last})")
            raise MotionRefused(f"treat_sequence: a step failed, exit {code} ({last})")

    TreatRobot.say_url = None                # the listener's POST /api/say (demo_runner --say-url)
    TreatRobot.treat_domain = 0              # the services' ROS domain
    return TreatRobot
