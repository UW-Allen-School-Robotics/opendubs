"""Ask perception for a grasp and send it to the simulated right arm, then say whether the hand got there.

  grasp_to_sim.sh sim                     # terminal 1: the arm service on MuJoCo (follow_pose) and its viewer, own ROS domain
  grasp_to_sim.sh run --label bottle      # terminal 2: perception -> TF -> simulated arm

What it does:
  1. Calls /perception/grasp_object on the robot's domain (0). The pose comes back in camera_color_optical_frame
     (x right, y down, z forward) with the GraspNet gripper convention (x approach, y closing).
  2. Looks up arm_mount <- camera_color_optical_frame in TF at the image's stamp. The robot description carries the
     chain arm_mount <- lift_platform -> head_pan -> head_tilt -> femto_mount_link -> camera_link, robot_state_publisher
     moves the head with the neck's live angles (software/ros/head_joint_relay.py), and the Orbbec driver adds
     camera_link -> camera_color_optical_frame from the camera's factory extrinsics. arm_mount is where the arm model's
     root (openarm_body_link0: x forward, y left, z up) sits, the frame the simulation's `right/target` is read in.
  3. Turns the gripper axes into the TCP's (openarm_right_hand_tcp: z approach, y closing).
  4. Moves the simulated hand to a pre-grasp pose --approach metres back along the approach, then onto the grasp,
     and reports the position and angle error the simulated arm reached.

The camera's accelerometer (/camera/gyro_accel/sample, through the driver's colour->IMU transform) gives the world's
up in the camera frame. It goes to grasp_object as `up_cam`, and it CHECKS the transform: the angle between it and
arm_mount's +z as TF has it is printed, and over --max-tilt-disagree-deg the run stops. The neck has no encoder, so
TF's head angles are the commanded ones; this is what catches a neck that is not where it says.

The simulation runs on ROS domain --sim-domain (77), not on the robot's: the arm service publishes `joint_states`, and
the robot's own /joint_states must not see simulated joints. Nothing here reaches a real arm: the simulated service runs
--backend mujoco, and this tool publishes targets only on the simulation's domain. The real arm service takes the same
`right/target`, so the targets tested here are the ones it would follow.

  --dry-run          perception and the transform only; prints the targets, sends nothing
  --save FILE        the response, the transform used and the targets, as JSON
  --from-json FILE   replay a --save file (its recorded transform included) or a bare grasp_object response, without
                     calling perception
  --tf-topics A,B    extra TF topics to read besides /tf and /tf_static (a private robot_state_publisher under test)
  --cam-xyz, --cam-yaw-deg   a MANUAL camera pose instead of TF (position in arm_mount, yaw toward the robot's left),
                     with tilt and roll from the IMU; for a robot without the head's TF
"""

import argparse
import json
import math
import os
import sys
import threading
import time

import numpy as np

ARM_FRAME = "arm_mount"               # the robot description's frame at the arm model's root
REPO_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "description", "openarm", "gafro",
                          "openarm_bimanual.xml")   # the arm service's model (software/openarm/gafro/engage.py DEFAULT_MODEL)
CAMERA_FRAME = "camera_color_optical_frame"
SHOULDER = np.array([0.0, -0.031, 0.698])   # openarm_right_link0 in the model's frame
REACH_WARN_M = 0.62                   # beyond this from the shoulder the right arm is near or past its reach
# GraspNet gripper axes -> TCP axes. Columns are the TCP's x, y, z in the grasp frame: z_tcp = x_grasp (approach),
# y_tcp = y_grasp (closing), x_tcp = y x z = -z_grasp.
GRASP_TO_TCP = np.array([[0.0, 0.0, 1.0],
                         [0.0, 1.0, 0.0],
                         [-1.0, 0.0, 0.0]])
FLIP_JAWS = np.diag([-1.0, -1.0, 1.0])   # a parallel-jaw grasp turned 180 deg about its approach is the same grasp


# -- rotations ----------------------------------------------------------------------------------------------------

def quat_to_mat(x, y, z, w):
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mat_to_quat(r):
    """(x, y, z, w) of a rotation matrix."""
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def angle_between(a, b):
    """Degrees between two rotation matrices."""
    c = (np.trace(a.T @ b) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def slerp(q0, q1, s):
    d = float(np.dot(q0, q1))
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + s * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - s) * th) * q0 + math.sin(s * th) * q1) / math.sin(th)


def minimum_jerk(s):
    return 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5


# -- the camera's pose --------------------------------------------------------------------------------------------

def camera_to_base(up_cam, cam_xyz, yaw_deg):
    """The 4x4 transform from camera_color_optical_frame to the arm model's frame.

    Tilt and roll come from `up_cam` (the world's up in the camera frame); yaw and position are given. Yaw is the
    angle of the camera's horizontal forward from the robot's +x, positive toward the robot's left (+y).
    """
    up = np.asarray(up_cam, float)
    up = up / np.linalg.norm(up)
    forward = np.array([0.0, 0.0, 1.0]) - up[2] * up      # the optical axis, made horizontal
    if np.linalg.norm(forward) < 1e-3:
        raise ValueError("the camera looks straight up or down: its yaw is undefined, give the transform another way")
    forward /= np.linalg.norm(forward)
    left = np.cross(up, forward)
    psi = math.radians(yaw_deg)
    x_base = forward * math.cos(psi) - left * math.sin(psi)   # the robot's forward, in camera coordinates
    z_base = up
    y_base = np.cross(z_base, x_base)
    t = np.eye(4)
    t[:3, :3] = np.vstack([x_base, y_base, z_base])           # rows: the robot's axes in camera coordinates
    t[:3, 3] = cam_xyz
    return t


def retilt(t_base_cam, t_base_tilt, pitch_deg):
    """The camera pose turned about the head's tilt axis (t_base_tilt's +y through its origin; + tips the face down)
    until the camera's pitch is `pitch_deg` down. Returns (pose, the turn in degrees)."""
    axis, origin = t_base_tilt[:3, 1] / np.linalg.norm(t_base_tilt[:3, 1]), t_base_tilt[:3, 3]

    def turned(delta):
        k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        r = np.eye(3) + math.sin(delta) * k + (1 - math.cos(delta)) * (k @ k)    # Rodrigues
        t = np.eye(4)
        t[:3, :3] = r @ t_base_cam[:3, :3]
        t[:3, 3] = r @ (t_base_cam[:3, 3] - origin) + origin
        return t

    delta = 0.0
    for _ in range(5):                  # the pitch is not quite linear in the turn when the head is panned
        up = turned(delta)[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        delta += math.radians(pitch_deg - camera_tilt(up)[0])
    return turned(delta), math.degrees(delta)


def camera_tilt(up_cam):
    """(pitch down, roll right) of the camera, degrees, from the world's up in its optical frame."""
    up = np.asarray(up_cam, float) / np.linalg.norm(up_cam)
    return math.degrees(math.asin(-up[2])), math.degrees(math.atan2(up[0], -up[1]))


def grasp_to_tcp(grasp, t_base_cam, current=None):
    """The TCP pose (position, 3x3) in the arm model's frame for a grasp_object result (camera frame, GraspNet).

    Of the two equivalent jaw orientations, the one nearer `current` (the hand's present rotation) is taken, so the
    wrist does not turn half a revolution for nothing.
    """
    p, q = grasp["position"], grasp["orientation"]
    p_cam = np.array([p["x"], p["y"], p["z"]])
    r_cam = quat_to_mat(q["x"], q["y"], q["z"], q["w"])
    position = t_base_cam[:3, :3] @ p_cam + t_base_cam[:3, 3]
    rotation = t_base_cam[:3, :3] @ r_cam @ GRASP_TO_TCP
    if current is not None and angle_between(current, rotation @ FLIP_JAWS) < angle_between(current, rotation):
        rotation = rotation @ FLIP_JAWS
    return position, rotation


# -- the simulated arm's hand -------------------------------------------------------------------------------------

class HandModel:
    """Forward kinematics of openarm_right_hand_tcp from the simulation's joint_states, with the model it runs."""

    def __init__(self, model_spec="openarm_assets:bimanual"):
        import mujoco
        from gafro_ri_ros2_bridge.models import resolve_model
        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_path(resolve_model(model_spec))
        self.data = mujoco.MjData(self.model)
        self.data.qpos[:] = self.model.key_qpos[self.model.key("ready").id]
        self.tcp = self.model.body("openarm_right_hand_tcp").id
        self.joints = {self.model.joint(i).name: self.model.jnt_qposadr[i] for i in range(self.model.njnt)}

    def set(self, names, positions):
        for n, v in zip(names, positions):
            if n in self.joints:
                self.data.qpos[self.joints[n]] = v

    def hand(self):
        self.mj.mj_kinematics(self.model, self.data)
        return self.data.xpos[self.tcp].copy(), self.data.xmat[self.tcp].reshape(3, 3).copy()


# -- ROS ----------------------------------------------------------------------------------------------------------

def spin_in_thread(rclpy, node, context):
    from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
    ex = SingleThreadedExecutor(context=context)
    ex.add_node(node)

    def spin():
        try:
            ex.spin()
        except ExternalShutdownException:   # the context was shut down: the normal end
            pass

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    SPINNING.append((context, thread))
    return ex


SPINNING = []   # (context, thread) of every executor started, so every way out can stop them


def shutdown_all(rclpy):
    """Shut every context down and let its executor thread end: a thread left spinning in an exiting interpreter
    aborts it ("terminate called without an active exception")."""
    for context, thread in SPINNING:
        rclpy.try_shutdown(context=context)
        thread.join(timeout=2.0)


class TfReader:
    """A tf2 buffer filled from /tf and /tf_static, plus any `extra` TF topics (a *tf_static name is read as static).

    A frame an `extra` topic publishes overrides the robot's own publisher of that frame, whichever arrives first: the extras
    are a description under test (a robot_state_publisher on a corrected URDF) beside the robot's deployed one, and two
    publishers of one transform otherwise leave the buffer with whichever came last."""

    def __init__(self, rclpy, node, extra=()):
        import tf2_ros
        from rclpy.duration import Duration
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from tf2_msgs.msg import TFMessage
        self.rclpy, self.tf2 = rclpy, tf2_ros
        self.buf = tf2_ros.Buffer(cache_time=Duration(seconds=30))
        static_qos = QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
        self.overridden = set()     # child frames an extra topic has published
        for topic in ("/tf", "/tf_static", *extra):
            static = topic.endswith("tf_static")
            node.create_subscription(TFMessage, topic, self._adder(static, topic in extra), static_qos if static else 100)

    def _adder(self, static, override):
        def add(msg):
            for t in msg.transforms:
                if override:
                    self.overridden.add(t.child_frame_id)
                elif t.child_frame_id in self.overridden:
                    continue
                (self.buf.set_transform_static if static else self.buf.set_transform)(t, "grasp_to_sim")
        return add

    def lookup(self, target, source, stamp_s=None, timeout=3.0):
        """The 4x4 taking `source` coordinates to `target`, at `stamp_s` (seconds) or the latest. Raises LookupError
        saying why."""
        when = self.rclpy.time.Time() if stamp_s is None else self.rclpy.time.Time(nanoseconds=int(stamp_s * 1e9))
        deadline = time.monotonic() + timeout
        while True:
            try:
                t = self.buf.lookup_transform(target, source, when)
                break
            except self.tf2.TransformException as error:
                if time.monotonic() > deadline:
                    raise LookupError(str(error).splitlines()[0]) from None
                time.sleep(0.05)
        r, p = t.transform.rotation, t.transform.translation
        m = np.eye(4)
        m[:3, :3] = quat_to_mat(r.x, r.y, r.z, r.w)
        m[:3, 3] = (p.x, p.y, p.z)
        return m


def measure_up(node, tf, seconds=1.0):
    """The world's up in camera_color_optical_frame: the mean accelerometer reading over `seconds`, rotated by the
    driver's colour->IMU transform. (None, why) if either is missing."""
    from sensor_msgs.msg import Imu
    samples = []
    sub = node.create_subscription(Imu, "/camera/gyro_accel/sample",
                                   lambda m: samples.append((m.linear_acceleration.x, m.linear_acceleration.y,
                                                             m.linear_acceleration.z)), 50)
    deadline = time.monotonic() + max(seconds, 3.0)
    while time.monotonic() < deadline and len(samples) < 20:
        time.sleep(0.05)
    node.destroy_subscription(sub)
    try:
        imu = tf.lookup(CAMERA_FRAME, "camera_accel_gyro_optical_frame", timeout=1.0)
    except LookupError as error:
        return None, f"no colour->IMU transform ({error})"
    if len(samples) < 5:
        return None, f"only {len(samples)} IMU samples"
    accel = np.mean(np.array(samples), axis=0)   # at rest the accelerometer reads +g, pointing up
    up = imu[:3, :3] @ accel
    return up / np.linalg.norm(up), f"{len(samples)} IMU samples, |a| = {np.linalg.norm(accel):.2f} m/s^2"


def call_grasp(rclpy, node, request, timeout):
    """grasp_object, with the settle-and-resend perception_call.py uses. Returns (code name, message, data)."""
    from robot_api_interfaces.msg import ResultCode
    from robot_api_interfaces.srv import RobotCommand
    codes = {getattr(ResultCode, n): n for n in dir(ResultCode) if n.isupper() and isinstance(getattr(ResultCode, n), int)}
    client = node.create_client(RobotCommand, "/perception/grasp_object")
    if not client.wait_for_service(timeout_sec=10.0):
        raise SystemExit("/perception/grasp_object is not available (is the perception stack up? grasp_stack.sh status)")
    time.sleep(0.5)   # let discovery settle: a call made the moment the service appears can lose its reply
    for attempt in range(1, 4):
        fut = client.call_async(RobotCommand.Request(req=json.dumps(request)))
        deadline = time.monotonic() + timeout
        while not fut.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        if fut.done() and fut.result() is not None:
            res = fut.result()
            code = res.result_code.result_code
            return codes.get(code, str(code)), res.result_code.message, json.loads(res.data or "{}")
        client.remove_pending_request(fut)
        print(f"no reply from grasp_object within {timeout:.0f} s (try {attempt} of 3); sending again", file=sys.stderr)
    raise SystemExit("no reply from /perception/grasp_object after 3 tries")


class SimArm:
    """The simulation's side: publishes right/target and follows joint_states, on the simulation's domain."""

    def __init__(self, rclpy, context, reference, hand_model):
        from geometry_msgs.msg import PoseStamped
        from rclpy.node import Node
        from sensor_msgs.msg import JointState
        self.PoseStamped = PoseStamped
        self.node = Node("grasp_to_sim", context=context)
        self.pub = self.node.create_publisher(PoseStamped, f"{reference}/target", 1)
        self.model = hand_model
        self.lock = threading.Lock()
        self.stamp = None
        self.node.create_subscription(JointState, "joint_states", self._on_joints, 1)

    def _on_joints(self, msg):
        with self.lock:
            self.model.set(msg.name, msg.position)
            self.stamp = time.monotonic()

    def hand(self):
        with self.lock:
            return self.model.hand()

    def wait_for_state(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while self.stamp is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.stamp is not None

    def send(self, position, rotation):
        m = self.PoseStamped()
        m.header.stamp = self.node.get_clock().now().to_msg()
        m.pose.position.x, m.pose.position.y, m.pose.position.z = (float(v) for v in position)
        m.pose.orientation.x, m.pose.orientation.y, m.pose.orientation.z, m.pose.orientation.w = (
            float(v) for v in mat_to_quat(rotation))
        self.pub.publish(m)

    def move(self, position, rotation, duration, hold, rate=50.0):
        """A minimum-jerk line from the hand's present pose to the target, then held; targets at `rate` Hz, well
        inside the bridge's 0.5 s freshness horizon."""
        p0, r0 = self.hand()
        q0, q1 = mat_to_quat(r0), mat_to_quat(rotation)
        t0 = time.monotonic()
        while True:
            t = time.monotonic() - t0
            if t > duration + hold:
                break
            s = minimum_jerk(min(t / duration, 1.0)) if duration > 0 else 1.0
            self.send(p0 + s * (np.asarray(position) - p0), quat_to_mat(*slerp(q0, q1, s)))
            time.sleep(1.0 / rate)
        p, r = self.hand()
        return float(np.linalg.norm(p - position)), angle_between(r, rotation), p


def fmt(v):
    return "(" + ", ".join(f"{x:+.3f}" for x in v) + ")"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="bottle")
    ap.add_argument("--arm-frame", default=ARM_FRAME, help="the TF frame at the arm model's root")
    ap.add_argument("--tf-topics", default="", help="extra TF topics, comma-separated (e.g. a private robot_state_publisher's)")
    ap.add_argument("--head-tilt-from", choices=("imu", "neck"), default="imu",
                    help="imu: the head's tilt is the camera accelerometer's measurement (the neck reports its commanded angle, "
                         "which its stop can keep it short of); neck: the neck's report as TF has it")
    ap.add_argument("--max-tilt-disagree-deg", type=float, default=3.0,
                    help="stop when TF's up and the camera IMU's differ by more than this")
    ap.add_argument("--cam-xyz", default=None, help="MANUAL mode, instead of TF: the colour camera's position in the arm frame, metres")
    ap.add_argument("--cam-yaw-deg", type=float, default=0.0, help="MANUAL mode: the camera's yaw, positive toward the robot's left")
    ap.add_argument("--up-cam", default=None, help="x,y,z: the world's up in the camera frame, instead of reading the IMU")
    ap.add_argument("--approach", type=float, default=0.10, help="pre-grasp distance back along the approach, metres")
    ap.add_argument("--move-s", type=float, default=3.0, help="seconds for each move")
    ap.add_argument("--hold-s", type=float, default=2.0, help="seconds to hold each target before measuring")
    ap.add_argument("--tolerance-mm", type=float, default=10.0)
    ap.add_argument("--tolerance-deg", type=float, default=5.0)
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=77)
    ap.add_argument("--reference", default="right", help="the simulation's reference name: targets go to <reference>/target")
    ap.add_argument("--model", default=REPO_MODEL if os.path.exists(REPO_MODEL) else "openarm_assets:bimanual",
                    help="the arm model for the hand's forward kinematics: the one the arm service runs (default: the repo's)")
    ap.add_argument("--from-json", default=None, help="replay a --save file, or a bare grasp_object response, instead of calling perception")
    ap.add_argument("--save", default=None, help="write the response, the transform used and the targets here (JSON)")
    ap.add_argument("--dry-run", action="store_true", help="perception and the transform only; send nothing")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()
    if args.sim_domain == args.robot_domain:
        raise SystemExit("--sim-domain must differ from the robot's domain: the simulation's joint_states would reach the robot")

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from rclpy.context import Context
    from rclpy.node import Node

    # -- a replay brings its own response, and the transform and up direction it was run with ---------------------
    up_cam, data, t_base_cam, source = None, None, None, None
    if args.from_json:
        with open(args.from_json) as f:
            saved = json.load(f)
        data = saved.get("response", saved.get("data", saved))
        if "t_arm_cam" in saved:
            t_base_cam, source = np.array(saved["t_arm_cam"]), f"replayed from {args.from_json} ({saved.get('transform_source')})"
        if "up_cam" in saved:
            up_cam = np.array(saved["up_cam"])
    if args.up_cam:
        up_cam = np.array([float(v) for v in args.up_cam.split(",")])
    if up_cam is not None:
        up_cam = up_cam / np.linalg.norm(up_cam)
    if args.cam_xyz:
        t_base_cam, source = None, "MANUAL (--cam-xyz)"

    # -- the robot's side: the camera's up direction, the grasp and the transform ------------------------------------
    robot_ctx = None
    if data is None or up_cam is None or (t_base_cam is None and not args.cam_xyz):
        robot_ctx = Context()
        rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
        robot_node = Node("grasp_to_sim_perception", context=robot_ctx)
        tf = TfReader(rclpy, robot_node, [t for t in args.tf_topics.split(",") if t])
        spin_in_thread(rclpy, robot_node, robot_ctx)
    if up_cam is None:
        up_cam, how = measure_up(robot_node, tf)
        if up_cam is None:
            raise SystemExit(f"could not measure the camera's up direction ({how}); give --up-cam x,y,z")
        print(f"camera up (IMU, {how}): {fmt(up_cam)}")
    pitch, roll = camera_tilt(up_cam)
    print(f"camera tilt (IMU): {pitch:.1f} deg down, roll {roll:+.1f} deg")

    if data is None:
        request = {"label": args.label, "up_cam": [round(float(v), 4) for v in up_cam]}
        code, message, data = call_grasp(rclpy, robot_node, request, args.timeout)
        print(f"grasp_object {args.label!r}: {code}: {message}")
        if code != "SUCCESS":
            return 1

    if args.cam_xyz:
        t_base_cam = camera_to_base(up_cam, np.array([float(v) for v in args.cam_xyz.split(",")]), args.cam_yaw_deg)
    elif t_base_cam is None:
        stamp = data.get("stamp")
        try:
            t_base_cam = tf.lookup(args.arm_frame, CAMERA_FRAME, stamp)
            source = f"TF {args.arm_frame} <- {CAMERA_FRAME} at the image's stamp"
        except LookupError as error:
            try:
                tf.lookup(args.arm_frame, CAMERA_FRAME, None, timeout=0.5)
                why = ("the chain exists but not at the image's time: the head's transform is stale. Is the neck "
                       "publishing /head/joint_states, and head_joint_relay forwarding it?")
            except LookupError:
                why = (f"there is no chain at all ({error}). It needs robot_state_publisher with the current "
                       "description/opendubs/opendubs.urdf (femto_mount_link, camera_link) and the head's joint states "
                       "from head_joint_relay, plus the Orbbec driver's camera_link frames")
            raise SystemExit(f"no transform {args.arm_frame} <- {CAMERA_FRAME}: {why}")
        tf_up = t_base_cam[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        tf_pitch, tf_roll = camera_tilt(tf_up)
        if args.head_tilt_from == "imu":
            # The neck reports the tilt it was COMMANDED to (no encoder): against its stop the head is short of it. The
            # camera's accelerometer measures the real one, so turn the camera about the head's own tilt axis (TF's
            # head_tilt_link, which carries the live pan) until its pitch is the measured pitch.
            try:
                t_tilt = tf.lookup(args.arm_frame, "head_tilt_link", data.get("stamp"))
            except LookupError as error:
                raise SystemExit(f"no transform {args.arm_frame} <- head_tilt_link ({error}): needed for --head-tilt-from imu")
            t_base_cam, delta = retilt(t_base_cam, t_tilt, camera_tilt(up_cam)[0])
            print(f"camera tilt (neck): {tf_pitch:.1f} deg down; measured (IMU) {camera_tilt(up_cam)[0]:.1f}: "
                  f"turned {delta:+.1f} deg about head_tilt to the measured tilt")
            tf_up = t_base_cam[:3, :3].T @ np.array([0.0, 0.0, 1.0])
            tf_pitch, tf_roll = camera_tilt(tf_up)
        disagree = math.degrees(math.acos(max(-1.0, min(1.0, float(tf_up @ up_cam)))))
        print(f"camera tilt (TF):  {tf_pitch:.1f} deg down, roll {tf_roll:+.1f} deg -> {disagree:.1f} deg from the IMU's")
        if disagree > args.max_tilt_disagree_deg:
            raise SystemExit(f"TF and the camera's IMU disagree about up by {disagree:.1f} deg (limit "
                             f"{args.max_tilt_disagree_deg}): the neck is not where it reports, or the robot is not level. "
                             "Not sending a grasp through a transform that is wrong.")
    if robot_ctx is not None:
        rclpy.try_shutdown(context=robot_ctx)
    print(f"camera at {fmt(t_base_cam[:3, 3])} m in {args.arm_frame}; transform: {source}")

    p, q = data["position"], data["orientation"]
    print(f"grasp in camera frame: {fmt([p['x'], p['y'], p['z']])} m, width {data.get('width', float('nan')):.3f} m, "
          f"grasp score {data.get('grasp_score')}, tilt from floor {data.get('tilt_from_floor_deg')}, "
          f"mode {data.get('mode')!r}")

    # -- the simulation's side -------------------------------------------------------------------------------------
    hand_model = HandModel(args.model)
    sim = None
    if not args.dry_run:
        sim_ctx = Context()
        rclpy.init(context=sim_ctx, domain_id=args.sim_domain)
        sim = SimArm(rclpy, sim_ctx, args.reference, hand_model)
        spin_in_thread(rclpy, sim.node, sim_ctx)
        if not sim.wait_for_state():
            raise SystemExit(f"no joint_states on domain {args.sim_domain}: start the simulation first (grasp_to_sim.sh sim)")
    p_hand, r_hand = sim.hand() if sim else hand_model.hand()

    grasp_p, grasp_r = grasp_to_tcp(data, t_base_cam, current=r_hand)
    pre_p = grasp_p - args.approach * grasp_r[:, 2]
    reach = float(np.linalg.norm(grasp_p - SHOULDER))
    approach = grasp_r[:, 2]
    print(f"hand now:  {fmt(p_hand)} m")
    print(f"pre-grasp: {fmt(pre_p)} m")
    print(f"grasp:     {fmt(grasp_p)} m, approach {fmt(approach)} "
          f"({math.degrees(math.asin(max(-1, min(1, -approach[2])))):.0f} deg below horizontal)")
    print(f"distance from the right shoulder: {reach:.3f} m" + (f"  -- WARNING: beyond about {REACH_WARN_M} m the arm cannot reach" if reach > REACH_WARN_M else ""))
    if args.save:
        with open(args.save, "w") as f:
            json.dump({"response": data, "up_cam": up_cam.tolist(), "t_arm_cam": t_base_cam.tolist(), "transform_source": source,
                       "grasp_tcp": {"position": grasp_p.tolist(), "quaternion_xyzw": mat_to_quat(grasp_r).tolist()},
                       "pre_grasp_tcp": {"position": pre_p.tolist(), "quaternion_xyzw": mat_to_quat(grasp_r).tolist()}}, f, indent=1)
        print(f"saved {args.save}")
    if args.dry_run:
        return 0

    ok = True
    for name, target in (("pre-grasp", pre_p), ("grasp", grasp_p)):
        err_m, err_deg, reached = sim.move(target, grasp_r, args.move_s, args.hold_s)
        good = err_m * 1000 <= args.tolerance_mm and err_deg <= args.tolerance_deg
        ok &= good
        print(f"{name}: hand at {fmt(reached)} m, off by {err_m * 1000:.1f} mm and {err_deg:.1f} deg -> "
              f"{'REACHED' if good else 'NOT REACHED'}")
    print("verdict:", "the simulated arm reached the grasp" if ok else
          "the simulated arm did not reach the grasp (out of reach, a joint limit, or a wrong camera transform)")
    rclpy.try_shutdown(context=sim_ctx)
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        code = main()
    finally:
        if SPINNING:
            import rclpy
            shutdown_all(rclpy)
    sys.exit(code)
