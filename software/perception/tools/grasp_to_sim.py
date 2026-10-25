"""Ask perception for a grasp and send it to the gafro OpenArm simulation (right arm), then say whether the hand got there.

  grasp_to_sim.sh sim                                    # terminal 1: the simulation and its viewer, on its own ROS domain
  grasp_to_sim.sh run --label bottle --cam-xyz 0.05,0,1.00   # terminal 2: perception -> transform -> simulated arm

What it does:
  1. Reads the camera's accelerometer (/camera/gyro_accel/sample) and the driver's colour->IMU transform, so the camera's
     tilt and roll are measured, not guessed. That also gives `up_cam`, which grasp_object uses to prefer level grasps.
  2. Calls /perception/grasp_object on the robot's domain (0). The pose comes back in camera_color_optical_frame
     (x right, y down, z forward) with the GraspNet gripper convention (x approach, y closing).
  3. Puts it in the arm model's frame (openarm_body_link0: x forward, y left, z up), the frame the simulation's
     `right/target` is read in, and turns the gripper axes into the TCP's (openarm_right_hand_tcp: z approach,
     y closing).
  4. Moves the simulated hand to a pre-grasp pose --approach metres back along the approach, then onto the grasp,
     and reports the position and angle error the simulated arm reached.

The camera's POSITION and YAW are not measurable from the IMU: give them with --cam-xyz (metres, in the arm model's
frame, lift fully down) and --cam-yaw-deg (0 = looking straight ahead, positive = turned to the robot's left). The
default --cam-xyz is an estimate from the CAD (the head's pan point is at 0, 0, 0.888) and says so when used.

The simulation runs on ROS domain --sim-domain (77), not on the robot's: the bridge publishes `joint_states`, and the
robot's own /joint_states must not see simulated joints. Nothing here reaches a real arm: the gafro bridge refuses
hardware, and this tool only publishes on the simulation's domain.

  --dry-run          perception and the transform only; prints the targets, sends nothing
  --from-json FILE   use a saved grasp_object response (or a --save file's `response`) instead of calling perception;
                     with --up-cam as well, nothing on the robot's domain is touched
"""

import argparse
import json
import math
import os
import sys
import threading
import time

import numpy as np

DEFAULT_CAM_XYZ = (0.05, 0.0, 1.00)   # ESTIMATE: on top of the head's display; measure it
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

    threading.Thread(target=spin, daemon=True).start()
    return ex


def measure_up(rclpy, node, seconds=1.0):
    """The world's up in camera_color_optical_frame: the mean accelerometer reading over `seconds`, rotated by the
    driver's colour->IMU transform. None if either is missing."""
    import tf2_ros
    from sensor_msgs.msg import Imu
    samples = []
    sub = node.create_subscription(Imu, "/camera/gyro_accel/sample",
                                   lambda m: samples.append((m.linear_acceleration.x, m.linear_acceleration.y,
                                                             m.linear_acceleration.z)), 50)
    buf = tf2_ros.Buffer()
    tf2_ros.TransformListener(buf, node)
    deadline = time.monotonic() + max(seconds, 3.0)
    tf = None
    while time.monotonic() < deadline and (tf is None or len(samples) < 20):
        time.sleep(0.05)
        if tf is None:
            try:
                tf = buf.lookup_transform("camera_color_optical_frame", "camera_accel_gyro_optical_frame",
                                          rclpy.time.Time())
            except tf2_ros.TransformException:
                pass
    node.destroy_subscription(sub)
    if tf is None or len(samples) < 5:
        return None, f"IMU samples {len(samples)}, colour->IMU transform {'found' if tf else 'missing'}"
    r = tf.transform.rotation
    accel = np.mean(np.array(samples), axis=0)   # at rest the accelerometer reads +g, pointing up
    up = quat_to_mat(r.x, r.y, r.z, r.w) @ accel
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
    ap.add_argument("--cam-xyz", default=None, help="the camera's optical centre in the arm model's frame, metres: x,y,z")
    ap.add_argument("--cam-yaw-deg", type=float, default=0.0)
    ap.add_argument("--up-cam", default=None, help="x,y,z: the world's up in the camera frame, instead of reading the IMU")
    ap.add_argument("--approach", type=float, default=0.10, help="pre-grasp distance back along the approach, metres")
    ap.add_argument("--move-s", type=float, default=3.0, help="seconds for each move")
    ap.add_argument("--hold-s", type=float, default=2.0, help="seconds to hold each target before measuring")
    ap.add_argument("--tolerance-mm", type=float, default=10.0)
    ap.add_argument("--tolerance-deg", type=float, default=5.0)
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=77)
    ap.add_argument("--reference", default="right", help="the simulation's reference name: targets go to <reference>/target")
    ap.add_argument("--from-json", default=None, help="a saved grasp_object response (its `data`) instead of calling perception")
    ap.add_argument("--save", default=None, help="write the response and the computed targets here (JSON)")
    ap.add_argument("--dry-run", action="store_true", help="perception and the transform only; send nothing")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()
    if args.sim_domain == args.robot_domain:
        raise SystemExit("--sim-domain must differ from the robot's domain: the simulation's joint_states would reach the robot")

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from rclpy.context import Context
    from rclpy.node import Node

    if args.cam_xyz:
        cam_xyz = np.array([float(v) for v in args.cam_xyz.split(",")])
    else:
        cam_xyz = np.array(DEFAULT_CAM_XYZ)
        print(f"WARNING: --cam-xyz not given; using the estimate {fmt(cam_xyz)} m. Measure the camera's position for a real answer.")

    # -- the robot's side: the camera's up direction and the grasp ------------------------------------------------
    up_cam, data = None, None
    if args.up_cam:
        up_cam = np.array([float(v) for v in args.up_cam.split(",")])
        up_cam /= np.linalg.norm(up_cam)
    robot_ctx = None
    if args.from_json is None or up_cam is None:
        robot_ctx = Context()
        rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
        robot_node = Node("grasp_to_sim_perception", context=robot_ctx)
        spin_in_thread(rclpy, robot_node, robot_ctx)
    if args.from_json:
        with open(args.from_json) as f:
            data = json.load(f)
        data = data.get("response", data.get("data", data))
    if up_cam is None:
        up_cam, how = measure_up(rclpy, robot_node)
        if up_cam is None:
            raise SystemExit(f"could not measure the camera's up direction ({how}); give --up-cam x,y,z")
        print(f"camera up (measured, {how}): {fmt(up_cam)}")
    pitch, roll = camera_tilt(up_cam)
    print(f"camera tilt: {pitch:.1f} deg down, roll {roll:+.1f} deg; yaw {args.cam_yaw_deg:+.1f} deg (given); "
          f"position {fmt(cam_xyz)} m")

    if data is None:
        request = {"label": args.label, "up_cam": [round(float(v), 4) for v in up_cam]}
        code, message, data = call_grasp(rclpy, robot_node, request, args.timeout)
        print(f"grasp_object {args.label!r}: {code}: {message}")
        if code != "SUCCESS":
            return 1
    if robot_ctx is not None:
        rclpy.try_shutdown(context=robot_ctx)

    t_base_cam = camera_to_base(up_cam, cam_xyz, args.cam_yaw_deg)
    p, q = data["position"], data["orientation"]
    print(f"grasp in camera frame: {fmt([p['x'], p['y'], p['z']])} m, width {data.get('width', float('nan')):.3f} m, "
          f"grasp score {data.get('grasp_score', float('nan')):.3f}, tilt from floor {data.get('tilt_from_floor_deg')}, "
          f"mode {data.get('mode')!r}")

    # -- the simulation's side -------------------------------------------------------------------------------------
    hand_model = HandModel()
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
            json.dump({"response": data, "up_cam": up_cam.tolist(), "cam_xyz": cam_xyz.tolist(), "cam_yaw_deg": args.cam_yaw_deg,
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
    sys.exit(main())
