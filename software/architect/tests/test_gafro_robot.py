#!/usr/bin/env python3
"""The gafro backend's promises (gafro_robot.py): a goal goes out only with the deadman held and is
cancelled the moment it is released, after which nothing more is sent; a close that stops on something
is a grip; a hand out of reach is tried with the fingers less steeply down, then given up with the arm
back at ready; and the demo runner's cancel ends a run with its program still armed.

    python3 software/architect/tests/test_gafro_robot.py      # no ROS, no MuJoCo
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import gafro_robot as gr  # noqa: E402
from opendubs_architect import live_arms  # noqa: E402
from opendubs_architect.client import MotionRefused  # noqa: E402

gr.POLL_S = 0.01


class Handle:
    def __init__(self, result, after_s=0.0):
        self.res, self.at, self.cancelled = result, time.monotonic() + after_s, False

    def done(self):
        return self.cancelled or time.monotonic() >= self.at

    def result(self):
        return self.res

    def cancel(self):
        self.cancelled = True


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Ros:
    def __init__(self):
        self.sent, self.handles, self.move_s = [], [], 0.0

    def _h(self, kind, result):
        self.sent.append(kind)
        h = Handle(result, self.move_s)
        self.handles.append(h)
        return h

    def command(self, side, name, params):
        return self._h(("command", name), R(result_code=0, message="reached"))

    def grip(self, side, metres):
        self.grip_m = metres
        return self._h(("grip", metres), self.grip_result)

    def trajectory(self, side, points):
        return self._h(("trajectory", points[-1][1][0]), R(error_code=0, error_string=""))

    def joints_deg(self):
        return {"right": list(self.at)}

    at = [0.0] * 7
    grip_result = R(reached_goal=True, stalled=False, position=0.0)


class Move:
    def __init__(self, goal):
        self.side, self.goal, self.seconds, self.clearance_mm, self.start = "right", goal, 1.0, 40.0, [0.0] * 7

    def points(self):
        return [(1.0, list(self.goal))]


class Planner:
    """Reaches everything, except (refuse) the approach directions listed."""

    def __init__(self, ros, refuse=()):
        self.ros, self.refuse, self.asked = ros, list(refuse), []

    def plan(self, side, pose, point=None, approach=None):
        self.asked.append(None if approach is None else tuple(approach))
        if (None if approach is None else tuple(approach)) in self.refuse:
            raise live_arms.Refused("OUT_OF_REACH", "the right arm cannot reach it")
        goal = [round(point[0] * 10, 1)] + [0.0] * 6
        self.ros.at = goal                           # it gets there
        return Move(goal)


class Config:
    workspace = {"right": {"x": [0.4, 0.9], "y": [-0.5, 0.2], "z": [0.4, 1.2]}}
    head = [(0.0, 0.0)]
    lift_mm = 0.0


def robot(ros, planner=None, held=lambda: True):
    return gr.GafroRobot(ros, Config(), planner or Planner(ros), deadman_held=held)


POSE = {"position": {"x": 0.7, "y": -0.1, "z": 0.6}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}}


class Gates(unittest.TestCase):
    def test_nothing_is_sent_without_the_deadman(self):
        ros = Ros()
        with self.assertRaises(MotionRefused):
            robot(ros, held=lambda: False).move_ee_to_pose("right", POSE)
        self.assertEqual(ros.sent, [])

    def test_a_release_mid_move_cancels_it_and_nothing_more_is_sent(self):
        ros, state = Ros(), {"held": True}
        ros.move_s = 5.0                              # a long move
        r = robot(ros, held=lambda: state["held"])
        import threading
        threading.Timer(0.1, lambda: state.update(held=False)).start()
        with self.assertRaises(MotionRefused):
            r.move_ee_to_pose("right", POSE)
        self.assertTrue(ros.handles[-1].cancelled)
        with self.assertRaises(MotionRefused):
            r.set_gripper_width("right", 1.0)
        self.assertEqual(len(ros.sent), 1)

    def test_a_point_outside_the_workspace_is_refused_before_planning(self):
        ros = Ros()
        p = Planner(ros)
        with self.assertRaises(MotionRefused):
            robot(ros, p).move_ee_to_pose("right", {**POSE, "position": {"x": 0.2, "y": 0.0, "z": 0.6}})
        self.assertEqual((p.asked, ros.sent), ([], []))


class Gripper(unittest.TestCase):
    def test_the_opening_is_metres_of_finger_travel_and_a_stall_is_a_grip(self):
        ros = Ros()
        ros.grip_result = R(reached_goal=False, stalled=True, position=0.012)   # stopped on a candy
        robot(ros).set_gripper_width("right", 0.5)
        self.assertAlmostEqual(ros.grip_m, 0.022)

    def test_a_gripper_that_does_not_get_there_is_refused(self):
        ros = Ros()
        ros.grip_result = R(reached_goal=False, stalled=False, position=0.0)
        with self.assertRaises(MotionRefused):
            robot(ros).set_gripper_width("right", 1.0)


class Head(unittest.TestCase):
    def test_the_neck_command_takes_the_apis_signs(self):
        self.assertEqual(gr.neck_command(0, 0), {"pan": 90.0, "tilt": 90.0})
        down = gr.neck_command(0, -25)                # looking down: the neck's tilt degrees fall
        self.assertLess(down["tilt"], 90.0)
        self.assertAlmostEqual(down["tilt"], 90.0 - 25 / gr.SERVO_SPAN, 1)
        self.assertGreater(gr.neck_command(20, 0)["pan"], 90.0)   # + pan is to the robot's left


class Hand(unittest.TestCase):
    def setUp(self):
        self.ros = Ros()
        self.p = Planner(self.ros, refuse=[gr.OVER_PALM[0]])     # steeply down is out of reach
        self.r = robot(self.ros, self.p)
        self.r.frames = lambda: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]   # the optical frame IS the chassis
        gr.TREAT_S = 0.0

    def feed(self, xyz, n=gr.STABLE_FRAMES):
        for _ in range(n):
            self.r.on_hand_state({"palms": {"left": {"xyz": list(xyz), "followable": True}}})

    def test_a_still_hand_is_reached_with_the_next_finger_direction_then_released(self):
        import threading
        threading.Timer(0.05, lambda: self.feed([1.0, -0.1, 1.0])).start()
        got = self.r.place_in_hand("right", timeout_s=2.0)
        self.assertTrue(got["released"], got)
        self.assertEqual(self.p.asked, [gr.OVER_PALM[0], gr.OVER_PALM[1]])
        self.assertIn(("command", "ready"), self.ros.sent)
        self.assertEqual(self.ros.grip_m, gr.FINGER_TRAVEL_M)      # opened fully at the hand

    def test_no_hand_in_time_goes_back_to_ready_holding_the_candy(self):
        got = self.r.place_in_hand("right", timeout_s=0.2)
        self.assertFalse(got["released"])
        self.assertEqual(self.ros.sent, [("command", "ready")])

    def test_a_hand_hanging_down_is_not_a_hand_held_out(self):
        self.feed([1.0, -0.1, 0.5])
        self.assertIsNone(self.r.get_hand_pose()["hand"])


class Treat(unittest.TestCase):
    """treat_calls.py (2026-10-29): a planned move goes to move_joints as its goal in radians, the gripper is only
    open or shut, and one shut on nothing is a gripper closed (to travel), not a failed call."""

    def calls(self, reply):
        from opendubs_architect import treat_calls as tc
        t = tc.TreatCalls.__new__(tc.TreatCalls)
        t.speed, t.sent = 0.4, []

        class Future:
            def done(self):
                return True

            def result(self):
                return R(result_code=R(result_code=reply[0], message=reply[1]), data=reply[2])

        class Client:
            def __init__(self, name):
                self.name = name

            def call_async(self, request):
                t.sent.append((self.name, request.req))
                return Future()
        t.client = lambda name: Client(name)
        t.Request = lambda req: R(req=req)
        return t

    def test_a_planned_move_is_one_move_joints_to_its_goal(self):
        import json, math
        t = self.calls((0, "moved", "{}"))
        res = t.trajectory("right", [(0.5, [0.0] * 7), (2.0, [90.0, 0, 0, 45.0, 0, 0, 0])]).result()
        name, req = t.sent[0]
        self.assertEqual((name, res.error_code), ("move_joints", 0))
        self.assertAlmostEqual(json.loads(req)["joints"][0], math.pi / 2)
        self.assertEqual(json.loads(req)["speed"], 0.4)

    def test_the_gripper_is_open_or_shut_and_shut_on_nothing_is_shut(self):
        t = self.calls((1, "close_gripper: FAILURE: closed on nothing: no object between the fingers", "{}"))
        res = t.grip("right", 0.0).result()
        self.assertEqual((t.sent[0][0], res.reached_goal, res.stalled), ("gripper_close", True, False))
        t = self.calls((0, "gripper closed on something at 12.0 mm (holding it)", '{"position_mm": 12.0, "holding": true}'))
        res = t.grip("right", 0.0).result()
        self.assertEqual((res.stalled, res.position), (True, 0.012))
        t = self.calls((0, "gripper open at 44.0 mm", '{"position_mm": 44.0}'))
        self.assertTrue(t.grip("right", 0.044).result().reached_goal)
        self.assertEqual(t.sent[0][0], "gripper_open")
        t = self.calls((1, "the gripper did not move (still 43.0 mm open)", '{"stopped": null}'))
        self.assertFalse(t.grip("right", 0.0).result().reached_goal)


class Cancel(unittest.TestCase):
    def test_a_cancel_ends_the_run_and_keeps_the_program_armed(self):
        from opendubs_architect import demo_runner as dr
        code = "# Summary: wait.\nfor _ in range(100):\n    wait(0.05)\n"
        released = []
        runner = dr.DemoRunner(dr.ProgramAgent(code), lambda: {"wait": time.sleep}, lambda t: None, lambda: True,
                               primitives=("wait",), rearm_on_failure=True, after_run=lambda: released.append(1))
        runner.armed, runner.state = dr.Program(code, "wait"), dr.ARMED
        self.assertTrue(runner.trigger("test"))
        time.sleep(0.2)
        self.assertTrue(runner.cancel("the demo day paused"))
        self.assertFalse(runner.trigger("a second trick or treat"))   # still RUNNING until it has ended
        end = time.monotonic() + 3.0
        while runner.state == dr.RUNNING and time.monotonic() < end:
            time.sleep(0.02)
        self.assertEqual((runner.state, runner.last["result"], released), (dr.ARMED, "stopped", [1]))
        self.assertFalse(runner.cancel("nothing running"))


if __name__ == "__main__":
    unittest.main()
