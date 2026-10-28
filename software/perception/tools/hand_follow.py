"""Follow a person's palm with the simulated right arm: the hand tracker's palm, through the robot's frames, onto right/target.

  hand_follow.sh up | down            everything (camera if needed, hand tracker, simulated arm, this node) | all but the camera
  hand_follow.sh start [left|right]   /perception/hand_follow/start: following on (returns at once)
  hand_follow.sh stop                 /perception/hand_follow/stop: following and tracking off, the arm holds
  hand_follow.sh frames [--save F]    the camera -> arm transform now, printed (and written as JSON), nothing followed

THE SERVICES, robot_api_interfaces like the other /perception services, on the robot's domain:

  /perception/hand_follow/start   RobotCommand  {} or {"hand": "left" | "right"}  checks that the simulated arm, the hand
                                  tracker and the camera -> arm transform are there (SERVICE_NOT_RUNNING says which is
                                  not), subscribes to the palm, turns following on, and returns. It does not wait for a
                                  palm: with nobody in view the arm holds until one appears.
  /perception/hand_follow/stop    RobotCommand  {}   following off and the palm unsubscribed (the tracker idles); returns.
  /perception/hand_follow/status  RobotQuery         following, hand, state, palm age, the hand's position, the transform.
Both commands are idempotent: start while following and stop while stopped succeed.

THE FRAMES, end to end:

  palm      camera_color_optical_frame   x right, y down, z forward, metres: the hand tracker's PointStamped, stamped with
                                         its image (hand_tracker/node.py)
  camera -> arm_mount                    T_arm_cam, a 4x4, from one of --transform:
              joints (default)           the head's two joints made here: head_pan (lift_platform -> head_pan_link) and
                                         head_tilt (head_pan_link -> head_tilt_link), each origin * rotation(axis, angle)
                                         with the origin and axis of the robot's own /robot_description and the angle of
                                         /head/joint_states (the neck's report); every fixed link (arm_mount on
                                         lift_platform, femto_mount_link, camera_link, and the driver's camera_link ->
                                         camera_color_optical_frame) from /tf_static. Needs no robot_state_publisher for
                                         the head and no head_joint_relay. The lift cancels: arm_mount and head_pan both
                                         ride lift_platform.
              tf                         the robot's TF as it is (needs the head's frames on /tf)
              calib FILE                 a fixed transform (cam_to_arm JSON: R, t; `frames --save` writes one; the
                                         nominal one, hand_tracker/cam_to_arm_nominal.json, holds with the neck centred)
            --head-tilt-from imu (default): the neck has no encoder and reports the tilt it was COMMANDED to, which its
            stop can keep it short of (the case of a head tilted fully down). The camera's accelerometer measures the real
            tilt; the transform is turned about head_tilt_link's axis until the camera's pitch is the measured one
            (grasp_to_sim.retilt). Then TF's up and the IMU's must agree within --max-tilt-disagree-deg, or nothing is
            followed: a robot that is not level, or a transform that is wrong.
  arm_mount                              the robot description's frame at the arm model's root (openarm_body_link0):
                                         x forward, y left, z up. right/target is a PoseStamped of the TCP
                                         (openarm_right_hand_tcp) in this frame.

THE TARGET, by --mode:
  body (default)      the palm against the person's own shoulder (V2's head keeps it in view), put against the arm's
                      shoulder line (--body-origin), times --gain: toward the robot is its forward, sideways and up the
                      same way in the room. Full range wherever the person stands. No shoulder seen for 1 s: absolute.
  absolute            the palm itself, in arm_mount: direct. A palm beyond --max-reach of the shoulder is reached TOWARD:
                      the point at the reach on the line from the shoulder to the palm (--standoff stops short of it).
  relative            the arm's hand moves as the person's hand moves, from where each was when tracking (re)started:
                      goal = hand_then + --gain * M (palm_now - palm_then), both in arm_mount, M by --mapping:
                      body the person's own frame, facing the robot: their right is the arm's right, toward the robot is
                      its forward, up is up; world: the same direction in the room.
Then inside --box (a generous safety box: the workspace is the IK's), low-passed (--smooth-s) and speed-limited
(--max-speed), and solved by PositionIk for the right arm alone: the pose of the solution is sent, its orientation the
IK's.

WHEN THE ARM MOVES (V2), trick or treat, one person at a time, the nearest:
  head       on start the head tilts to put the nearest person's shoulders in the middle of the picture (searching with
             a slow sweep if nobody is seen), and keeps them there (HeadTilt). THE HEAD MOVES BY ITSELF; it does not move
             while held, touched, aimed with the joystick (CH7 in pan/tilt), with the dashboard's own tracking on, or with
             a real arm's J2 over 142 deg. Until it has settled, the arm holds where it is.
  offered    the palm held out from that person's own shoulder (offered()): followed. Not held out, or no shoulder
             seen, for --away-s -> neutral, the arm down. No palm -> hold where it is. (--close-m: V1's "within 0.5 m
             of the arm" rule, off by default; --no-head: V1.)

HOLDING, so the arm never drops: once a target has been sent, every tick sends one. Stopped, a palm older than --stale-s
(occluded, out of view, a rough palm, the tracker stopped) or no transform: the LAST COMMANDED pose is sent again, latched
(never the measured hand, which a sagging arm would walk down). When the palm comes back, following goes on from the held
pose, low-passed and speed-limited, so there is no jump. When this process ends (Ctrl-C, TERM from `hand_follow.sh down`)
it tells the arm service to `hold`: the follow ends and the arm stays where it is, by the service's own controller.

Nothing here reaches a real arm: targets go only to --sim-domain, which must differ from the robot's. The real arm service
reads the same right/target, so what is tuned here is what it would follow.

--dummy: no tracker; a palm drawn on a circle in front of the camera (--dummy-z metres out), through the same frames.
"""

import argparse
import json
import math
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grasp_to_sim as g2s  # noqa: E402  (TfReader, SimArm, HandModel, retilt, camera_tilt, the rotations)

ARM_FRAME = g2s.ARM_FRAME                 # arm_mount
CAMERA_FRAME = g2s.CAMERA_FRAME           # camera_color_optical_frame
SHOULDER = g2s.SHOULDER                   # openarm_right_link0 in the arm model's frame
HEAD_JOINTS = ("head_pan", "head_tilt")
IMU_FRAME = "camera_accel_gyro_optical_frame"


def rpy_to_mat(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def axis_angle(axis, q):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(q) * k + (1 - math.cos(q)) * (k @ k)


def urdf_joints(urdf_xml, names):
    """{name: (parent, child, origin 4x4, axis)} of the named joints of a URDF string."""
    out = {}
    for j in ET.fromstring(urdf_xml).findall("joint"):
        if j.get("name") not in names:
            continue
        o = j.find("origin")
        xyz = [float(v) for v in (o.get("xyz", "0 0 0") if o is not None else "0 0 0").split()]
        rpy = [float(v) for v in (o.get("rpy", "0 0 0") if o is not None else "0 0 0").split()]
        a = j.find("axis")
        origin = np.eye(4)
        origin[:3, :3], origin[:3, 3] = rpy_to_mat(*rpy), xyz
        out[j.get("name")] = (j.find("parent").get("link"), j.find("child").get("link"), origin,
                              [float(v) for v in (a.get("xyz") if a is not None else "1 0 0").split()])
    missing = set(names) - set(out)
    if missing:
        raise ValueError(f"the robot description has no joint {', '.join(sorted(missing))}")
    return out


def joint_transform(origin, axis, q):
    t = np.eye(4)
    t[:3, :3] = axis_angle(axis, q)
    return origin @ t


class Frames:
    """T_arm_cam, the camera's pose in arm_mount, by --transform; and the IMU's up for the tilt and the check."""

    def __init__(self, rclpy, node, mode, calib=None, tf_topics=(), head_tilt_from="imu", max_disagree_deg=3.0,
                 head_stale_s=2.0):
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import Imu, JointState
        from std_msgs.msg import String
        self.rclpy, self.mode, self.head_tilt_from, self.max_disagree = rclpy, mode, head_tilt_from, max_disagree_deg
        self.head_stale_s = head_stale_s
        self.tf = g2s.TfReader(rclpy, node, tf_topics)
        self.calib = None
        if mode == "calib":
            cal = json.load(open(calib))
            self.calib = np.eye(4)
            self.calib[:3, :3], self.calib[:3, 3] = np.asarray(cal["R"]), np.asarray(cal["t"])
            self.calib_source = f"{calib}: {cal.get('source', '')} (valid for: {cal.get('valid_for', '?')})"
        self.joints, self.description, self.head, self.head_at = None, None, {}, None
        self.accel, self.lock = None, threading.Lock()
        if mode == "joints":
            latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
            # the whole robot's description is /opendubs/robot_description; /robot_description on the robot is the arms'
            for topic in ("/opendubs/robot_description", "/robot_description"):
                node.create_subscription(String, topic, lambda m, t=topic: self._on_description(m, t), latched)
            node.create_subscription(JointState, "/head/joint_states", self._on_head, 10)
        node.create_subscription(Imu, "/camera/gyro_accel/sample", self._on_imu, 50)

    def _on_description(self, msg, topic):
        """The first description that has the head's joints is the one used; one without them is not an error."""
        if self.joints is not None:
            return
        try:
            self.joints = urdf_joints(msg.data, HEAD_JOINTS)
            self.description = topic
        except (ET.ParseError, ValueError):
            pass

    def _on_head(self, msg):
        """The head's two joints into the TF buffer, overriding the robot's own publisher of them (if any)."""
        from geometry_msgs.msg import TransformStamped
        if self.joints is None:
            return
        got = dict(zip(msg.name, msg.position))
        stamp = msg.header.stamp if (msg.header.stamp.sec or msg.header.stamp.nanosec) else self.node_now()
        for name in HEAD_JOINTS:
            if name not in got:
                continue
            parent, child, origin, axis = self.joints[name]
            m = joint_transform(origin, axis, got[name])
            t = TransformStamped()
            t.header.stamp, t.header.frame_id, t.child_frame_id = stamp, parent, child
            t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = (float(v) for v in m[:3, 3])
            q = g2s.mat_to_quat(m[:3, :3])
            t.transform.rotation.x, t.transform.rotation.y, t.transform.rotation.z, t.transform.rotation.w = (float(v) for v in q)
            self.tf.overridden.add(child)
            self.tf.buf.set_transform(t, "hand_follow")
        with self.lock:
            self.head = {n: got[n] for n in HEAD_JOINTS if n in got}
            self.head_at = time.monotonic()

    def node_now(self):
        return self.rclpy.clock.Clock().now().to_msg()

    def _on_imu(self, msg):
        a = np.array([msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z])
        with self.lock:
            self.accel = a if self.accel is None else self.accel + 0.05 * (a - self.accel)   # ~20 samples

    def up_cam(self):
        """The world's up in the colour camera's frame from the accelerometer, or None."""
        with self.lock:
            accel = None if self.accel is None else self.accel.copy()
        if accel is None:
            return None
        try:
            imu = self.tf.lookup(CAMERA_FRAME, IMU_FRAME, timeout=0.2)
        except LookupError:
            return None
        up = imu[:3, :3] @ accel
        return up / np.linalg.norm(up)

    def _lookup(self, target, source, stamp_s):
        """At the image's stamp if the buffer has it, else the latest (the head is held still while following)."""
        if stamp_s is not None:
            try:
                return self.tf.lookup(target, source, stamp_s, timeout=0.02)
            except LookupError:
                pass
        return self.tf.lookup(target, source, None, timeout=0.5)

    def camera_in_arm(self, stamp_s=None):
        """(T_arm_cam, a description of how it was made). Raises LookupError saying why not."""
        info = {}
        if self.mode == "calib":
            t, info["source"] = self.calib.copy(), self.calib_source
        else:
            if self.mode == "joints":
                if self.joints is None:
                    raise LookupError("no robot description with head_pan and head_tilt yet (/opendubs/robot_description, "
                                      "/robot_description): the head's joint origins come from it")
                with self.lock:
                    head, at = dict(self.head), self.head_at
                if at is None or time.monotonic() - at > self.head_stale_s:
                    raise LookupError("the neck is silent: no /head/joint_states for "
                                      f"{'ever' if at is None else f'{time.monotonic() - at:.1f} s'}")
                info["head_deg"] = {n: round(math.degrees(v), 2) for n, v in head.items()}
            t = self._lookup(ARM_FRAME, CAMERA_FRAME, stamp_s)
            info["source"] = f"TF {ARM_FRAME} <- {CAMERA_FRAME}" + (f", head joints from /head/joint_states and {self.description}" if self.mode == "joints" else "")
        up = self.up_cam()
        tf_pitch = g2s.camera_tilt(t[:3, :3].T @ np.array([0.0, 0.0, 1.0]))[0]
        info["pitch_reported_deg"] = round(tf_pitch, 2)
        if up is None:
            raise LookupError("no IMU up yet (/camera/gyro_accel/sample and the colour->IMU transform): "
                              "the transform cannot be checked")
        imu_pitch = g2s.camera_tilt(up)[0]
        info["pitch_imu_deg"] = round(imu_pitch, 2)
        if self.head_tilt_from == "imu" and self.mode != "calib":
            tilt = self._lookup(ARM_FRAME, "head_tilt_link", stamp_s)
            t, delta = g2s.retilt(t, tilt, imu_pitch)
            info["retilt_deg"] = round(delta, 2)
        tf_up = t[:3, :3].T @ np.array([0.0, 0.0, 1.0])
        disagree = math.degrees(math.acos(max(-1.0, min(1.0, float(tf_up @ up)))))
        info["disagree_deg"] = round(disagree, 2)
        if disagree > self.max_disagree:
            raise LookupError(f"the transform's up and the camera IMU's differ by {disagree:.1f} deg (limit "
                              f"{self.max_disagree}): the robot is not level, or the transform is wrong")
        return t, info


def shape_target(p_arm, standoff, min_reach, max_reach, box):
    """The TCP position for a palm at p_arm (arm_mount): `standoff` short of it toward the shoulder, its distance from
    the shoulder kept in [min_reach, max_reach], then inside the box ((x0, x1), (y0, y1), (z0, z1))."""
    d = np.asarray(p_arm, float) - SHOULDER
    n = float(np.linalg.norm(d))
    u = d / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
    r = min(max(n - standoff, min_reach), max_reach)
    target = SHOULDER + r * u
    return np.array([min(max(target[i], box[i][0]), box[i][1]) for i in range(3)])


class PositionIk:
    """Joint angles that put the TCP at a position, orientation left free: damped least squares on the TCP's position
    Jacobian over one arm's 7 joints, inside the joint limits (less `margin`), the null space pulled toward the posture
    the arm had when following started (so the elbow and wrist stay as they were rather than wander). Warm-started from
    its own last solution, so the solution is continuous and never flips branch between ticks.

    The pose of the solution is what is sent: always one the arm can reach, so the arm service's pose controller is never
    asked for one it can only approach into a joint limit (it stalled 150-500 mm short of most of the workspace when the
    orientation was held). An unreachable position is solved to the nearest reachable one."""

    def __init__(self, model_spec, side="right", margin=0.05, damping=0.05, posture_gain=0.1, min_elbow=0.35):
        import mujoco
        from gafro_ri_ros2_bridge.models import resolve_model
        self.mj = mujoco
        self.m = mujoco.MjModel.from_xml_path(resolve_model(model_spec))
        self.d = mujoco.MjData(self.m)
        self.d.qpos[:] = self.m.key_qpos[self.m.key("ready").id]
        self.tcp = self.m.body(f"openarm_{side}_hand_tcp").id
        jids = [self.m.joint(f"openarm_{side}_joint{i}").id for i in range(1, 8)]
        self.names = [f"openarm_{side}_joint{i}" for i in range(1, 8)]
        self.qadr = np.array([self.m.jnt_qposadr[j] for j in jids])
        self.dadr = np.array([self.m.jnt_dofadr[j] for j in jids])
        self.lo, self.hi = self.m.jnt_range[jids, 0] + margin, self.m.jnt_range[jids, 1] - margin
        # The elbow (joint 4) kept bent: straight, the arm is singular and the pose controller oscillates there. Seen on the
        # simulated arm: joint 4 driven to -2.3 deg, the service's guard stopped the follow (VIBRATION in joint 4).
        # At full reach the arm points at the hand a little short of straight instead.
        self.lo[3] = max(self.lo[3], min_elbow)
        self.damping, self.posture_gain = damping, posture_gain
        self.posture = self.d.qpos[self.qadr].copy()
        self.jacp, self.jacr = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))

    def reset_qpos(self, qpos):
        """Start from the arm as it is (the qpos of the same model, kept from joint_states), and pull toward that posture
        from now on."""
        self.d.qpos[self.qadr] = np.clip(np.asarray(qpos)[self.qadr], self.lo, self.hi)
        self.posture = self.d.qpos[self.qadr].copy()

    def fk(self, q):
        """(TCP position, 3x3) at the arm's joints `q`, without touching the solver's state."""
        d = self.mj.MjData(self.m)
        d.qpos[:] = self.d.qpos
        d.qpos[self.qadr] = q
        self.mj.mj_kinematics(self.m, d)
        return d.xpos[self.tcp].copy(), d.xmat[self.tcp].reshape(3, 3).copy()

    def solve(self, target, iterations=15, step_max=0.15):
        """(TCP position, TCP 3x3, joint angles) of the solution for `target` (arm_mount), from the last solution."""
        q = self.d.qpos[self.qadr].copy()
        for _ in range(iterations):
            self.mj.mj_kinematics(self.m, self.d)
            self.mj.mj_comPos(self.m, self.d)
            e = np.asarray(target, float) - self.d.xpos[self.tcp]
            if np.linalg.norm(e) < 2e-4:
                break
            self.mj.mj_jacBody(self.m, self.d, self.jacp, self.jacr, self.tcp)
            J = self.jacp[:, self.dadr]
            Jp = J.T @ np.linalg.inv(J @ J.T + self.damping ** 2 * np.eye(3))
            dq = Jp @ e + (np.eye(7) - Jp @ J) @ (self.posture_gain * (self.posture - q))
            n = np.linalg.norm(dq)
            if n > step_max:
                dq *= step_max / n
            q = np.clip(q + dq, self.lo, self.hi)
            self.d.qpos[self.qadr] = q
        self.mj.mj_kinematics(self.m, self.d)
        return self.d.xpos[self.tcp].copy(), self.d.xmat[self.tcp].reshape(3, 3).copy(), q.copy()


def turn_toward(r_from, r_to, max_angle):
    """r_to, or the rotation `max_angle` (rad) from r_from toward it."""
    angle = math.radians(g2s.angle_between(r_from, r_to))
    if angle <= max_angle:
        return r_to
    q = g2s.slerp(g2s.mat_to_quat(r_from), g2s.mat_to_quat(r_to), max_angle / angle)
    return g2s.quat_to_mat(*q)


MAPPINGS = {"body": np.diag([-1.0, -1.0, 1.0]),   # facing the robot, in the person's own frame: your right is its right,
                                                   # toward the robot is its forward, up is up (a puppet, not a mirror)
            "world": np.eye(3)}                     # the same direction in the room as the hand moves


SHOULDER_REACH = 0.9      # m: a shoulder farther than this from the palm is someone else's (a bystander's)


def offered(palm_arm, shoulder_arm, close_m, reach_min, below, above):
    """(is the hand offered, why), in arm_mount (x away from the robot toward the person, z up). Trick or treat:

      close     the palm within close_m of the arm (its shoulder, SHOULDER): offered, whatever the shoulder. Up close the
                person's shoulders are often out of view, and a hand that near the arm is asking.
      held out  farther, the person's own shoulder seen (within SHOULDER_REACH of the palm) and the palm held out from
                it: at least reach_min nearer the robot, from `below` under to `above` over its height. A hand hanging
                at the side or lying in a lap or on a desk is not.
      else      farther and no shoulder seen: not offered."""
    d = float(np.linalg.norm(palm_arm - SHOULDER))
    if d <= close_m:
        return True, f"close: {d:.2f} m from the arm"
    if shoulder_arm is None or np.linalg.norm(shoulder_arm - palm_arm) > SHOULDER_REACH:
        return False, f"{d:.2f} m from the arm and no shoulder seen"
    reach, height = float(shoulder_arm[0] - palm_arm[0]), float(palm_arm[2] - shoulder_arm[2])
    ok = reach >= reach_min and -below <= height <= above
    return ok, f"{reach:+.2f} m out from the shoulder, {height:+.2f} m above it ({d:.2f} m from the arm)"


class Shoulders:
    """The newest shoulders from the tracker's state (camera xyz per side) and when they came."""

    def __init__(self):
        self.lock, self.value, self.recv = threading.Lock(), {}, 0.0

    def put(self, data):
        with self.lock:
            self.value = {s: None if v is None else np.asarray(v, float) for s, v in (data.get("shoulders") or {}).items()}
            self.mid = data.get("shoulder_mid")
            self.recv = time.monotonic()

    def get(self, side, max_age):
        with self.lock:
            return self.value.get(side) if time.monotonic() - self.recv <= max_age else None

    def get_mid(self, max_age):
        """The nearest person's shoulders as an angle below the camera's axis (dict, see hand_tracker/node.py), or None."""
        with self.lock:
            return getattr(self, "mid", None) if time.monotonic() - self.recv <= max_age else None


def neck_host_from_env():
    """OPENDUBS_HEAD_NECK_HOST, from the environment or /etc/opendubs/robot.env (the dashboard's own setting)."""
    host = os.environ.get("OPENDUBS_HEAD_NECK_HOST")
    if host:
        return host
    try:
        for line in open("/etc/opendubs/robot.env"):
            if line.startswith("OPENDUBS_HEAD_NECK_HOST="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


class HeadTilt:
    """V2: the head tilts to keep the nearest person's shoulders in the middle of the picture, so their shoulders, the
    base the arm's offered check measures from, stay in view. THE HEAD MOVES BY ITSELF.

    The neck's GAZE (head protocol §4), the message the dashboard's own tracking sends, pan held where it is:
      search   nobody seen for --head-search-s: sweep the tilt slowly between the limits until someone is
      centre   the shoulders more than --head-start-deg from the axis: step toward them, at --head-dps
      settled  within --head-stop-deg for --head-settle frames: still; the arm may follow
    As the dashboard's tracking does it (neck_follow.py, a quiet neck): GAZE only while moving, in small steps, then
    silence, so the lease lapses and the neck holds. Nothing accumulates: each step aims at where the shoulders are seen
    now, and frames taken while the head moved are not used.

    It sends nothing, and is not settled, unless all hold: the neck answers NECK; nobody holds it (hold, touch, RC: the
    joystick aims it only with CH7 in pan/tilt), the dashboard's own head tracking is off (two senders would fight), and
    both real arms' J2 are at most 142 deg (arm_clearance.py's rule: a raised arm and a moving head can meet)."""

    def __init__(self, host, shoulders, args, arms_ok):
        self.host, self.shoulders, self.a, self.arms_ok = host, shoulders, args, arms_ok
        self.lock = threading.Lock()
        self.on, self.mode, self.why, self.settled, self.acquired = False, "off", "not started", False, False
        self.tilt = self.pan = None
        self.count, self.sweep_dir, self.moved_at, self.seen_at = 0, 1.0, 0.0, 0.0
        self.moving = True              # at the start: centre the person first
        self.addr = None

    def start(self):
        with self.lock:
            self.on, self.moving, self.count, self.settled, self.acquired = True, True, 0, False, False
            self.seen_at = time.monotonic()

    def stop(self):
        with self.lock:
            self.on = False

    def ask(self, line, timeout=0.3):
        import socket
        try:
            if self.addr is None:
                self.addr = socket.gethostbyname(self.host)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            try:
                s.sendto(line.encode(), (self.addr, 4242))
                return s.recvfrom(512)[0].decode(errors="replace").strip()
            finally:
                s.close()
        except OSError:
            self.addr = None
            return None

    def neck(self):
        """The neck's NECK reply as a dict, or None."""
        r = self.ask("NECK")
        if not r or not r.startswith("OK neck"):
            return None
        return dict(kv.split("=", 1) for kv in r.split()[2:] if "=" in kv)

    def blocked(self):
        """Why the head must not move now, or None. Also refreshes where the neck points."""
        n = self.neck()
        if n is None:
            return f"the neck ({self.host}) does not answer NECK"
        if n.get("hold", "off") != "off":
            return f"the neck is held ({n.get('hold_by')})"
        if n.get("owner") in ("hold", "touch", "rc", "park"):
            return f"the neck is owned by {n.get('owner')}"
        if self.tilt is None or n.get("owner") != "gaze":
            self.pan, self.tilt = float(n["pan"]), float(n["tilt"])
        try:
            import urllib.request
            why = json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/head/follow", timeout=1.0)).get("why") or ""
            if "not set to the tablet" not in why:
                return "the dashboard's own head tracking is on"
        except (OSError, ValueError):
            return "the dashboard does not answer /api/head/follow"
        arms = self.arms_ok()
        if arms:
            return arms
        return None

    def run(self):
        period, last_check, block = 0.1, 0.0, "not checked"
        while True:
            time.sleep(period)
            now = time.monotonic()
            with self.lock:
                if not self.on:
                    self.mode, self.settled, self.acquired = "off", False, False
                    continue
            if now - last_check >= 1.0 or block:
                block, last_check = self.blocked(), now
            if block:
                with self.lock:
                    self.mode, self.why, self.settled, self.acquired = "blocked", block, False, False
                continue
            mid = self.shoulders.get_mid(0.5)
            a = self.a
            if mid is not None and now - self.moved_at > a.head_wait_s:   # a frame taken after the last move
                self.seen_at = now
                err = float(mid["below_deg"])          # + : the shoulders are below the axis -> tilt down
                # hysteresis: a still head starts at --head-start-deg off, a moving one goes on to --head-stop-deg
                self.moving = abs(err) > (a.head_stop_deg if self.moving else a.head_start_deg)
                if self.moving:
                    step = max(-a.head_dps * period, min(a.head_dps * period, a.head_gain * err))
                    target = max(a.head_tilt_min, min(a.head_tilt_max, self.tilt - step))
                    self.count = 0
                    if abs(target - self.tilt) >= 0.2:
                        self.gaze(target)
                        self.moved_at = now
                    with self.lock:
                        self.mode, self.settled = "centre", False
                        self.why = f"shoulders {err:+.1f} deg from the axis, tilt {self.tilt:.1f}"
                else:
                    self.count += 1
                    with self.lock:
                        if self.count >= a.head_settle:
                            # ACQUIRED: settled on this person once; small re-centring moves after it keep it
                            self.mode, self.settled, self.acquired = "settled", True, True
                        self.why = f"shoulders {err:+.1f} deg from the axis, tilt {self.tilt:.1f}"
            elif mid is None and now - self.seen_at > a.head_search_s:
                # SEARCH: nobody: sweep slowly between the limits
                self.count = 0
                target = self.tilt + self.sweep_dir * a.head_search_dps * period
                if not a.head_tilt_min <= target <= a.head_tilt_max:
                    self.sweep_dir = -self.sweep_dir
                    target = max(a.head_tilt_min, min(a.head_tilt_max, target))
                self.gaze(target)
                with self.lock:
                    self.mode, self.settled, self.acquired = "search", False, False
                    self.why = f"nobody seen: sweeping, tilt {self.tilt:.1f}"

    def gaze(self, tilt):
        r = self.ask(f"GAZE {self.pan:.1f} {tilt:.1f} {self.a.head_lease_ms}")
        if r and r.startswith("OK gaze"):
            try:
                self.tilt = float(r.split()[3])        # the clamped angle the neck will run
            except (IndexError, ValueError):
                self.tilt = tilt
        else:
            self.tilt = tilt

    def status(self):
        with self.lock:
            return {"mode": self.mode, "settled": self.settled, "acquired": self.acquired, "why": self.why,
                    "tilt": None if self.tilt is None else round(self.tilt, 1)}

    def get(self, side, max_age):
        with self.lock:
            return self.value.get(side) if time.monotonic() - self.recv <= max_age else None


class Palm:
    """The newest palm: (xyz in its frame, image stamp in s, monotonic receipt)."""

    def __init__(self):
        self.lock, self.value = threading.Lock(), None

    def put(self, xyz, stamp_s):
        with self.lock:
            self.value = (np.asarray(xyz, float), stamp_s, time.monotonic())

    def get(self):
        with self.lock:
            return self.value


def fmt(v):
    return g2s.fmt(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=("run", "frames"))
    ap.add_argument("--hand", default="right", choices=("left", "right"),
                    help="which of the person's hands (hand_tracker/palm.py)")
    ap.add_argument("--mode", default="body", choices=("body", "absolute", "relative"))
    ap.add_argument("--body-origin", default="0.20,-0.19,0.75",
                    help="body mode: x,y,z in arm_mount where the arm's hand goes when the palm is at the person's "
                         "shoulder. 0.20 forward of the shoulder line: from 0.0 a hand held out 0.2-0.3 m put the arm at "
                         "x 0.2-0.3, the elbow folded, and the service's guard stopped it (VIBRATION in joint 4)")
    ap.add_argument("--mapping", default="body", choices=tuple(MAPPINGS), help="relative mode: whose frame the motion is in")
    ap.add_argument("--gain", type=float, default=1.0, help="body and relative modes: metres of arm per metre of hand")
    ap.add_argument("--palm-topic", default=None, help="default /perception/hand/<hand>/palm")
    ap.add_argument("--transform", default="joints", choices=("joints", "tf", "calib"))
    ap.add_argument("--calib", default=os.path.join(HERE, "..", "hand_tracker", "cam_to_arm_nominal.json"),
                    help="the cam_to_arm JSON for --transform calib")
    ap.add_argument("--tf-topics", default="", help="extra TF topics, comma-separated (a private robot_state_publisher's)")
    ap.add_argument("--head-tilt-from", choices=("imu", "neck"), default="imu")
    ap.add_argument("--max-tilt-disagree-deg", type=float, default=3.0)
    ap.add_argument("--standoff", type=float, default=0.0, help="absolute mode: m the hand stops short of the palm, toward the shoulder")
    ap.add_argument("--min-reach", type=float, default=0.25, help="m from the shoulder, at least")
    ap.add_argument("--max-reach", type=float, default=0.60, help="m from the shoulder, at most: a palm farther is reached toward")
    ap.add_argument("--box", default="0.05,0.85,-0.85,0.50,0.15,1.40",
                    help="x0,x1,y0,y1,z0,z1: the goal is kept inside this box in arm_mount, metres. A safety box, larger "
                         "than the reach: the IK solves what is out of reach to the nearest reachable point")
    ap.add_argument("--smooth-s", type=float, default=0.08, help="time constant of the target's low-pass")
    ap.add_argument("--max-speed", type=float, default=0.50, help="m/s the target may move at (0.5: the arm service's own pose limit)")
    ap.add_argument("--max-accel", type=float, default=2.0, help="m/s^2 the target may accelerate at")
    ap.add_argument("--max-turn", type=float, default=1.0, help="rad/s the target's orientation may turn at")
    ap.add_argument("--no-auto-resume", dest="auto_resume", action="store_false",
                    help="do not restart the arm service's follow when it stops (it does by default: simulation only)")
    ap.add_argument("--stale-s", type=float, default=0.60, help="a palm older than this is not followed")
    ap.add_argument("--no-offer", dest="offer", action="store_false", help="follow any palm: no offered check, no neutral")
    ap.add_argument("--close-m", type=float, default=0.0,
                    help="m: a palm this near the arm (its shoulder) is followed whether the person's shoulder is seen or "
                         "not (V1: 0.5; 0, the default, turns it off: V2's head keeps the shoulders in view)")
    ap.add_argument("--offer-reach", type=float, default=0.20, help="m a held-out palm is nearer the robot than its shoulder")
    ap.add_argument("--offer-below", type=float, default=0.55, help="m a held-out palm may be below its shoulder")
    ap.add_argument("--offer-above", type=float, default=0.35, help="m a held-out palm may be above its shoulder")
    ap.add_argument("--offer-s", type=float, default=0.3, help="s a palm is offered before the arm follows it")
    ap.add_argument("--away-s", type=float, default=0.7, help="s a palm is not offered before the arm goes to neutral")
    ap.add_argument("--neutral-q", default="0,0.15,0,0.9,0,0,0",
                    help="the neutral posture, the arm's 7 joints in rad: down, the elbow bent (the model's `rest` is "
                         "straight down, near singular)")
    ap.add_argument("--state-topic", default="/perception/hand/state", help="the tracker's state, for the shoulders")
    # V2: the head keeps the nearest person's shoulders centred (HeadTilt); the arm follows only once it has settled
    ap.add_argument("--no-head", dest="head", action="store_false", help="leave the head alone (V1)")
    ap.add_argument("--neck-host", default=None, help="default OPENDUBS_HEAD_NECK_HOST (environment or /etc/opendubs/robot.env)")
    ap.add_argument("--head-tilt-min", type=float, default=71.0, help="deg, the neck's own: 71 is this robot's fully down")
    ap.add_argument("--head-tilt-max", type=float, default=108.0, help="deg: 90 + the neck's 18 deg envelope for tracking")
    ap.add_argument("--head-start-deg", type=float, default=5.0, help="a still head moves when the shoulders are this far off")
    ap.add_argument("--head-stop-deg", type=float, default=2.0, help="a moving head stops when they are within this")
    ap.add_argument("--head-gain", type=float, default=0.35,
                    help="of the offset, per step (0.5 hunted: the servos move ~1.67 deg per commanded one, neck_follow.py)")
    ap.add_argument("--head-dps", type=float, default=25.0, help="deg/s at most (the dashboard's quiet neck: 25)")
    ap.add_argument("--head-wait-s", type=float, default=0.6,
                    help="s after a move before a frame counts (the shoulders reach the follower up to ~0.25 s late: the "
                         "pose model runs every 3rd frame; 0.25 hunted +-10 deg for 11 s before settling)")
    ap.add_argument("--head-settle", type=int, default=5, help="centred frames before the head is settled")
    ap.add_argument("--head-search-s", type=float, default=1.5, help="s with nobody seen before the head sweeps")
    ap.add_argument("--head-search-dps", type=float, default=8.0, help="deg/s of the sweep")
    ap.add_argument("--head-lease-ms", type=int, default=600)
    ap.add_argument("--arm-j2-max", type=float, default=142.0,
                    help="deg: the head does not move while either real arm's J2 is above this (arm_clearance.py)")
    ap.add_argument("--rate", type=float, default=50.0)
    ap.add_argument("--start", action="store_true", help="following from the start, as if /perception/hand_follow/start was called")
    ap.add_argument("--dummy", action="store_true", help="no tracker: a palm on a circle in front of the camera")
    ap.add_argument("--dummy-z", type=float, default=0.55)
    ap.add_argument("--dummy-r", type=float, default=0.10)
    ap.add_argument("--dummy-period", type=float, default=8.0)
    ap.add_argument("--dummy-hz", type=float, default=0.0, help="the dummy palm as steps at this rate, as the tracker's (0: every tick)")
    ap.add_argument("--dummy-noise", type=float, default=0.0, help="m: the dummy palm's noise (standard deviation)")
    ap.add_argument("--save", default=None, help="frames: write the transform as cam_to_arm JSON")
    ap.add_argument("--palm", default=None, help="frames: x,y,z of a palm in the camera frame, to see where it lands")
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=78)
    ap.add_argument("--reference", default="right")
    ap.add_argument("--model", default=g2s.REPO_MODEL if os.path.exists(g2s.REPO_MODEL) else "openarm_assets:bimanual")
    ap.add_argument("--arm-socket", default=os.environ.get("HAND_ARM_SOCKET"),
                    help="the arm service's command socket, told to `hold` when this exits (hand_follow.sh sets it)")
    args = ap.parse_args()
    if args.sim_domain == args.robot_domain:
        raise SystemExit("--sim-domain must differ from the robot's domain: the simulation's joint_states would reach the robot")
    b = [float(v) for v in args.box.split(",")]
    box = ((b[0], b[1]), (b[2], b[3]), (b[4], b[5]))

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from geometry_msgs.msg import PointStamped, PoseStamped
    from rclpy.context import Context
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data

    robot_ctx = Context()
    rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
    node = Node("hand_follow", context=robot_ctx)
    frames = Frames(rclpy, node, args.transform, args.calib, [t for t in args.tf_topics.split(",") if t],
                    args.head_tilt_from, args.max_tilt_disagree_deg)
    g2s.spin_in_thread(rclpy, node, robot_ctx)

    if args.command == "frames":
        deadline, t, info = time.monotonic() + 10.0, None, None
        while time.monotonic() < deadline:
            try:
                t, info = frames.camera_in_arm()
                break
            except LookupError as error:
                why = str(error)
                time.sleep(0.2)
        if t is None:
            raise SystemExit(f"no transform {ARM_FRAME} <- {CAMERA_FRAME}: {why}")
        print(json.dumps(info, indent=1))
        print(f"T_{ARM_FRAME}_{CAMERA_FRAME} (camera coordinates -> {ARM_FRAME}; x forward, y left, z up):")
        print(np.array2string(t, precision=4, suppress_small=True))
        print(f"camera at {fmt(t[:3, 3])} m; optical axis {fmt(t[:3, 2])} in {ARM_FRAME}")
        if args.palm:
            p = np.array([float(v) for v in args.palm.split(",")])
            pa = t[:3, :3] @ p + t[:3, 3]
            print(f"palm {fmt(p)} (camera) -> {fmt(pa)} ({ARM_FRAME}) -> target "
                  f"{fmt(shape_target(pa, args.standoff, args.min_reach, args.max_reach, box))}")
        if args.save:
            with open(args.save, "w") as f:
                json.dump({"from": CAMERA_FRAME, "to": f"{ARM_FRAME} (the arm model frame: +x forward, +y left, +z up)",
                           "side": args.reference, "R": t[:3, :3].round(6).tolist(), "t": t[:3, 3].round(5).tolist(),
                           "source": f"hand_follow.py frames, --transform {args.transform}: {info['source']}",
                           "head_deg": info.get("head_deg"), "pitch_imu_deg": info.get("pitch_imu_deg"),
                           "valid_for": f"the head where it was: {info.get('head_deg')}, written {time.strftime('%Y-%m-%d %H:%M')}"},
                          f, indent=1)
            print(f"saved {args.save}")
        g2s.shutdown_all(rclpy)
        return 0

    # -- run: the services, then the follow loop ----------------------------------------------------------------------
    from robot_api_interfaces.msg import ResultCode
    from robot_api_interfaces.srv import RobotCommand, RobotQuery

    from std_msgs.msg import String

    palm, shoulders = Palm(), Shoulders()

    # the REAL arms' shoulders (J2), for the head: arm_clearance.py's rule. Unknown means the head does not move.
    from sensor_msgs.msg import JointState
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

    head = None
    if args.head:
        host = args.neck_host or neck_host_from_env()
        if not host:
            raise SystemExit("no neck host: set OPENDUBS_HEAD_NECK_HOST or --neck-host, or run with --no-head")
        head = HeadTilt(host, shoulders, args, arms_clear)
        threading.Thread(target=head.run, daemon=True).start()
    pub_palm = node.create_publisher(PointStamped, "/hand_follow/palm", 10)      # for watching: the palm in arm_mount
    pub_target = node.create_publisher(PoseStamped, "/hand_follow/target", 10)   # and the target sent

    hand_model = g2s.HandModel(args.model)
    ik = PositionIk(args.model, args.reference)
    q_neutral = np.array([float(v) for v in args.neutral_q.split(",")])
    neutral_pos, _ = ik.fk(q_neutral)
    sim_ctx = Context()
    rclpy.init(context=sim_ctx, domain_id=args.sim_domain)
    sim = g2s.SimArm(rclpy, sim_ctx, args.reference, hand_model)
    g2s.spin_in_thread(rclpy, sim.node, sim_ctx)
    if not sim.wait_for_state():
        raise SystemExit(f"no joint_states on domain {args.sim_domain}: start the simulation first (hand_follow.sh sim)")

    follow = {"on": False, "hand": args.hand, "sub": None, "state_sub": None, "state": "stopped", "info": None,
              "error": None, "mode": "stopped", "offer": None}
    lock = threading.Lock()

    # The arm service, watched: its status once a second over its command socket (arm_client.py). A follow its guard
    # stopped (VIBRATION, a torque limit, the permit) leaves the arm held where it is; without this the follower kept
    # sending targets nobody followed, which looked like an arm stuck in between.
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

    def palm_topic(hand):
        return args.palm_topic or f"/perception/hand/{hand}/palm"

    def subscribe(hand):
        """The palm topic of `hand`, or none: the tracker runs MediaPipe only while somebody subscribes, so stopping here
        also stops the tracking."""
        for key in ("sub", "state_sub"):
            if follow[key] is not None:
                node.destroy_subscription(follow[key])
                follow[key] = None
        palm.value = None
        follow["mode"] = "waiting" if hand is not None else "stopped"
        if head is not None:
            (head.start if hand is not None else head.stop)()
        if hand is not None and not args.dummy:
            follow["sub"] = node.create_subscription(PointStamped, palm_topic(hand), lambda m: palm.put(
                (m.point.x, m.point.y, m.point.z), m.header.stamp.sec + m.header.stamp.nanosec * 1e-9), qos_profile_sensor_data)
            if args.offer:              # the shoulders, for the offered check
                def on_state(msg):
                    try:
                        shoulders.put(json.loads(msg.data))
                    except (ValueError, TypeError):
                        pass
                follow["state_sub"] = node.create_subscription(String, args.state_topic, on_state, 10)

    def reply(response, code, message, data):
        response.result_code.result_code, response.result_code.message = code, message
        response.data = json.dumps(data)
        print(message, flush=True)
        return response

    def on_start(request, response):
        """Checks what following needs (the simulated arm, the tracker, the transform), turns following on, and returns:
        it does not wait for a palm. Called while following it succeeds again (and switches the hand if one is given)."""
        try:
            req = json.loads(request.req) if request.req.strip() else {}
        except json.JSONDecodeError as error:
            return reply(response, ResultCode.INVALID_INPUT, f"the request is not JSON: {error}", {})
        if not isinstance(req, dict) or set(req) - {"hand"} or req.get("hand", "right") not in ("left", "right"):
            return reply(response, ResultCode.INVALID_INPUT, 'start takes {} or {"hand": "left" | "right"}', {})
        hand = req.get("hand", follow["hand"])
        if sim.stamp is None or time.monotonic() - sim.stamp > 2.0:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING,
                         f"the simulated arm is not publishing joint_states on domain {args.sim_domain} (hand_follow.sh up)", {})
        if not args.dummy and node.count_publishers(palm_topic(hand)) == 0:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING,
                         f"nothing publishes {palm_topic(hand)}: the hand tracker is not running (hand_stack.sh up)", {})
        try:
            _, info = frames.camera_in_arm()
        except LookupError as error:
            return reply(response, ResultCode.SERVICE_NOT_RUNNING, f"no {ARM_FRAME} <- {CAMERA_FRAME} transform: {error}", {})
        if arm["following"] is not True:     # stopped by its guard or told to hold: start is the way to re-arm it
            reply_line = arm_command("follow")
            print(f"start: the arm service was not following ({arm['line']}): {reply_line}", flush=True)
            if not (reply_line.startswith("follow started") or "follow is running" in reply_line):
                return reply(response, ResultCode.SERVICE_NOT_RUNNING, f"the arm service did not start following: {reply_line}", {})
            arm["following"] = None
        with lock:
            was = follow["on"] and follow["hand"] == hand
            if not was:
                subscribe(hand)
                follow.update(on=True, hand=hand)
        return reply(response, ResultCode.SUCCESS, f"{'already following' if was else 'following'} the {hand} palm",
                     {"following": True, "hand": hand, "palm_topic": "dummy" if args.dummy else palm_topic(hand),
                      "target": f"{args.reference}/target on ROS domain {args.sim_domain}", "transform": info})

    def on_stop(_request, response):
        """Following off and the palm unsubscribed (the tracker idles); the loop keeps sending the last pose, so the arm
        holds there. Called when stopped it succeeds again."""
        with lock:
            was = follow["on"]
            follow["on"] = False
            subscribe(None)
        return reply(response, ResultCode.SUCCESS, "stopped: the arm holds where it is" if was else "was not following",
                     {"following": False})

    def on_status(_request, response):
        hand_p, _ = sim.hand()
        got = palm.get()
        alive = sim.stamp is not None and time.monotonic() - sim.stamp < 2.0
        with lock:
            data = {"following": follow["on"], "hand": follow["hand"], "state": follow["state"], "mode": follow["mode"],
                    "offer": follow["offer"], "neutral": [round(float(v), 3) for v in neutral_pos],
                    "head": None if head is None else head.status(),
                    "palm_topic": "dummy" if args.dummy else palm_topic(follow["hand"]),
                    "palm_age_s": None if got is None else round(time.monotonic() - got[2], 2),
                    "hand_position": [round(float(v), 4) for v in hand_p], "transform": follow["info"],
                    "last_error": follow["error"], "sim_domain": args.sim_domain}
        response.result_code.result_code = ResultCode.SUCCESS if alive else ResultCode.SERVICE_NOT_RUNNING
        response.result_code.message = (follow["state"] if alive else
                                        f"no joint_states from the simulated arm on domain {args.sim_domain}")
        response.data = json.dumps(data)
        return response

    node.create_service(RobotCommand, "/perception/hand_follow/start", on_start)
    node.create_service(RobotCommand, "/perception/hand_follow/stop", on_stop)
    node.create_service(RobotQuery, "/perception/hand_follow/status", on_status)
    if args.start:
        subscribe(args.hand)
        follow["on"] = True
    print(f"hand_follow: {'dummy circle' if args.dummy else palm_topic(args.hand)} -> {ARM_FRAME} ({args.transform}, tilt "
          f"from {args.head_tilt_from}) -> {args.mode}"
          f"{f' ({args.mapping} frame, gain {args.gain})' if args.mode == 'relative' else ''}, IK -> "
          f"{args.reference}/target on domain {args.sim_domain}; "
          f"{'offered: held out from the shoulder' + (f' or within {args.close_m:.2f} m of the arm' if args.close_m > 0 else '') + ', else neutral; ' if args.offer else ''}"
          f"{f'head: tilts to centre the shoulders ({head.host}, {args.head_tilt_min:.0f}-{args.head_tilt_max:.0f} deg), the arm waits for it; ' if head else ''}"
          f"services "
          f"/perception/hand_follow/{{start,stop,status}}; {'FOLLOWING' if follow['on'] else 'stopped until start'}", flush=True)

    dt = 1.0 / args.rate
    active, cmd, rot, sent, info = False, None, None, None, None
    pf = vf = None                               # the target's second-order filter: position and velocity
    anchor_palm = anchor_cmd = goal = None
    t0, last_print, last_err, last_err_at, next_dummy = time.monotonic(), 0.0, "", 0.0, 0.0
    cached = (None, None, None, None, True, "")  # (palm receipt, T, info, error, offered, why): once per palm
    last_sh = (None, 0.0)                        # body mode: the person's shoulder in arm_mount, and when seen
    body_origin = np.array([float(v) for v in args.body_origin.split(",")])
    resume_at, backoff, following_since, state = 0.0, 2.0, None, "stopped"
    offered_since = away_since = None
    posture_follow = None                        # the IK's posture while following: the arm's when it first moved
    w = 1.0 / args.smooth_s
    try:
        while True:
            now = time.monotonic()
            if args.dummy and now >= next_dummy:
                # a palm on a circle; at --dummy-hz it comes as steps with --dummy-noise, as the tracker's do
                a = 2 * math.pi * (now - t0) / args.dummy_period
                noise = np.random.normal(0.0, args.dummy_noise, 3) if args.dummy_noise else np.zeros(3)
                palm.put(np.array((args.dummy_r * math.cos(a), args.dummy_r * math.sin(a), args.dummy_z)) + noise, None)
                next_dummy = now + (1.0 / args.dummy_hz if args.dummy_hz else 0.0)
            got = palm.get()
            err = None
            t = None
            arm_on = arm["following"]
            if follow["on"] and arm_on is False:
                # The arm service is not following right/target (its guard stopped it, or it was told to hold): say so,
                # and in the simulation start its follow again, backing off if it keeps stopping.
                state = f"arm service not following ({arm['line']})"
                active, following_since = False, None
                if args.auto_resume and now >= resume_at:
                    print(f"resuming the arm service's follow after: {arm['line']}", flush=True)
                    print(f"  arm service: {arm_command('follow')}", flush=True)
                    resume_at, backoff = now + backoff, min(backoff * 2, 30.0)
                    cmd = sent = None            # its controller starts where the arm is now: so does this
                    arm["following"] = None      # until the next status says
            elif not follow["on"]:
                active, state = False, "stopped: holding the last pose"
            elif got is None or now - got[2] > args.stale_s:
                state = "no palm: holding the last pose"
            else:
                if cached[0] != got[2]:          # a new palm: its transform (TF and the IMU) and is it offered, once
                    try:
                        T, inf = frames.camera_in_arm(got[1])
                        ok, why = True, "any palm (--no-offer)"
                        pa = T[:3, :3] @ got[0] + T[:3, 3]
                        sc = shoulders.get(follow["hand"], max_age=0.5)
                        sa = None if sc is None else T[:3, :3] @ sc + T[:3, 3]
                        if sa is not None and np.linalg.norm(sa - pa) <= SHOULDER_REACH:
                            last_sh = (sa, time.monotonic())     # the palm's own person's shoulder, for body mode
                        if args.offer:
                            ok, why = offered(pa, sa, args.close_m, args.offer_reach, args.offer_below, args.offer_above)
                        cached = (got[2], T, inf, None, ok, why)
                    except LookupError as error:
                        cached = (got[2], None, None, str(error), False, "no transform")
                t, err = cached[1], cached[3]
                info = cached[2] if cached[2] is not None else info
                if t is None:
                    state = "no transform: holding the last pose"
            # OFFERED (V2.1): a palm offered for --offer-s -> following. Not offered for --away-s -> neutral (the arm
            # down), and "not offered" includes the hand gone while the person is still seen: a dropped hand is mostly
            # lost by the hand landmarker, and V2 held the arm up until it happened to see the lowered palm. Nobody
            # seen at all -> hold where it is. While following, the arm keeps following the palm through frames that
            # are not offered until --away-s has passed: a flicker does not freeze it.
            motion = "hold"
            hs = head.status() if head is not None and follow["on"] else None
            live = follow["on"] and arm_on is not False
            if live and hs is not None and not hs["acquired"]:
                # the arm waits until the head has settled on the person once (then small re-centring moves do not
                # stop it); searching or blocked, it holds and no clock runs
                offered_since = away_since = None
                state = f"head {hs['mode']}: holding ({hs['why']})"
            elif live and (t is not None or shoulders.get_mid(0.5) is not None):
                if t is not None:
                    ok, why = cached[4], cached[5]
                else:
                    ok, why = False, "the person is seen, their hand is not (withdrawn)"
                offered_since = (offered_since or now) if ok else None
                away_since = None if ok else (away_since or now)
                mode = follow["mode"] if args.offer else "following"
                if ok and now - offered_since >= args.offer_s and mode != "following":
                    mode = "following"
                    print(f"following: offered ({why})", flush=True)
                elif not ok and now - away_since >= args.away_s and mode != "neutral":
                    mode = "neutral"
                    print(f"neutral: not offered ({why})", flush=True)
                follow["mode"], follow["offer"] = mode, why
                if mode == "following" and t is not None:
                    motion, state = "follow", "following" if ok else f"following (not offered for now: {why})"
                elif mode == "neutral":
                    motion, state = "neutral", f"neutral ({why})"
                else:
                    state = f"{mode}: holding ({why})"
            elif live:
                offered_since = away_since = None   # nobody seen: hold, and no clock runs
            if arm_on and follow["on"] and state == "following":
                following_since = following_since or now
                if now - following_since > 30.0:
                    backoff = 2.0
            if motion == "hold":
                active = False
            if motion == "hold" and sent is not None:
                # HOLD: the last pose sent, sent again. Stopped, or the hand lost (occluded, out of view, a rough palm),
                # the arm is still given a target every tick, so nothing depends on how the arm's controller treats a
                # silent reference (the simulation's holds, another's may not). The held pose is the last COMMANDED one,
                # latched, never the measured hand: re-reading a sagging arm would walk its own target down.
                sim.send(*sent)
            if motion != "hold":
                p_arm = t[:3, :3] @ got[0] + t[:3, 3] if motion == "follow" else None
                if not active:
                    if cmd is None:              # the first palm: from where the arm is, its posture the IK's start
                        with sim.lock:
                            ik.reset_qpos(sim.model.data.qpos)
                        posture_follow = ik.posture.copy()
                        cmd = sim.hand()[0].copy()
                    # after a hold too: from the held pose, smoothed, and in relative mode the hand's motion counts from
                    # here, so a palm that comes back somewhere else does not jump the arm
                    anchor_palm, anchor_cmd = (None if p_arm is None else p_arm.copy()), cmd.copy()
                    pf, vf, active = cmd.copy(), np.zeros(3), True
                if motion == "neutral":          # NEUTRAL: the arm down, by the same IK, filter and limits
                    ik.posture, goal = q_neutral, neutral_pos.copy()
                elif args.mode == "body" and last_sh[0] is not None and now - last_sh[1] <= 1.0:
                    # BODY: the palm's place against the person's own shoulder, put against the arm's shoulder line:
                    # toward the robot is its forward, sideways and up the same way in the room. Reaching toward a
                    # palm 1.1-1.5 m off (absolute) moved the arm 0.18 m for 0.57 m of the hand sideways; this is 1:1
                    # wherever the person stands. Kept within the reach of the arm's shoulder as absolute is.
                    ik.posture = posture_follow
                    d = p_arm - last_sh[0]
                    goal = body_origin + args.gain * np.array([-d[0], d[1], d[2]])
                    r = goal - SHOULDER
                    n = float(np.linalg.norm(r))
                    lo = max(args.min_reach, 0.30)       # and the elbow never folded tight
                    if n > 1e-6 and not lo <= n <= args.max_reach:
                        goal = SHOULDER + r * (min(max(n, lo), args.max_reach) / n)
                elif args.mode == "relative":
                    ik.posture = posture_follow
                    goal = anchor_cmd + args.gain * (MAPPINGS[args.mapping] @ (p_arm - anchor_palm))
                else:
                    ik.posture = posture_follow
                    goal = shape_target(p_arm, args.standoff, args.min_reach, args.max_reach, box)
                goal = np.array([min(max(goal[i], box[i][0]), box[i][1]) for i in range(3)])
                # SMOOTH: a critically damped second-order filter toward the goal, its acceleration and speed limited.
                # The tracker's palms are steps (12-15 a second, with noise); stepped targets jerk the arm's pose
                # controller, and the service's vibration guard stopped the follow on them.
                acc = w * w * (goal - pf) - 2.0 * w * vf
                na = float(np.linalg.norm(acc))
                if na > args.max_accel:
                    acc *= args.max_accel / na
                vf = vf + acc * dt
                nv = float(np.linalg.norm(vf))
                if nv > args.max_speed:
                    vf *= args.max_speed / nv
                pf = pf + vf * dt
                pos, rot_ik, _ = ik.solve(pf)
                if np.linalg.norm(pos - pf) > 0.005:
                    pf, vf = pos.copy(), vf * 0.5    # at the reach: the filter stays on what the arm can reach (no windup)
                # the wrist turns at most --max-turn: the IK's orientation is free, and a small move near a singular
                # posture can swing it
                rot = rot_ik if rot is None else turn_toward(rot, rot_ik, args.max_turn * dt)
                cmd = pos
                sent = (pos, rot)
                sim.send(pos, rot)
                stamp = node.get_clock().now().to_msg()
                if p_arm is not None:
                    pm = PointStamped()
                    pm.header.stamp, pm.header.frame_id = stamp, ARM_FRAME
                    pm.point.x, pm.point.y, pm.point.z = (float(v) for v in p_arm)
                    pub_palm.publish(pm)
                tm = PoseStamped()
                tm.header.stamp, tm.header.frame_id = stamp, ARM_FRAME
                tm.pose.position.x, tm.pose.position.y, tm.pose.position.z = (float(v) for v in cmd)
                q = g2s.mat_to_quat(rot)
                tm.pose.orientation.x, tm.pose.orientation.y, tm.pose.orientation.z, tm.pose.orientation.w = (float(v) for v in q)
                pub_target.publish(tm)
            with lock:
                follow["state"], follow["error"] = state, err or follow["error"]
                if active:
                    follow["info"] = info
            if err and (err != last_err or now - last_err_at > 5.0):
                print(f"not following: {err}", file=sys.stderr, flush=True)
                last_err, last_err_at = err, now
            if now - last_print >= 1.0:
                last_print = now
                hand_p, _ = sim.hand()
                line = f"[{state}] hand {fmt(hand_p)}"
                if active and motion == "follow":
                    line += (f"  palm cam {fmt(got[0])} -> {ARM_FRAME} {fmt(p_arm)}  target {fmt(cmd)} "
                             f"(goal {fmt(goal)}, hand {np.linalg.norm(hand_p - cmd) * 1000:.0f} mm off)  "
                             f"pitch imu {info['pitch_imu_deg']:.1f} reported {info['pitch_reported_deg']:.1f}")
                elif active:
                    line += f"  target {fmt(cmd)} (goal {fmt(goal)}, hand {np.linalg.norm(hand_p - cmd) * 1000:.0f} mm off)"
                print(line, flush=True)
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        g2s.shutdown_all(rclpy)
        hold_arm(args.arm_socket, args.reference)
    return 0


def hold_arm(socket_path, side):
    """THE END: this process stops sending targets, so the arm service is told to hold (its `hold`: the follow ends and
    the arm stays where it is, by its own controller), rather than left to treat a silent reference however it does.
    `hand_follow.sh up` starts the service's follow again."""
    import subprocess
    service = os.path.join(HERE, "..", "..", "openarm", "gafro", "arm_service.py")
    if not socket_path or not os.path.exists(socket_path):
        print(f"exit: no arm service socket at {socket_path!r}: nothing told to hold", file=sys.stderr, flush=True)
        return
    try:
        out = subprocess.run([sys.executable, service, "ctl", "--side", side, "--socket", socket_path, "hold"],
                             capture_output=True, text=True, timeout=10)
        print(f"exit: arm service: {(out.stdout or out.stderr).strip()}", flush=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"exit: could not tell the arm service to hold: {error}", file=sys.stderr, flush=True)


def _sigterm(*_):
    raise KeyboardInterrupt      # `hand_follow.sh down` sends TERM: the same way out as Ctrl-C, so the arm is held


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
