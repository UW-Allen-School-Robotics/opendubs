"""The wall tag test (tag_test.py): the camera matrix for a frame from the tablet's optics, and the head is
never moved while an arm is up. No robot, no tablet."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tag_test

# A 4:3 sensor streaming 16:9: full width, the top and bottom cropped, then scaled 0.48.
BACK = {"facing": "back", "focal_mm": [4.0], "sensor_mm": [6.4, 4.8], "pixel_array": [4000, 3000],
        "active": [0, 0, 4000, 3000], "pre_active": [0, 0, 4000, 3000]}


def test_the_frame_matrix_scales_and_crops_the_sensor_to_the_frame():
    k, dist, source = tag_test.frame_matrix(BACK, 1920, 1080)
    assert "focal length" in source and dist is None
    assert abs(k[0][0] - 4.0 * 4000 / 6.4 * 0.48) < 1e-6 and k[0][2] == 960 and k[1][2] == 540
    cal = dict(BACK, intrinsic=[2500.0, 2500.0, 2010.0, 1490.0, 0.0], distortion=[0.1, -0.2, 0.01, 0.001, 0.002])
    k, dist, source = tag_test.frame_matrix(cal, 1920, 1080)
    assert "own calibration" in source
    assert abs(k[0][0] - 1200) < 1e-6 and abs(k[0][2] - 2010 * 0.48) < 1e-6
    assert abs(k[1][2] - (1490 - (3000 - 1080 / 0.48) / 2) * 0.48) < 1e-6
    assert dist == [0.1, -0.2, 0.001, 0.002, 0.01]                 # Android k1 k2 k3 p1 p2 -> OpenCV k1 k2 p1 p2 k3
    assert tag_test.frame_matrix(None, 1920, 1080) is None


def test_the_head_is_not_moved_while_an_arm_is_up(monkeypatch):
    import arm_clearance
    import neck_follow
    sent = []
    monkeypatch.setattr(arm_clearance, "why_not", lambda now=None: "the right arm's shoulder is raised")
    monkeypatch.setattr(neck_follow, "_ask", lambda *a: sent.append(a) or "OK")
    why = asyncio.run(tag_test.move_head(90, 90, 0.0, time.monotonic() + 1))
    assert "raised" in why and not sent
