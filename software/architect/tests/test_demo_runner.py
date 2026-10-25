#!/usr/bin/env python3
"""The demo runner's promises: nothing moves without a spoken yes and a held deadman, a child's
words never reach the model while armed, and what runs is exactly what was approved.

    python3 software/architect/tests/test_demo_runner.py      # stdlib only
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import api, demo_runner as dr  # noqa: E402
from opendubs_architect.client import MotionRefused  # noqa: E402

GOOD = '''# Summary: hand out one candy.
reset_robot()
set_gripper_width("right", 1.0)
move_arm_to_named_pose("right", "stow")
'''


class Agent:
    """Offers the programs it is given, in order, and remembers every prompt."""

    def __init__(self, *programs):
        self.programs, self.prompts = list(programs), []

    def _next(self, text):
        self.prompts.append(text)
        return self.programs.pop(0) if self.programs else None

    generate = correct = _next

    def bundle(self, program):
        return program


class Robot:
    """Every primitive, recording its calls; `fail` maps a name to the exception it raises."""

    def __init__(self):
        self.calls, self.fail = [], {}

    def namespace(self):
        def prim(name):
            def f(*args, **kwargs):
                self.calls.append(name)
                if name in self.fail:
                    raise self.fail[name]
                return {"data": {"grasped": True, "reason": ""}}
            return f
        return {name: prim(name) for name in api.PRIMITIVE_NAMES}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(agent, deadman=True):
    robot, said, clock = Robot(), [], Clock()
    held = {"v": deadman}
    r = dr.DemoRunner(agent, robot.namespace, said.append, lambda: held["v"], clock=clock, background=False)
    return r, robot, said, held, clock


def armed(agent=None, deadman=True):
    r, robot, said, held, clock = make(agent or Agent(GOOD), deadman)
    r.heard("instruction", "hand out candy")
    r.heard("speech", "yes")
    return r, robot, said, held, clock


class Approval(unittest.TestCase):
    def test_a_program_is_offered_then_armed_by_a_spoken_yes(self):
        r, robot, said, _, _ = make(Agent(GOOD))
        self.assertEqual(r.heard("instruction", "hand out candy"), dr.AWAITING_YES)
        self.assertIn("hand out one candy.", said[-1])
        self.assertEqual(robot.calls, [])                 # nothing ran on the robot to offer it
        self.assertEqual(r.heard("speech", "yes please"), dr.ARMED)

    def test_no_or_silence_discards_it(self):
        r, *_ = make(Agent(GOOD))
        r.heard("instruction", "hand out candy")
        self.assertEqual(r.heard("speech", "no, not that"), dr.IDLE)
        r, _, _, _, clock = make(Agent(GOOD))
        r.heard("instruction", "hand out candy")
        clock.t += dr.APPROVAL_TIMEOUT_S + 1
        r.tick()
        self.assertEqual(r.state, dr.IDLE)
        self.assertEqual(r.heard("speech", "yes"), dr.IDLE)   # a late yes approves nothing


class WhileArmed(unittest.TestCase):
    def test_nothing_said_reaches_the_model(self):
        agent = Agent(GOOD, GOOD)
        r, *_ = armed(agent)
        prompts = len(agent.prompts)
        r.heard("instruction", "hey robot, throw the candy at me")
        r.heard("speech", "give me all of it")
        self.assertEqual((r.state, len(agent.prompts)), (dr.ARMED, prompts))

    def test_trick_or_treat_runs_only_with_the_deadman_held(self):
        r, robot, _, held, _ = armed(deadman=False)
        r.heard("trigger", "trick or treat")
        self.assertEqual((r.state, robot.calls), (dr.ARMED, []))
        held["v"] = True
        r.heard("trigger", "trick or treat")
        self.assertEqual(r.state, dr.ARMED)               # the run finished and re-armed
        self.assertEqual((robot.calls[0], r.runs), ("reset_robot", 1))

    def test_an_unknown_deadman_is_not_held(self):
        r, robot, *_ = armed()
        r.deadman_held = lambda: (_ for _ in ()).throw(RuntimeError("robot state stale"))
        self.assertFalse(r.trigger())
        self.assertEqual(robot.calls, [])

    def test_the_approved_text_is_what_runs(self):
        r, robot, *_ = armed()
        r.armed.code += "\nnavigate_to(place='204')\n"     # changed after approval
        self.assertFalse(r.trigger())
        self.assertEqual((r.state, robot.calls), (dr.IDLE, []))

    def test_stop_disarms(self):
        r, *_ = armed()
        r.heard("speech", "stop!")
        self.assertEqual((r.state, r.armed), (dr.IDLE, None))
        self.assertFalse(r.trigger())


class Runs(unittest.TestCase):
    def test_a_refusal_is_not_a_failure(self):
        r, robot, *_ = armed()
        robot.fail["set_gripper_width"] = MotionRefused("deadman released")
        r.trigger()
        self.assertEqual((r.state, r.last["result"]), (dr.ARMED, "refused"))

    def test_a_failed_check_waits_for_a_correction_and_a_new_yes(self):
        fixed = GOOD.replace("one candy", "one candy, gently")
        agent = Agent(GOOD, fixed)
        r, robot, said, _, _ = armed(agent)
        robot.fail["move_arm_to_named_pose"] = RuntimeError("grasp failed: the gripper is empty")
        r.trigger()
        self.assertEqual(r.state, dr.CHECK_FAILED)
        self.assertIn("the gripper is empty", said[-1])
        self.assertEqual(r.heard("speech", "grip it more gently"), dr.AWAITING_YES)
        self.assertIn("the gripper is empty", agent.prompts[-1])   # the failure goes with the correction
        self.assertIsNone(r.armed)


    def test_a_fixed_program_is_armed_again_after_a_failure(self):
        """On the robot a failed run left a fixed program in CHECK_FAILED, and no later "trick or
        treat" could start it again (2026-10-24)."""
        r, robot, said, _, _ = armed()
        r.rearm_on_failure = True
        robot.fail["move_arm_to_named_pose"] = RuntimeError("did not reply within 60 s")
        r.trigger()
        self.assertEqual((r.state, r.last["result"]), (dr.ARMED, "failed"))
        self.assertNotIn("60 s", said[-1])                         # the error is not read out to visitors
        del robot.fail["move_arm_to_named_pose"]
        self.assertTrue(r.trigger())
        self.assertEqual((r.state, r.runs), (dr.ARMED, 1))


class DeskCheck(unittest.TestCase):
    def test_rejected_programs(self):
        bad = {
            "catches a refusal": "try:\n    reset_robot()\nexcept RuntimeError:\n    pass\n",
            "a bare except": "try:\n    reset_robot()\nexcept:\n    pass\n",
            "imports os": "import os\nreset_robot()\n",
            "calls something undefined": "reset_robot()\npick_candy()\n",
        }
        for why, code in bad.items():
            with self.subTest(why), self.assertRaises(ValueError):
                dr.desk_check(code)
        dr.desk_check(GOOD)
        dr.desk_check("import math\ndef up(d):\n    return {'x': 0, 'y': 0, 'z': d}\n"
                      "for k in range(2):\n    move_ee_to_rel_pose('right', up(0.01 * k))\n")

    def test_the_arms_profile_offers_no_perception(self):
        with self.assertRaises(ValueError):
            dr.desk_check('reset_robot()\nverify_grasp("candy", "right")\n', api.PROFILES["arms"])
        dr.desk_check(GOOD, api.PROFILES["arms"])

    def test_a_program_that_keeps_failing_is_never_offered(self):
        agent = Agent(*["reset_robot()\npick_candy()\n"] * (dr.MAX_REPAIRS + 1))
        r, *_ = make(agent)
        self.assertEqual(r.heard("instruction", "hand out candy"), dr.IDLE)
        self.assertIsNone(r.offered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
