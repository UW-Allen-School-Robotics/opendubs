"""The head keeps out of a raised arm's way (arm_clearance.py): no GAZE while a shoulder is up, or unknown."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import arm_clearance as ac


def _js(left_deg, right_deg):
    return {"name": ["openarm_left_joint1", "openarm_left_joint2", "openarm_right_joint2"],
            "position": [0.0, math.radians(left_deg), math.radians(right_deg)]}


def test_a_raised_shoulder_or_an_unknown_one_stops_the_head():
    ac.last.update(j2={}, at=None)
    assert "unknown" in ac.why_not(now=0.0)                          # nothing heard yet: wait
    ac.note(_js(-90, 100), now=10.0)
    assert ac.why_not(now=10.5) is None                              # both low and fresh: follow
    ac.note(_js(-90, 150), now=11.0)                                 # a run stopped mid-move, right arm up
    assert "right arm's shoulder is raised" in ac.why_not(now=11.2)
    ac.note(_js(-150, 100), now=12.0)                                # the LEFT arm raises to NEGATIVE J2
    assert "left arm's shoulder is raised" in ac.why_not(now=12.1)
    ac.note(_js(-90, 100), now=13.0)
    assert ac.why_not(now=13.0 + ac.STALE_S + 0.1).startswith("the arms' positions are unknown")   # gone quiet
