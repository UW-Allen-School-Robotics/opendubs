#!/usr/bin/env python3
"""The live arm service's gates: what must stop a move before it is sent, or while it runs.

    python3 software/architect/tests/test_live_arms.py

Needs numpy and PyYAML; the twin tests also need mujoco, and skip without it.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from opendubs_architect import live_arms as la  # noqa: E402
import cue  # noqa: E402  (on the path once live_arms is imported)
import runner  # noqa: E402

LIMITS_PATH = os.path.join(la._REPO, "software", "choreo", "tests", "joint_limits_fixture.yaml")
ZERO = {"ARM_L": [0.0] * 7, "ARM_R": [0.0] * 7}
try:
    import mujoco  # noqa: F401
    HAVE_MUJOCO = True
except ImportError:
    HAVE_MUJOCO = False


class StubPlanner:
    """Plans every request as a 1 s move to a fixed pose: the gates, not the twin, are under test."""

    def plan(self, side, pose, point=None, goal_signed=None, margin_deg=0.0):
        return la.Move(side, list(pose[la.ARM_SLOT[side]]), [0.0, 10.0, 0.0, 30.0, 0.0, 0.0, 0.0], 1.0, 50.0)

    def tool(self, side, pose):
        return [0.4, 0.0, 0.9], {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}


class FlipState:
    """The robot state, with the deadman released from the `after`-th read on."""

    def __init__(self, after):
        self.reads, self.after = 0, after

    def read(self):
        self.reads += 1
        return runner.FakeRobotState(deadman=self.reads < self.after).read()


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def live(state, arms=None, planner=None, config=None):
    clock = Clock()
    limits = cue.load_limits(LIMITS_PATH)
    config = config or la.ArmConfig(workspace={"right": {"x": [0.2, 0.6], "y": [-0.4, 0.0], "z": [0.6, 1.2]}})
    return la.LiveArms(arms or la.FollowingFakeArms(), state, planner or StubPlanner(), config, limits,
                       grip_cal={"left": (0.0, 0.04), "right": (0.0, 0.04)}, clock=clock, sleep=clock.sleep)


class Gates(unittest.TestCase):
    def test_deadman_released_refuses_before_anything_is_sent(self):
        a = live(runner.FakeRobotState(deadman=False))
        with self.assertRaises(la.Refused):
            a.move_to_named("right", "stow")
        with self.assertRaises(la.Refused):
            a.set_gripper("right", 0.0)
        self.assertEqual((a.arms.sent, a.arms.grips), ([], []))

    def test_deadman_released_mid_move_cancels_it(self):
        arms = la.FollowingFakeArms()
        arms.silent.add("ARM_R")                # the goal never reports: it is still running
        a = live(FlipState(after=5), arms=arms)
        with self.assertRaises(la.Refused) as e:
            a.move_to_named("right", "stow")
        self.assertEqual(e.exception.code, "DEADMAN")
        self.assertEqual((len(arms.sent), arms.cancelled), (1, 1))

    def test_unknown_state_refuses(self):
        a = live(runner.FakeRobotState(ch5=None))
        with self.assertRaises(la.Refused):
            a.move_to_named("right", "stow")
        self.assertEqual(a.arms.sent, [])

    def test_target_outside_the_workspace_is_never_planned(self):
        a = live(runner.FakeRobotState())
        with self.assertRaises(la.Refused) as e:
            a.move_to_point("right", [0.4, -0.2, 1.5])
        self.assertEqual(e.exception.code, "WORKSPACE")
        with self.assertRaises(la.Refused) as e:
            a.move_to_point("left", [0.4, 0.2, 0.9])  # no box for the left arm: named poses only
        self.assertEqual(e.exception.code, "NO_WORKSPACE")
        self.assertEqual(a.arms.sent, [])

    def test_the_node_answers_a_refusal_as_refused(self):
        from opendubs_architect import api, robot_node
        table = robot_node.handlers(live(runner.FakeRobotState()))
        pose = {"position": {"x": 0.4, "y": -0.2, "z": 1.5}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}}
        self.assertEqual(robot_node.answer(table["move_ee_to_pose"], {"arm": "right", "target_pose": pose})[0],
                         api.REFUSED)
        self.assertEqual(robot_node.answer(table["move_ee_to_pose"], {"arm": "middle", "target_pose": pose})[0],
                         api.INVALID_INPUT)
        self.assertEqual(robot_node.answer(table["move_arm_to_named_pose"], {"arm": "right", "name": "stow"})[0],
                         api.SUCCESS)

    def test_an_arm_that_stops_short_fails(self):
        class Short(la.FollowingFakeArms):
            def send(self, slot, points):
                gid = super().send(slot, points)
                self._measured[slot] = [v - 10.0 for v in self._measured[slot]]
                return gid
        a = live(runner.FakeRobotState(), arms=Short())
        with self.assertRaises(la.MoveFailed):
            a.move_to_named("right", "stow")


@unittest.skipUnless(HAVE_MUJOCO, "needs mujoco (the twin)")
class Twin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.limits = cue.load_limits(LIMITS_PATH)
        cls.config = la.ArmConfig(workspace={"right": {"x": [0.0, 0.7], "y": [-0.5, 0.1], "z": [0.3, 1.6]}},
                                  named={"offer": {"right": [20, 10, 0, 60, 0, 10, 0]}})
        cls.planner = la.Planner(cls.limits, cls.config)

    def test_a_reach_into_the_head_is_refused(self):
        with self.assertRaises(la.Refused) as e:
            self.planner.plan("right", ZERO, point=[0.3, 0.0, 1.3])
        self.assertEqual(e.exception.code, "TOO_CLOSE")

    def test_a_reach_runs_on_the_fake_arms(self):
        a = live(runner.FakeRobotState(), planner=self.planner, config=self.config)
        move = a.move_to_point("right", [0.45, -0.25, 0.85])
        self.assertGreaterEqual(move.clearance_mm, 30.0)
        here, _ = self.planner.tool("right", a.arms.measured())
        self.assertLess(max(abs(h - t) for h, t in zip(here, [0.45, -0.25, 0.85])), 0.012)

    def test_a_recorded_program_is_a_valid_show(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "architect.yaml")
            r = la.ShowRecorder(self.planner, self.config, self.limits, path)
            r.set_gripper("right", 1.0)
            r.move_to_point("right", [0.45, -0.25, 0.85])
            r.move_by("right", [0, 0, -0.08])
            r.set_gripper("right", 0.0)
            r.move_to_named("right", "offer")
            r.stow()
            tl = cue.load_show(path).export(self.limits, LIMITS_PATH)
        self.assertTrue(tl["valid"], tl["problems"])


@unittest.skipUnless(HAVE_MUJOCO, "needs mujoco (the twin)")
class Publish(unittest.TestCase):
    def test_an_approved_program_becomes_a_stamped_listed_show(self):
        import tempfile
        from opendubs_architect import api, show_backend
        from opendubs_architect.demo_runner import Program
        code = open(os.path.join(HERE, "..", "examples", "arms_wave.py")).read()
        with tempfile.TemporaryDirectory() as d:
            arms = os.path.join(d, "arms.yaml")
            with open(arms, "w") as fh:
                fh.write('workspace:\n  right: {x: [0.25, 0.6], y: [-0.45, 0.1], z: [0.6, 1.1]}\n')
            os.environ["OPENDUBS_ARCHITECT_ARMS"] = arms
            try:
                out = show_backend.Publisher(os.path.join(d, "shows"), api.PROFILES["arms"], LIMITS_PATH)(Program(code, "wave"))
            finally:
                del os.environ["OPENDUBS_ARCHITECT_ARMS"]
            show = cue.load_show(out["path"])
            self.assertIn("label", show.doc["palette"])           # the Shows page lists it
            self.assertGreaterEqual(show.doc["clearance"]["min_mm"], 30.0)
            self.assertEqual(os.listdir(os.path.join(d, "shows")), [os.path.basename(out["path"])])


if __name__ == "__main__":
    unittest.main(verbosity=2)
