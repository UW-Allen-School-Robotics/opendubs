import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import neck_state as ns


def test_the_heads_angles_reach_ros_in_real_radians_with_ros_signs():
    """The arms' head clearance reads these. Wrong scale or sign would put the head somewhere it is not:
    the servos are 300 degrees over 0-180 commanded, pan + is the robot's left, tilt + is up."""
    n = ns.parse("OK neck pan=60.0 tilt=99.0 target=30.0,95.0 moving=1 owner=gaze tilt_band=80,100 "
                 "commissioned=1 tilt_pending=none chest=1500")
    assert n["moving"] and n["target"] == (30.0, 95.0) and n["tilt_band"] == (80.0, 100.0)
    m = ns.message(n["pan"], n["tilt"], 1000.25)
    assert m["name"] == ["head_pan_joint", "head_tilt_joint"]
    pan, tilt = m["position"]
    assert math.isclose(pan, math.radians(-50.0), abs_tol=1e-4)     # 30 commanded right = 50 real, right
    assert math.isclose(tilt, math.radians(15.0), abs_tol=1e-4)     # 9 commanded up = 15 real, up
    assert m["header"]["stamp"] == {"sec": 1000, "nanosec": 250000000}
    lim = ns.limits_message(n["tilt_band"], 1000.0)
    assert math.isclose(lim["position"][0], math.radians(-150.0), abs_tol=1e-4)
    assert math.isclose(lim["position"][3], math.radians(16.6667), abs_tol=1e-3)
    assert ns.parse("ERR neck no pan/tilt fitted") is None            # nothing is published, never a guess
