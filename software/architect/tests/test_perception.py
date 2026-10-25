#!/usr/bin/env python3
"""Perception's answers, in the camera's frame, land where they are in the chassis frame.

    python3 software/architect/tests/test_perception.py

Needs numpy; the twin tests also need mujoco and PyYAML, and skip without them.
"""
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np  # noqa: E402

from opendubs_architect import camera_frames as cf  # noqa: E402
from opendubs_architect.perception import Perception  # noqa: E402

try:
    import mujoco  # noqa: F401
    import yaml  # noqa: F401
    HAVE_TWIN = True
except ImportError:
    HAVE_TWIN = False


class Conversion(unittest.TestCase):
    def setUp(self):
        self.p = Perception()                       # the head centred, the lift down

    def test_straight_ahead_of_the_camera_is_ahead_of_the_robot(self):
        cam = self.p.T[:3, 3]
        here = self.p.locate_reply({"position": {"x": 0.0, "y": 0.0, "z": 1.0}})["position"]
        ahead = np.array([here[k] for k in "xyz"]) - cam
        self.assertGreater(ahead[0], 0.98)          # forward, the camera tilted 6 degrees down
        self.assertAlmostEqual(math.degrees(math.atan2(-ahead[2], ahead[0])), 6.0, delta=0.1)
        right = self.p.locate_reply({"position": {"x": 1.0, "y": 0.0, "z": 0.0}})["position"]
        self.assertLess(right["y"] - cam[1], -0.99)  # the image's right is the robot's right (-y)

    def test_a_grasp_approaching_along_the_view_points_the_tool_forward(self):
        # GraspNet's x (the approach) along the camera's z: the tool's +z (out through the fingers)
        # must point where the camera looks.
        R = np.column_stack([(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])   # x_g, y_g, z_g = x_g x y_g
        q = cf.matrix_quat(R)
        g = self.p.grasp_reply({"position": {"x": 0.0, "y": 0.1, "z": 0.8}, "orientation": q,
                                "grasp_score": 0.04, "width": 0.03}, offsets=(0.0, 0.0, 0.02))["best_grasp"]
        tool_z = cf.quat_matrix(g["quaternion_wrt_base"])[:, 2]
        self.assertGreater(float(tool_z @ self.p.T[:3, 2]), 0.999)
        plain = self.p.grasp_reply({"position": {"x": 0.0, "y": 0.1, "z": 0.8}, "orientation": q})
        self.assertAlmostEqual(g["translation_wrt_base"][2] - plain["best_grasp"]["translation_wrt_base"][2], 0.02, 4)

    def test_requests_name_the_camera_and_the_world_up(self):
        req = self.p.grasp_request("candy bowl", "head")
        self.assertEqual((req["label"], req["camera"]), ("candy bowl", "femto"))
        up = np.array(req["up_cam"])
        self.assertAlmostEqual(float(np.linalg.norm(up)), 1.0, 4)
        self.assertLess(up[1], -0.99)               # up is the image's -y with the head level
        with self.assertRaises(RuntimeError):
            self.p.locate_request("bowl", "wrist_left")   # no wrist camera fitted


@unittest.skipUnless(HAVE_TWIN, "needs mujoco and PyYAML (the twin)")
class AgainstTheTwin(unittest.TestCase):
    def test_the_robot_description_and_the_twin_put_the_camera_in_the_same_place(self):
        from opendubs_architect import live_arms  # noqa: F401  (puts the twin on the path)
        import clearance
        import scene
        m = scene.build(None)
        d = mujoco.MjData(m)
        body = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "femto_mount")
        frames = cf.CameraFrames()
        for pan, tilt, lift in [(0, 0, 0.0), (30, -20, 0.0), (-25, 10, 0.1)]:
            clearance.set_pose(m, d, "", lift * 1000, pan, tilt)
            T = frames.link_pose("femto_mount_link", {"head_pan": math.radians(pan),
                                                      "head_tilt": math.radians(tilt), "zaxis": lift})
            self.assertLess(np.linalg.norm(T[:3, 3] - d.xpos[body]), 1e-4)
            self.assertLess(np.abs(T[:3, :3] - d.xmat[body].reshape(3, 3)).max(), 1e-4)

    def test_a_show_recorded_with_perception_goes_where_the_camera_saw(self):
        import functools
        import tempfile
        from opendubs_architect import api, show_backend   # puts the twin on the path
        import cue
        from opendubs_architect.demo_runner import Program
        limits = os.path.join(HERE, "..", "..", "choreo", "tests", "joint_limits_fixture.yaml")
        p = Perception()
        target = [0.70, -0.30, 0.95]                # inside the proposed right-arm box
        cam = np.linalg.inv(p.T) @ np.array(target + [1.0])

        class Seen:                                  # the perception services, answering in the camera frame
            def locate_object(self, label, camera="head"):
                return p.locate_reply({"position": dict(zip("xyz", cam[:3])), "score": 0.9})

        code = ('# Summary: reach to the bowl and back.\nreset_robot()\n'
                'b = locate_object("candy bowl")["position"]\n'
                'move_ee_to_pose("right", {"position": b, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}})\n'
                'reset_robot()\n')
        with tempfile.TemporaryDirectory() as d:
            arms = os.path.join(d, "arms.yaml")
            with open(arms, "w") as fh:
                fh.write("workspace:\n  right: {x: [0.6, 0.8], y: [-0.45, -0.15], z: [0.75, 1.1]}\n")
            os.environ["OPENDUBS_ARCHITECT_ARMS"] = arms
            try:
                base = functools.partial(show_backend.PerceptionDryRun, client=Seen())
                out = show_backend.Publisher(os.path.join(d, "shows"), api.PROFILES["arms_see"], limits,
                                             base=base)(Program(code, "bowl"))
            finally:
                del os.environ["OPENDUBS_ARCHITECT_ARMS"]
            show = cue.load_show(out["path"])
            reach = [c for c in show.doc["cues"] if c["slot"] == "ARM_R" and "joints" in c][0]
            from opendubs_architect import live_arms
            lim = cue.load_limits(limits)
            planner = live_arms.Planner(lim, live_arms.ArmConfig())
            rest = {s: live_arms.to_signed(s, [cue.rest_pose(lim, s)[j] for j in range(1, 8)]) for s in live_arms.SIDES}
            pose = {live_arms.ARM_SLOT[s]: rest[s] for s in live_arms.SIDES}
            pose["ARM_R"] = live_arms.to_signed("right", [reach["joints"][j] for j in range(1, 8)])
            here, _ = planner.tool("right", pose)
            self.assertLess(max(abs(a - b) for a, b in zip(here, target)), 0.012)


if __name__ == "__main__":
    unittest.main(verbosity=2)
