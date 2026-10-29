"""Trick or treat: find the person, settle the head on their torso, and when they hold a hand out, the right arm's hand
follows it, live; hand down, the arm goes home; hand up again, it follows again. The decisions are treat_brain.py's;
this is the wiring.

  treat_follow.sh up | down | start | stop | status       (treat_follow.sh's header)

THE SERVICES, robot_api_interfaces like the other /perception services, on the robot's domain:
  /perception/treat_follow/start   RobotCommand {}   checks the simulated arm, the hand tracker and the transform; the
                                   head starts searching; returns at once
  /perception/treat_follow/stop    RobotCommand {}   everything off: the head still, the arm holds where it is
  /perception/treat_follow/status  RobotQuery        the arm's mode and why, the head's, the hand followed, the transform

IN:  /perception/hand/state (the tracker, hand_tracker/node.py): per frame, both palms (followable only), both
     shoulders, the shoulders' midpoint as angles off the camera's axis, all from one image. Only this topic: palms and
     shoulders of the same frame, and the tracker runs only while it has a subscriber, so stop also stops MediaPipe.
     Through hand_follow.Frames (the head's joints and the camera's IMU) into arm_mount.
OUT: the neck: GAZE over UDP 4242 (head protocol section 4), as the dashboard's tracking sends it, only while hand_follow's
     HeadTilt.blocked() finds nothing against it (the neck held or touched or on the joystick, the dashboard's own head
     tracking on, a real arm's J2 over 142 deg). THE HEAD MOVES BY ITSELF.
     The arm: right/target on --sim-domain (the simulated arm only), through PositionIk, so every pose sent is one the
     arm can reach. /treat_follow/palm and /treat_follow/target on the robot's domain, for watching.

NO JUMPS: every tick sends a target (HOLD sends the last commanded pose again, latched); every motion starts from the
pose last commanded; the commanded point is a speed- and acceleration-limited spring (treat_brain.Smoother); the
wrist turns at most --max-turn. One IK posture (home) throughout: switching postures between follow and home tripped
the arm service's vibration guard in hand_follow V2.x. When this process ends the arm service is told to `hold`.
"""

import argparse
import json
import math
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grasp_to_sim as g2s  # noqa: E402
from hand_follow import (ARM_FRAME, CAMERA_FRAME, SHOULDER, Frames, HeadTilt, PositionIk, fmt,  # noqa: E402
                         hold_arm, neck_host_from_env, turn_toward)
from treat_brain import (SIDES, Brain, BrainParams, Frame, HeadLaw, HeadParams, Offer, OfferParams,  # noqa: E402
                         PalmFilter, Smoother, reach_goal)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-topic", default="/perception/hand/state")
    ap.add_argument("--transform", default="joints", choices=("joints", "tf", "calib"), help="hand_follow.py's")
    ap.add_argument("--calib", default=os.path.join(HERE, "..", "hand_tracker", "cam_to_arm_nominal.json"))
    ap.add_argument("--head-tilt-from", choices=("imu", "neck"), default="imu")
    ap.add_argument("--max-tilt-disagree-deg", type=float, default=3.0)
    # the arm's goal
    ap.add_argument("--standoff", type=float, default=0.10, help="m the hand stops short of the palm (toward the robot)")
    ap.add_argument("--min-reach", type=float, default=0.25)
    ap.add_argument("--max-reach", type=float, default=0.57, help="m from the shoulder (0.57: the elbow bent ~50 deg)")
    ap.add_argument("--min-fwd", type=float, default=0.30, help="m ahead of the shoulder at least")
    ap.add_argument("--max-up", type=float, default=0.35, help="m above the shoulder at most")
    ap.add_argument("--box", default="0.05,0.85,-0.85,0.50,0.15,1.40", help="x0,x1,y0,y1,z0,z1 in arm_mount: a safety box")
    ap.add_argument("--home-q", default="",
                    help="home as the arm's 7 joints, rad; default: where the hand was when following started (the arm "
                         "service's `ready`)")
    # how fast it follows
    ap.add_argument("--tau", type=float, default=0.07, help="s, the commanded point's spring")
    ap.add_argument("--max-speed", type=float, default=0.50, help="m/s (0.5: the arm service's own pose limit)")
    ap.add_argument("--max-accel", type=float, default=3.0, help="m/s^2")
    ap.add_argument("--max-turn", type=float, default=0.0,
                    help="rad/s the wrist may turn; 0 (default): the orientation of the joint-limited IK solution as it is, "
                         "so every pose sent is one arm pose (a lagging orientation paired it with a position no pose has)")
    ap.add_argument("--home-speed", type=float, default=0.30,
                    help="m/s going home: near the body the elbow turns fastest, and going home needs no hurry (at 0.5 the "
                         "sim's elbow reached 2.2-2.5 rad/s and the arm service's guard stopped the follow; opendubs2, 2026-10-28)")
    ap.add_argument("--max-joint-speed", type=float, default=1.2,
                    help="rad/s any joint of the IK's solution may move: the pose sent comes from a smooth joint path. "
                         "Unlimited, the position-only IK swung its free joints at home<->follow switches and the arm "
                         "service's guard stopped the follow (VIBRATION in joint 1, over 2.5 rad/s; opendubs2, 2026-10-28)")
    ap.add_argument("--lead", type=float, default=0.08, help="s the palm is predicted ahead (the tracker is ~0.1 s late)")
    ap.add_argument("--gate", type=float, default=0.30, help="m: a palm farther than this from the prediction needs a second frame")
    # when it follows
    ap.add_argument("--offer-fwd", type=float, default=0.15, help="m nearer the robot than its shoulder: held out")
    ap.add_argument("--offer-up", type=float, default=-0.15, help="m relative to its shoulder: this high counts however far forward")
    ap.add_argument("--offer-s", type=float, default=0.12)
    ap.add_argument("--away-s", type=float, default=0.45)
    ap.add_argument("--palm-grace-s", type=float, default=0.5)
    ap.add_argument("--lost-home-s", type=float, default=3.0)
    # the head
    ap.add_argument("--no-head", dest="head", action="store_false", help="leave the head alone (the arm follows once a torso is seen)")
    ap.add_argument("--head-pan", action="store_true", help="pan as well as tilt to centre the torso (default: tilt only)")
    ap.add_argument("--head-aim-deg", type=float, default=15.0,
                    help="hold the person's shoulders this far ABOVE the picture's centre (deg): the head looks lower, so a hand held out at the waist is in frame (15 by default; 0: the shoulders centred, as before)")
    ap.add_argument("--neck-host", default=None, help="default OPENDUBS_HEAD_NECK_HOST (environment or /etc/opendubs/robot.env)")
    ap.add_argument("--head-tilt-min", type=float, default=71.0)
    ap.add_argument("--head-tilt-max", type=float, default=108.0)
    ap.add_argument("--head-gain", type=float, default=0.4)
    ap.add_argument("--head-wait-s", type=float, default=0.5)
    ap.add_argument("--head-lock-deg", type=float, default=10.0, help="while the arm follows, the head moves only past this")
    ap.add_argument("--head-quiet-s", type=float, default=0.35, help="s after a head move before palms are trusted again")
    ap.add_argument("--head-lease-ms", type=int, default=600)
    ap.add_argument("--arm-j2-max", type=float, default=142.0)
    # plumbing
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--start", action="store_true", help="on from the start, as if start was called")
    ap.add_argument("--dummy", action="store_true",
                    help="no camera, no head: a scripted person in arm_mount (dummy_frame) drives the arm, for the simulation")
    ap.add_argument("--dummy-speed", type=float, default=1.0, help="how fast the dummy's hand moves (1: up to ~0.5 m/s)")
    ap.add_argument("--no-auto-resume", dest="auto_resume", action="store_false")
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=78)
    ap.add_argument("--reference", default="right")
    ap.add_argument("--model", default=g2s.REPO_MODEL if os.path.exists(g2s.REPO_MODEL) else "openarm_assets:bimanual")
    ap.add_argument("--arm-socket", default=os.environ.get("HAND_ARM_SOCKET"))
    return ap.parse_args()


def dummy_frame(t, speed, rng):
    """A scripted person 1.1 m out, facing the robot, in a 20 s loop: hand hanging; the right hand held out and waved
    (with a 0.3 s dropout and stray points); dropped; the left hand held out; nobody; hanging again. 14 frames a second
    with 1 cm of noise, as the tracker's."""
    shoulders = {"left": np.array([1.1, -0.18, 1.35]), "right": np.array([1.1, 0.18, 1.35])}
    c = t % 20.0
    w = 2 * math.pi * speed

    def hanging(side):
        return shoulders[side] + np.array([0.02, 0.04 if side == "right" else -0.04, -0.55])

    if 14.0 <= c < 16.0:
        return Frame(t, False)
    palms = {"left": hanging("left"), "right": hanging("right")}
    if 2.0 <= c < 9.0 and not 5.0 <= c < 5.3:
        palms["right"] = np.array([0.80 + 0.08 * math.sin(w * c / 5.0), 0.05 + 0.30 * math.sin(w * c / 4.0),
                                   1.05 + 0.15 * math.sin(w * c / 3.0)])
    elif 5.0 <= c < 5.3:
        del palms["right"]
    elif 10.0 <= c < 14.0:
        palms["left"] = np.array([0.85, -0.25 + 0.20 * math.sin(w * c / 3.5), 1.15 + 0.12 * math.cos(w * c / 2.5)])
    palms = {s: p + rng.normal(0, 0.01, 3) for s, p in palms.items()}
    if rng.random() < 0.03 and "right" in palms:
        palms["right"] = palms["right"] + np.array([0.0, 0.5, 0.2])
    return Frame(t, True, shoulders, palms)


class Head:
    """HeadLaw on the real neck, 10 times a second, in its own thread. HeadTilt is used only for its I/O: NECK, GAZE and
    blocked() (the reasons the head must not move)."""

    def __init__(self, io, law, brain_mode):
        self.io, self.law, self.brain_mode = io, law, brain_mode
        self.lock = threading.Lock()
        self.on, self.block, self.mid, self.mid_at = False, "not checked", None, 0.0

    def put_mid(self, mid, at):
        with self.lock:
            self.mid, self.mid_at = mid, at

    def start(self):
        with self.lock:
            self.on = True
            self.law.tilt = None          # the neck's present angles are read before the first move

    def stop(self):
        with self.lock:
            self.on = False
            self.law.stop()

    def acquired(self):
        with self.lock:
            return self.on and not self.block and self.law.acquired

    def reacquire(self, why):
        with self.lock:
            self.law.reacquire(why)

    def quiet_since(self):
        with self.lock:
            return self.law.quiet_since()

    def status(self):
        with self.lock:
            return {"mode": "blocked" if self.block and self.on else self.law.mode, "acquired": self.law.acquired,
                    "why": self.block or self.law.why, "pan": self.law.pan, "tilt": self.law.tilt}

    def run(self):
        last_check = 0.0
        while True:
            time.sleep(self.law.p.period)
            now = time.monotonic()
            with self.lock:
                if not self.on:
                    continue
            if now - last_check >= 1.0 or self.block:
                block, last_check = self.io.blocked(), now        # also reads where the neck points
                with self.lock:
                    self.block = block
                    if block:
                        self.law.acquired = False
                    elif self.law.tilt is None:
                        self.law.start(now, self.io.pan, self.io.tilt)
            if self.block:
                continue
            with self.lock:
                cmd = self.law.step(now, self.mid, self.mid_at, hold_still=self.brain_mode() in ("follow", "hold"))
            if cmd is None:
                continue
            pan, tilt = cmd
            r = self.io.ask(f"GAZE {pan:.1f} {tilt:.1f} {self.io.a.head_lease_ms}")
            if r and r.startswith("OK gaze"):
                try:
                    pan, tilt = float(r.split()[2]), float(r.split()[3])     # what the neck will run, clamped
                except (IndexError, ValueError):
                    pass
            with self.lock:
                self.law.sent(pan, tilt)


def main():
    args = parse_args()
    if args.sim_domain == args.robot_domain:
        raise SystemExit("--sim-domain must differ from the robot's domain: the simulation's joint_states would reach the robot")
    b = [float(v) for v in args.box.split(",")]
    box = ((b[0], b[1]), (b[2], b[3]), (b[4], b[5]))

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from geometry_msgs.msg import PointStamped, PoseStamped
    from rclpy.context import Context
    from rclpy.node import Node
    from robot_api_interfaces.msg import ResultCode
    from robot_api_interfaces.srv import RobotCommand, RobotQuery
    from sensor_msgs.msg import JointState
    from std_msgs.msg import String

    robot_ctx = Context()
    rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
    node = Node("treat_follow", context=robot_ctx)
    frames = Frames(rclpy, node, args.transform, args.calib, (), args.head_tilt_from, args.max_tilt_disagree_deg)
    g2s.spin_in_thread(rclpy, node, robot_ctx)

    # the REAL arms' J2, for the head's clearance rule (unknown: the head does not move)
    real_j2 = {"value": {}, "at": 0.0}

    def on_real_joints(msg):
        got = {n: p for n, p in zip(msg.name, msg.position) if n in ("openarm_left_joint2", "openarm_right_joint2")}
        if got:
            real_j2["value"].update(got)
            real_j2["at"] = time.monotonic()

    node.create_subscription(JointState, "/joint_states", on_real_joints, 10)

    def arms_clear():
        if time.monotonic() - real_j2["at"] > 2.0 or len(real_j2["value"]) < 2:
            return "the real arms' positions are not known (/joint_states)"
        up = {n: math.degrees(abs(v)) for n, v in real_j2["value"].items() if math.degrees(abs(v)) > args.arm_j2_max}
        return f"an arm's shoulder is raised ({', '.join(f'{n} {d:.0f} deg' for n, d in up.items())})" if up else None

    def new_brain():
        return Brain(BrainParams(offer_s=args.offer_s, away_s=args.away_s, palm_grace_s=args.palm_grace_s,
                                 lost_home_s=args.lost_home_s),
                     Offer(OfferParams(fwd_in=args.offer_fwd, fwd_out=args.offer_fwd * 0.55, up_in=args.offer_up,
                                       up_out=args.offer_up - 0.10)),
                     PalmFilter(gate_m=args.gate, lead_s=args.lead))

    brain = new_brain()
    head = None
    if args.head and not args.dummy:
        host = args.neck_host or neck_host_from_env()
        if not host:
            raise SystemExit("no neck host: set OPENDUBS_HEAD_NECK_HOST or --neck-host, or run with --no-head")
        law = HeadLaw(HeadParams(tilt_min=args.head_tilt_min, tilt_max=args.head_tilt_max, pan=args.head_pan, aim_deg=args.head_aim_deg,
                                 gain=args.head_gain, wait_s=args.head_wait_s, lock_deg=args.head_lock_deg))
        head = Head(HeadTilt(host, None, args, arms_clear), law, lambda: brain.mode)
        threading.Thread(target=head.run, daemon=True).start()

    pub_palm = node.create_publisher(PointStamped, "/treat_follow/palm", 10)
    pub_target = node.create_publisher(PoseStamped, "/treat_follow/target", 10)

    hand_model = g2s.HandModel(args.model)
    ik = PositionIk(args.model, args.reference)
    home_pos = ik.fk(np.array([float(v) for v in args.home_q.split(",")]))[0] if args.home_q else None
    sim_ctx = Context()
    rclpy.init(context=sim_ctx, domain_id=args.sim_domain)
    sim = g2s.SimArm(rclpy, sim_ctx, args.reference, hand_model)
    g2s.spin_in_thread(rclpy, sim.node, sim_ctx)
    if not sim.wait_for_state():
        raise SystemExit(f"no joint_states on domain {args.sim_domain}: start the simulation first (treat_follow.sh up)")

    sys.path.insert(0, os.path.join(HERE, "..", "..", "openarm", "gafro"))
    import arm_client
    arm = {"following": None, "line": "not asked yet"}

    def arm_command(command):
        try:
            return arm_client.send(command, path=args.arm_socket, timeout=5.0).strip().splitlines()[0]
        except (OSError, IndexError, TypeError) as error:
            return f"no answer from {args.arm_socket}: {error}"

    def watch_arm():
        while True:
            line = arm_command("status")
            following = "follow running" in line
            arm.update(following=following, line=line.split("last: ", 1)[-1] if not following else line)
            time.sleep(1.0)

    if args.arm_socket:
        threading.Thread(target=watch_arm, daemon=True).start()
    else:
        print("no --arm-socket: the arm service is not watched", file=sys.stderr, flush=True)

    lock = threading.Lock()
    run = {"on": False, "sub": None, "info": None, "error": None}
    latest = {"msg": None, "at": 0.0}

    def on_state(msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        with lock:
            latest["msg"], latest["at"] = data, time.monotonic()

    def switch(on):
        nonlocal brain
        with lock:
            if on and run["sub"] is None and not args.dummy:
                run["sub"] = node.create_subscription(String, args.state_topic, on_state, 10)
            if not on and run["sub"] is not None:
                node.destroy_subscription(run["sub"])
                run["sub"] = None
            if on and not run["on"]:
                brain = new_brain()
            latest["msg"] = None
            run["on"] = on
        if head is not None:
            (head.start if on else head.stop)()

    def reply(response, code, message, data):
        response.result_code.result_code, response.result_code.message = code, message
        response.data = json.dumps(data)
        print(message, flush=True)
        return response

    def on_start(_request, response):
        if sim.stamp is None or time.monotonic() - sim.stamp > 2.0:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING,
                         f"the simulated arm is not publishing joint_states on domain {args.sim_domain} (treat_follow.sh up)", {})
        if not args.dummy and node.count_publishers(args.state_topic) == 0:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING,
                         f"nothing publishes {args.state_topic}: the hand tracker is not running (hand_stack.sh up)", {})
        try:
            _, info = frames.camera_in_arm()
        except LookupError as error:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING, f"no {ARM_FRAME} <- {CAMERA_FRAME} transform: {error}", {})
        if args.arm_socket and arm["following"] is not True:
            line = arm_command("follow")
            if not (line.startswith("follow started") or "follow is running" in line):
                return reply(response, ResultCode.SERVICE_NOT_RUNNING, f"the arm service did not start following: {line}", {})
            arm["following"] = None
        was = run["on"]
        switch(True)
        return reply(response, ResultCode.SUCCESS, "already on" if was else "on: looking for a person",
                     {"on": True, "transform": info})

    def on_stop(_request, response):
        was = run["on"]
        switch(False)
        return reply(response, ResultCode.SUCCESS, "stopped: the arm holds where it is" if was else "was not on", {"on": False})

    def on_status(_request, response):
        alive = sim.stamp is not None and time.monotonic() - sim.stamp < 2.0
        hand_p, _ = sim.hand()
        data = {"on": run["on"], "arm": brain.status(), "head": None if head is None else head.status(),
                "arm_service": arm["line"], "hand_position": [round(float(v), 4) for v in hand_p],
                "home": None if home_pos is None else [round(float(v), 3) for v in home_pos], "transform": run["info"], "last_error": run["error"],
                "sim_domain": args.sim_domain}
        response.result_code.result_code = ResultCode.SUCCESS if alive else ResultCode.SERVICE_NOT_RUNNING
        response.result_code.message = brain.why if alive else f"no joint_states from the simulated arm on domain {args.sim_domain}"
        response.data = json.dumps(data)
        return response

    node.create_service(RobotCommand, "/perception/treat_follow/start", on_start)
    node.create_service(RobotCommand, "/perception/treat_follow/stop", on_stop)
    node.create_service(RobotQuery, "/perception/treat_follow/status", on_status)
    if args.start:
        switch(True)
    print(f"treat_follow: {args.state_topic} -> {ARM_FRAME} ({args.transform}) -> {args.reference}/target on domain "
          f"{args.sim_domain}; head: {'off' if head is None else ('pan and tilt' if args.head_pan else 'tilt')}; services "
          f"/perception/treat_follow/{{start,stop,status}}; {'ON' if run['on'] else 'off until start'}", flush=True)

    smoother = Smoother(args.tau, args.max_speed, args.max_accel)
    dt = 1.0 / args.rate
    active, cmd, rot, sent, goal = False, None, None, None, None
    seen_msg, last_print, last_err, last_err_at, resume_at, backoff = None, 0.0, "", 0.0, 0.0, 2.0
    no_head_seen, next_dummy = -1e9, 0.0
    rng = np.random.default_rng(0)
    try:
        while True:
            now = time.monotonic()
            with lock:
                msg, msg_at = latest["msg"], latest["at"]
            if run["on"] and args.dummy and now >= next_dummy:
                next_dummy = now + 1 / 14
                f = dummy_frame(now, args.dummy_speed, rng)
                no_head_seen = now if f.person else no_head_seen
                brain.update(f)
            elif run["on"] and msg is not None and msg is not seen_msg:
                # A TRACKER FRAME: into arm_mount with the transform at its image's stamp, then to the brain and head
                seen_msg = msg
                mid = msg.get("shoulder_mid")
                if head is not None:
                    head.put_mid(mid, msg_at)
                elif mid is not None:
                    no_head_seen = now
                person = msg.get("person_z") is not None or mid is not None
                shoulders, palms, trusted = {}, {}, False
                try:
                    T, info = frames.camera_in_arm(msg.get("stamp"))
                    run["info"] = info

                    def to_arm(v):
                        return T[:3, :3] @ np.asarray(v, float) + T[:3, 3]
                    shoulders = {s: to_arm(v) for s, v in (msg.get("shoulders") or {}).items() if s in SIDES and v}
                    palms = {s: to_arm(d["xyz"]) for s, d in (msg.get("palms") or {}).items()
                             if s in SIDES and d and d.get("followable") and d.get("xyz")}
                    trusted = head is None or msg_at - head.quiet_since() > args.head_quiet_s
                except LookupError as error:
                    run["error"] = str(error)
                    if str(error) != last_err or now - last_err_at > 5.0:
                        print(f"no transform: {error}", file=sys.stderr, flush=True)
                        last_err, last_err_at = str(error), now
                if brain.update(Frame(msg_at, person, shoulders, palms, trusted)) and head is not None:
                    head.reacquire("someone else is in front")
                for side, p in palms.items():
                    if side == brain.side:
                        pm = PointStamped()
                        pm.header.stamp, pm.header.frame_id = node.get_clock().now().to_msg(), ARM_FRAME
                        pm.point.x, pm.point.y, pm.point.z = (float(v) for v in p)
                        pub_palm.publish(pm)

            acquired = head.acquired() if head is not None else now - no_head_seen < 1.0
            motion, palm_goal = brain.tick(now, acquired) if run["on"] else ("hold", None)
            for event in brain.events:
                print(event, flush=True)
            brain.events.clear()

            arm_on = arm["following"]
            if run["on"] and arm_on is False:
                # the arm service is not following right/target (its guard stopped it, or it was told to hold)
                motion, active = "hold", False
                if args.auto_resume and now >= resume_at:
                    print(f"resuming the arm service's follow after: {arm['line']}: {arm_command('follow')}", flush=True)
                    resume_at, backoff = now + backoff, min(backoff * 2, 30.0)
                    cmd = sent = None            # its controller starts where the arm is now: so does this
                    arm["following"] = None

            if motion == "hold" or (motion == "follow" and palm_goal is None):
                active = False
                if sent is not None:
                    sim.send(*sent)              # HOLD: the last commanded pose, latched
            else:
                if not active:
                    # every motion starts from the pose last commanded (or, the first time, where the arm is)
                    if cmd is None:
                        # the IK's posture is the arm's as the service's follow starts: the service's own posture
                        # reference (pose_follow.py), so the two resolve the arm's free joint alike. Pulled toward
                        # another posture, they fought, and the arm's joints ran over the guard's speed.
                        with sim.lock:
                            ik.reset_qpos(sim.model.data.qpos)
                        cmd = sim.hand()[0].copy()
                        home_pos = cmd.copy() if home_pos is None else home_pos
                    smoother.reset(cmd)
                    active = True
                goal = home_pos if motion == "home" else reach_goal(palm_goal, SHOULDER, args.min_reach, args.max_reach,
                                                                    args.standoff, args.min_fwd, args.max_up, box=box)
                smoother.vmax = args.home_speed if motion == "home" else args.max_speed
                pf = smoother.step(goal, dt)
                q_prev = ik.d.qpos[ik.qadr].copy()
                pos, rot_ik, q = ik.solve(pf)
                dq = q - q_prev
                over = float(np.max(np.abs(dq))) / (args.max_joint_speed * dt)
                if over > 1.0:
                    # the IK moved some joint faster than allowed: go only that far along its step this tick
                    q = q_prev + dq / over
                    ik.d.qpos[ik.qadr] = q
                    pos, rot_ik = ik.fk(q)
                if np.linalg.norm(pos - pf) > 0.005:
                    smoother.pin(pos)            # at the reach: the spring stays on what the arm can reach
                rot = rot_ik if rot is None or not args.max_turn else turn_toward(rot, rot_ik, args.max_turn * dt)
                cmd, sent = pos, (pos, rot)
                sim.send(pos, rot)
                tm = PoseStamped()
                tm.header.stamp, tm.header.frame_id = node.get_clock().now().to_msg(), ARM_FRAME
                tm.pose.position.x, tm.pose.position.y, tm.pose.position.z = (float(v) for v in cmd)
                q = g2s.mat_to_quat(rot)
                tm.pose.orientation.x, tm.pose.orientation.y, tm.pose.orientation.z, tm.pose.orientation.w = (float(v) for v in q)
                pub_target.publish(tm)

            if now - last_print >= 1.0:
                last_print = now
                hand_p, _ = sim.hand()
                hs = head.status() if head is not None else None
                line = f"[{brain.mode}: {brain.why}] hand {fmt(hand_p)}"
                if hs:
                    line += f"  head {hs['mode']} tilt {hs['tilt']} pan {hs['pan']}"
                if active and goal is not None:
                    line += f"  goal {fmt(goal)} target {fmt(cmd)} ({np.linalg.norm(hand_p - cmd) * 1000:.0f} mm off)"
                print(line, flush=True)
            time.sleep(max(0.0, dt - (time.monotonic() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        g2s.shutdown_all(rclpy)
        hold_arm(args.arm_socket, args.reference)
    return 0


def _sigterm(*_):
    raise KeyboardInterrupt      # `treat_follow.sh down` sends TERM: the same way out as Ctrl-C, so the arm is held


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, _sigterm)
    try:
        code = main()
    finally:
        if g2s.SPINNING:
            import rclpy
            g2s.shutdown_all(rclpy)
    sys.exit(code)
