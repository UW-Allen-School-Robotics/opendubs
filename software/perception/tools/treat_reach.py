"""Trick or treat, V1, one shot: centre the head on the person, wait for a raised hand to come to rest, send the SIMULATED
right arm to it with the arm service's `reach_pose` action, and exit.

  treat_follow.sh reach [options]       (the tracker and the simulated arm started first if they are not running)

  0. READY   first, every run: the `ready` command (same action as below) takes the simulated arm to its ready pose;
             if it does not get there, nothing else is done (exit 1). Not with --dry-run.
  1. CENTRE  the head tilts (and pans with --head-pan) until the nearest person's torso is centred and settled
             (treat_follow.Head, the follower's own). THE HEAD MOVES BY ITSELF. Nobody settled on in --find-s: exit 3.
  2. WAIT    up to --wait-s for a hand held out (treat_brain.Offer) and STILL: --stable-frames tracker frames in a row
             with every palm within --still-m of their mean (treat_brain.HandCapture). A hand still rising does not
             count. None in time: exit 2. The capture is the latest palm of that run, in arm_mount (hand_follow.Frames:
             the head's joints, re-tilted to the camera's accelerometer).
  3. REACH   the goal is that palm, --standoff short and inside the arm's comfortable reach (treat_brain.reach_goal);
             the simulated arm service is told to `hold` (a running follow would make it refuse), then one ArmCommand
             goal `reach_pose` {"position": goal} on /right_arm/command (software/openarm/interfaces). Its feedback and
             result are printed.
  4. HOME    after --treat-s (the treat taken), the `ready` command on the same action takes the arm back to the
             model's `ready` pose (--no-return: it stays). Exit 0 if it reached the pose and got home, 1 if not.
             Ctrl-C at any point cancels what is running, the arm holds where it is, and no further move is made.

By default only the simulation: the goals go to --sim-domain, which must differ from the robot's. --dummy: no camera and
no head, a scripted person in arm_mount who raises a hand after 2 s (for the action path without anyone in frame).
--dry-run: all but the goals.

--real (treat_follow.sh reach-real): THE REAL RIGHT ARM, for supervised testing, the same steps with these changes:
  - the goals go to the real arm service (opendubs-arm@right) on the robot's domain; nothing is sent to the simulation
  - the head stays still (--no-head) and the real arms' clearance rule is on (--arm-check). With --head (and
    --left-stowed: the left arm is not run and is at stow, so its unpublished position counts as clear) the head centres
    on the person, but only AFTER the first `ready` and it stops before `reach_pose`: the head and the arm never move
    at the same time, as `supervise` assumes. The right arm's J2 comes from the service's /joint_states (142 deg rule)
  - the reach goal at most REAL_MAX_UP m above the arm's shoulder (a hand held higher is reached toward at that height)
  - reach_pose at REAL_SPEED rad/s by default and REAL_SPEED_MAX at most (--speed), REAL_STANDOFF m short of the palm at least (`ready` takes no speed: it moves
    at the service's default, ~0.68 rad/s with the driver's max_velocity at 1.0)
  - before the first move it prints HOLD THE DEADMAN and waits (--deadman-wait-s) until CH5 is MANUAL or AUTO, the e-stop
    is clear and CH6 is held, as the arm service's own gate reads them (guards.motion_still_permitted, 10 times a second)
  - from then on, the moment any of them stops permitting motion (the deadman released) the running goal is cancelled,
    NOTHING more is sent, and the script ends (exit 4): it never moves again after a release
The service must already be started, supervised and activated, and be running a checkout with the command action.
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
from hand_follow import ARM_FRAME, SHOULDER, Frames, HeadTilt, PositionIk, fmt, neck_host_from_env  # noqa: E402
from treat_brain import SIDES, HandCapture, HeadLaw, HeadParams, Offer, OfferParams, reach_goal  # noqa: E402
from treat_follow import Head  # noqa: E402


REAL_SPEED = 0.4       # rad/s: --real's reach_pose joint speed by default (0.3 in V2: the user found it slow)
REAL_SPEED_MAX = 0.6   # rad/s: and at most, with --speed (the service's own default is ~0.68, its cap 0.9)
REAL_STANDOFF = 0.20   # m: --real stops at least this short of the palm
REAL_MAX_UP = 0.25     # m: --real's goal at most this above the arm's shoulder (z ~0.95 m; reach_pose models no head)


class PermitLost(Exception):
    """--real: CH5, the e-stop or the deadman stopped permitting motion."""


DUMMY_Z = float(os.environ.get("TREAT_DUMMY_Z", "1.10"))   # the scripted palm's height in arm_mount, m


def dummy_state(t):
    """(shoulders, palms) of a scripted person 1.1 m out: hands down for 2 s, then the right hand raised over 1 s and held
    out (1 cm of noise)."""
    shoulders = {"left": np.array([1.1, -0.18, 1.35]), "right": np.array([1.1, 0.18, 1.35])}
    down = shoulders["right"] + np.array([0.02, 0.04, -0.55])
    out = np.array([0.80, 0.10, DUMMY_Z])
    a = min(max(t - 2.0, 0.0), 1.0)
    palm = down + a * (out - down) + np.random.normal(0.0, 0.01, 3)
    return shoulders, {"right": palm, "left": shoulders["left"] + np.array([0.02, -0.04, -0.55])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-topic", default="/perception/hand/state")
    ap.add_argument("--find-s", type=float, default=20.0, help="s to find and settle on a person")
    ap.add_argument("--wait-s", type=float, default=10.0, help="s for a raised hand, once settled")
    ap.add_argument("--stable-frames", type=int, default=5, help="tracker frames in a row the hand is out and still")
    ap.add_argument("--still-m", type=float, default=0.05, help="m: the palms of that run all within this of their mean")
    ap.add_argument("--standoff", type=float, default=0.10, help="m short of the palm")
    ap.add_argument("--tolerance-mm", type=float, default=25.0,
                    help="reach_pose's position tolerance (its own default 15 is for a real arm's friction; in the "
                         "simulation a shoulder-high hand ended 16 mm off)")
    ap.add_argument("--speed", type=float, default=None, help="rad/s, reach_pose's joint speed (default: the service's)")
    ap.add_argument("--orientation", choices=("ik", "held"), default="ik",
                    help="ik (default): the hand's orientation in a position-only IK solution for the goal from `ready`, "
                         "one the arm can take there; held: none sent, reach_pose keeps the hand's present one (in the "
                         "simulation that failed for a raised hand: 'the planned path leaves a joint's range')")
    ap.add_argument("--model", default=g2s.REPO_MODEL if os.path.exists(g2s.REPO_MODEL) else "openarm_assets:bimanual")
    ap.add_argument("--no-head", dest="head", action="store_false", help="leave the head where it is")
    ap.add_argument("--head", dest="real_head", action="store_true",
                    help="--real: centre the head too (needs --left-stowed; the head never moves while the arm does)")
    ap.add_argument("--left-stowed", action="store_true",
                    help="--real --head: the left arm is not run and is at stow: its unpublished position counts as clear")
    ap.add_argument("--max-up", type=float, default=0.35, help="m: the reach goal at most this above the arm's shoulder")
    ap.add_argument("--head-pan", action="store_true", help="pan as well as tilt to centre the torso")
    ap.add_argument("--neck-host", default=None)
    ap.add_argument("--head-quiet-s", type=float, default=0.35)
    ap.add_argument("--head-lease-ms", type=int, default=600)
    ap.add_argument("--arm-j2-max", type=float, default=142.0)
    ap.add_argument("--arm-check", action="store_true",
                    help="keep the follower's rule that the head moves only while both REAL arms' positions are known and "
                         "neither shoulder is raised. Off by default: V1 is for the simulation, where the real arm service "
                         "is usually off and nothing publishes them (the head then never moved)")
    ap.add_argument("--real", action="store_true", help="THE REAL RIGHT ARM, supervised testing (above)")
    ap.add_argument("--deadman-wait-s", type=float, default=120.0, help="--real: s to wait for the deadman before giving up")
    ap.add_argument("--dummy", action="store_true", help="no camera, no head: a scripted person (above)")
    ap.add_argument("--dry-run", action="store_true", help="everything but the goal")
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=78)
    ap.add_argument("--arm-socket", default=os.environ.get("HAND_ARM_SOCKET"), help="the simulated arm service's socket")
    ap.add_argument("--result-s", type=float, default=60.0, help="s to wait for each action's result")
    ap.add_argument("--treat-s", type=float, default=5.0, help="s the hand stays at the treat before going back to ready")
    ap.add_argument("--no-return", dest="return_home", action="store_false",
                    help="stay at the treat: no `ready` afterwards")
    args = ap.parse_args()
    args.head = args.head and not args.dummy
    if args.real:
        if args.dummy:
            ap.error("--real and --dummy together: a scripted person must not drive the real arm")
        args.head, args.arm_check = args.real_head, True
        if args.head and not args.left_stowed:
            ap.error("--real --head needs --left-stowed: the head's clearance rule needs both arms' shoulders, and only "
                     "the right arm is run (say so only when the left arm is at stow and stays there)")
        args.max_up = min(args.max_up, REAL_MAX_UP)
        args.speed = min(args.speed or REAL_SPEED, REAL_SPEED_MAX)
        args.standoff = max(args.standoff, REAL_STANDOFF)
        action_domain = args.robot_domain
    else:
        if args.sim_domain == args.robot_domain:
            raise SystemExit("--sim-domain must differ from the robot's domain: without --real the goals go to the simulated arm only")
        action_domain = args.sim_domain

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from rclpy.context import Context
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from std_msgs.msg import String

    robot_ctx = Context()
    rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
    node = Node("treat_reach", context=robot_ctx)
    frames = Frames(rclpy, node, "joints") if not args.dummy else None
    latest = {"msg": None, "at": 0.0}
    lock = threading.Lock()

    def on_state(msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        with lock:
            latest["msg"], latest["at"] = data, time.monotonic()

    if not args.dummy:
        node.create_subscription(String, args.state_topic, on_state, 10)   # the tracker runs while this subscribes
    real_j2 = {"value": {}, "at": 0.0}

    def on_real_joints(msg):
        got = {n: p for n, p in zip(msg.name, msg.position) if n in ("openarm_left_joint2", "openarm_right_joint2")}
        if got:
            real_j2["value"].update(got)
            real_j2["at"] = time.monotonic()

    node.create_subscription(JointState, "/joint_states", on_real_joints, 10)
    g2s.spin_in_thread(rclpy, node, robot_ctx)

    def arms_clear():
        if not args.arm_check:
            return None
        # the follower's rule: the head moves only while both real arms are known and neither shoulder is raised
        values = dict(real_j2["value"])
        if args.left_stowed:
            values.setdefault("openarm_left_joint2", 0.0)    # not run, at stow: what it publishes, if anything, wins
        if time.monotonic() - real_j2["at"] > 2.0 or len(values) < 2:
            return "the real arms' positions are not known (/joint_states)"
        up = [n for n, v in values.items() if math.degrees(abs(v)) > args.arm_j2_max]
        return f"an arm's shoulder is raised ({', '.join(up)})" if up else None

    head = None
    if args.head:
        host = args.neck_host or neck_host_from_env()
        if not host:
            raise SystemExit("no neck host: set OPENDUBS_HEAD_NECK_HOST or --neck-host, or run with --no-head")
        mode = {"m": "watch"}
        head = Head(HeadTilt(host, None, args, arms_clear), HeadLaw(HeadParams(pan=args.head_pan)), lambda: mode["m"])
        threading.Thread(target=head.run, daemon=True).start()
        if not args.real:
            head.start()                                 # --real: only after the first `ready` (step 1)
        if not args.arm_check:
            print("head: the real arms' positions are not checked (simulation; --arm-check to check them)", flush=True)

    def frame_in_arm(msg):
        """(shoulders, palms, mid) of one tracker message in arm_mount, or None when it cannot be trusted."""
        if head is not None and latest["at"] - head.quiet_since() <= args.head_quiet_s:
            return None                                  # taken while the head moved
        try:
            T, _ = frames.camera_in_arm(msg.get("stamp"))
        except LookupError as error:
            print(f"no transform: {error}", file=sys.stderr, flush=True)
            return None

        def to_arm(v):
            return T[:3, :3] @ np.asarray(v, float) + T[:3, 3]
        shoulders = {s: to_arm(v) for s, v in (msg.get("shoulders") or {}).items() if s in SIDES and v}
        palms = {s: to_arm(d["xyz"]) for s, d in (msg.get("palms") or {}).items()
                 if s in SIDES and d and d.get("followable") and d.get("xyz")}
        return shoulders, palms

    # --real: the arm service's own gate (CH5 MANUAL or AUTO, the e-stop clear, CH6 held), read 10 times a second
    permit = {"ok": False, "why": "not read yet", "armed": False}

    def watch_permit():
        sys.path.insert(0, os.path.join(HERE, "..", "..", "openarm", "gafro"))
        import guards
        while True:
            try:
                r = guards.robot_state(timeout=0.2)
                ok = guards.motion_still_permitted(state=lambda _u: r)
                why = None if ok else f"CH5 {r.get('ch5')}, e-stop {r.get('estop')}, deadman {r.get('deadman')}"
            except Exception as error:  # noqa: BLE001 -- unknown is no
                ok, why = False, f"the dashboard does not answer ({type(error).__name__})"
            permit.update(ok=ok, why=why)
            time.sleep(0.1)

    def check_permit():
        """--real, once armed: raise PermitLost the moment motion is no longer permitted."""
        if args.real and permit["armed"] and not permit["ok"]:
            raise PermitLost(permit["why"])

    if args.real:
        threading.Thread(target=watch_permit, daemon=True).start()

    code, t0 = 3, time.monotonic()
    try:
        # 0. READY first, every run: the arm starts from the same known pose (the IK's orientation assumes it)
        if not args.dry_run:
            if args.real:
                print("REAL ARM. HOLD THE DEADMAN NOW (CH5 MANUAL or AUTO, the e-stop clear). Nothing moves until it is "
                      "held; letting go at any time stops the arm and ends this script.", flush=True)
                deadline, last = time.monotonic() + args.deadman_wait_s, None
                while not permit["ok"] and time.monotonic() < deadline:
                    if permit["why"] != last:
                        print(f"  waiting: {permit['why']}", flush=True)
                        last = permit["why"]
                    time.sleep(0.1)
                if not permit["ok"]:
                    print(f"the deadman was not held in {args.deadman_wait_s:.0f} s: nothing was sent", flush=True)
                    return 4
                permit["armed"] = True
                print("deadman held: starting (release it to stop)", flush=True)
            elif args.arm_socket:
                sys.path.insert(0, os.path.join(HERE, "..", "..", "openarm", "gafro"))
                import arm_client
                try:
                    print(f"sim arm service: {arm_client.send('hold', path=args.arm_socket, timeout=5.0).strip().splitlines()[0]}",
                          flush=True)
                except (OSError, IndexError) as error:
                    print(f"could not tell the simulated arm service to hold ({error}): reach_pose may be refused", flush=True)
            from opendubs_arm_interfaces.action import ArmCommand
            from rclpy.action import ActionClient
            sim_ctx = Context()
            rclpy.init(context=sim_ctx, domain_id=action_domain)
            sim_node = Node("treat_reach_client", context=sim_ctx)
            g2s.spin_in_thread(rclpy, sim_node, sim_ctx)
            client = ActionClient(sim_node, ArmCommand, "/right_arm/command")
            if not client.wait_for_server(timeout_sec=10.0):
                print(f"no /right_arm/command on domain {action_domain}: is the "
                      + ("real arm service up, on a checkout with the command action and opendubs_arm_interfaces built "
                         "where it runs?" if args.real else
                         "simulated arm service up, with opendubs_arm_interfaces built and sourced?"), flush=True)
                return 1
            names = {0: "SUCCESS", 1: "FAILURE", 2: "INVALID_INPUT", 5: "NOT_FOUND (out of reach)"}

            def command(name, params):
                """One ArmCommand goal, its feedback printed: (accepted, result code or None). Ctrl-C or no result in
                --result-s cancels it, and a cancel holds the arm where it is (the service's own rule)."""
                check_permit()
                print(f"{name} {json.dumps(params)} -> /right_arm/command (domain {action_domain}"
                      + (", THE REAL ARM)" if args.real else ")"), flush=True)
                sent = client.send_goal_async(ArmCommand.Goal(name=name, params=json.dumps(params)),
                                              feedback_callback=lambda fb: print(f"  {fb.feedback.stage}", flush=True))
                deadline = time.monotonic() + 10.0
                while not sent.done() and time.monotonic() < deadline:
                    time.sleep(0.05)
                handle = sent.result() if sent.done() else None
                if handle is None or not handle.accepted:
                    print(f"{name} was rejected (another move running, or the request is invalid: see the service's log)",
                          flush=True)
                    return False, None
                result = handle.get_result_async()
                deadline = time.monotonic() + args.result_s
                try:
                    while not result.done() and time.monotonic() < deadline:
                        check_permit()
                        time.sleep(0.05)
                except PermitLost:
                    print(f"motion no longer permitted: cancelling {name} (the arm holds where it is)", flush=True)
                    handle.cancel_goal_async()
                    time.sleep(0.5)
                    raise
                except KeyboardInterrupt:
                    print(f"interrupted: cancelling {name} (the arm holds where it is)", flush=True)
                    handle.cancel_goal_async()
                    time.sleep(0.5)
                    raise
                if not result.done():
                    print(f"no result in {args.result_s:.0f} s: cancelling {name} (the arm holds where it is)", flush=True)
                    handle.cancel_goal_async()
                    time.sleep(0.5)
                    return True, None
                r = result.result().result
                print(f"{name}: {names.get(r.result_code, r.result_code)}: {r.message}  {r.data}", flush=True)
                return True, r.result_code

            print("ready: the arm to its ready pose before anything else", flush=True)
            _, first = command("ready", {})
            if first != 0:
                print("the arm did not get to ready: stopping here, nothing else is sent", flush=True)
                return 1

        if args.real:
            t0 = time.monotonic()                        # --find-s from here: the deadman wait and `ready` came first
            if head is not None:
                head.start()                             # the arm is at ready and still: now the head may move
                print("head: centring on the person (the arm is still)", flush=True)
        # 1. CENTRE
        print("centre: looking for a person" + (" (the head moves)" if head else ""), flush=True)
        settled = False
        while time.monotonic() - t0 < args.find_s:
            with lock:
                msg = latest["msg"]
            if head is not None:
                head.put_mid(None if msg is None else msg.get("shoulder_mid"), latest["at"])
                settled = head.acquired()
            else:
                settled = args.dummy or (msg is not None and msg.get("shoulder_mid") is not None)
            if settled:
                break
            check_permit()
            time.sleep(0.05)
        if not settled:
            print(f"nobody settled on in {args.find_s:.0f} s" + (f" (head: {head.status()['why']})" if head else ""), flush=True)
            return 3
        hs = head.status() if head else None
        print("settled" + (f": head tilt {hs['tilt']} pan {hs['pan']}" if hs else ""), flush=True)

        # 2. WAIT for a raised hand that has come to rest
        capture = HandCapture(Offer(OfferParams()), args.stable_frames, args.still_m)
        t1, got, seen, next_dummy = time.monotonic(), None, None, 0.0
        print(f"waiting up to {args.wait_s:.0f} s for a hand held out and still ({args.stable_frames} frames)", flush=True)
        while time.monotonic() - t1 < args.wait_s and got is None:
            now = time.monotonic()
            if args.dummy:
                if now >= next_dummy:
                    next_dummy = now + 1 / 14
                    shoulders, palms = dummy_state(now - t1)
                    got = capture.update(palms, shoulders)
            else:
                with lock:
                    msg = latest["msg"]
                if head is not None:
                    head.put_mid(None if msg is None else msg.get("shoulder_mid"), latest["at"])
                if msg is not None and msg is not seen:
                    seen = msg
                    f = frame_in_arm(msg)
                    if f is not None:
                        got = capture.update(f[1], f[0])
            check_permit()
            time.sleep(0.01)
        if head is not None:
            head.stop()                                  # the head stays where it is (the lease lapses)
        if got is None:
            print(f"no raised hand in {args.wait_s:.0f} s: done", flush=True)
            return 2
        side, palm = got
        goal = reach_goal(palm, SHOULDER, standoff=args.standoff, max_up=args.max_up)
        print(f"captured the {side} hand at {fmt(palm)} in {ARM_FRAME}; reach_pose goal {fmt(goal)}", flush=True)
        if args.dry_run:
            print("--dry-run: no goal sent", flush=True)
            return 0

        # 3. REACH
        params = {"position": [round(float(v), 4) for v in goal]}
        if args.orientation == "ik":
            ik = PositionIk(args.model, "right")          # starts at the model's `ready`, where the arm service starts
            pos, rot, _ = ik.solve(goal, iterations=300)
            x, y, z, w = (float(v) for v in g2s.mat_to_quat(rot))
            params["orientation"] = [round(w, 5), round(x, 5), round(y, 5), round(z, 5)]
            miss = float(np.linalg.norm(pos - goal))
            if miss > 0.01:
                # the goal is past what the arm reaches with a comfortable posture: send the nearest point it does reach,
                # whose orientation this is (the raw goal was refused: 'the planned path leaves a joint's range')
                params["position"] = [round(float(v), 4) for v in pos]
            print(f"orientation from the IK; its hand {miss * 1000:.0f} mm from the goal"
                  + (f": sending its position {fmt(pos)} instead" if miss > 0.01 else ""), flush=True)
        params["tolerance_mm"] = args.tolerance_mm
        if args.speed:
            params["speed"] = args.speed
        accepted, reached = command("reach_pose", params)
        code = 0 if reached == 0 else 1
        if not accepted or not args.return_home:
            return code
        # HOME: the arm moved (or tried to): leave the treat there for --treat-s, then back to `ready` by the same
        # action. Ctrl-C during the wait or the move stops it where it is: no move is made after an interrupt.
        print(f"holding for {args.treat_s:.0f} s for the treat to be taken, then back to ready (Ctrl-C: stay)", flush=True)
        end = time.monotonic() + args.treat_s
        while time.monotonic() < end:
            check_permit()
            time.sleep(0.05)
        _, home = command("ready", {})
        if home != 0:
            print("the arm did not get back to ready: it holds where it is", flush=True)
            return 1
        return code
    except PermitLost as lost:
        print(f"STOPPED: motion is no longer permitted ({lost}). Nothing more is sent; the arm holds where it is.",
              flush=True)
        return 4
    except KeyboardInterrupt:
        return 130
    finally:
        if head is not None:
            head.stop()
        g2s.shutdown_all(rclpy)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
