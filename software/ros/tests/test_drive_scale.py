#!/usr/bin/env python3
"""drive_scale: the software speed cap caps every wheel, and the strafe trim. Pure Python; no ROS.
Run: python3 software/ros/tests/test_drive_scale.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import drive_scale as d  # noqa: E402

K = d.DEFAULT_AUTO_CAP / d.FW_AUTO_MAX_UNIT     # DRIVE at the cap: 0.25 / 0.35


def wheels(fwd, rot, strafe):
    """The firmware's mecanum mix (main.cpp driveMix), before its own normalisation."""
    return (fwd + strafe + rot, fwd - strafe - rot, fwd - strafe + rot, fwd + strafe - rot)


class CapTest(unittest.TestCase):
    def test_full_scale_on_one_axis_is_the_cap(self):
        fwd, rot, strafe = d.drive_units(0.5, 0, 0, 0.5, 0.4, 1.0)
        self.assertAlmostEqual(fwd, K); self.assertEqual((rot, strafe), (0.0, 0.0))

    def test_asking_for_more_gets_the_cap(self):
        fwd, _, _ = d.drive_units(5.0, 0, 0, 0.5, 0.4, 1.0)
        self.assertAlmostEqual(fwd, K)

    def test_no_wheel_exceeds_the_cap_whatever_the_mix(self):
        for cmd in ((0.5, 0.4, 1.0), (-0.5, 0.4, -1.0), (0.5, -0.4, 0.3), (3, 3, 3), (0.1, 0.0, 1.0)):
            fwd, rot, strafe = d.drive_units(*cmd, 0.5, 0.4, 1.0)
            for w in wheels(fwd, rot, strafe):
                self.assertLessEqual(abs(w), K + 1e-9, (cmd, w))

    def test_scaling_keeps_the_direction(self):
        fwd, rot, strafe = d.drive_units(0.5, 0.4, 0.0, 0.5, 0.4, 1.0)   # 1 + 1 units: halved each
        self.assertAlmostEqual(fwd, K / 2); self.assertAlmostEqual(strafe, K / 2); self.assertEqual(rot, 0.0)

    def test_a_small_command_is_not_scaled(self):
        fwd, _, _ = d.drive_units(0.1, 0, 0, 0.5, 0.4, 1.0)
        self.assertAlmostEqual(fwd, 0.2 * K)

    def test_the_cap_is_one_number_to_change(self):
        fwd, _, _ = d.drive_units(0.5, 0, 0, 0.5, 0.4, 1.0, cap=0.10)
        self.assertAlmostEqual(fwd, 0.10 / d.FW_AUTO_MAX_UNIT)

    def test_never_past_the_firmware_ceiling(self):
        fwd, _, _ = d.drive_units(0.5, 0, 0, 0.5, 0.4, 1.0, cap=0.9)
        self.assertAlmostEqual(fwd, 1.0)


class SignTest(unittest.TestCase):
    """ROS turns left (+wz) and strafes left (+vy); the firmware's DRIVE means right by +rot / +str."""
    def test_left_and_counter_clockwise_become_negative(self):
        fwd, rot, strafe = d.to_firmware(*d.drive_units(0.0, 0.4, 1.0, 0.5, 0.4, 1.0))
        self.assertEqual(fwd, 0.0); self.assertLess(rot, 0); self.assertLess(strafe, 0)

    def test_forward_is_unchanged(self):
        fwd, rot, strafe = d.to_firmware(*d.drive_units(0.5, 0.0, 0.0, 0.5, 0.4, 1.0))
        self.assertGreater(fwd, 0); self.assertEqual((rot, strafe), (-0.0, -0.0))

    def test_ros_right_and_clockwise_become_positive(self):
        _fwd, rot, strafe = d.to_firmware(*d.drive_units(0.0, -0.4, -1.0, 0.5, 0.4, 1.0))
        self.assertGreater(rot, 0); self.assertGreater(strafe, 0)

    def test_firmware_mix_moves_the_robot_the_way_ros_asked(self):
        # firmware mix: FL = fwd + str + rot. A ROS strafe LEFT must give FL BACKWARD (wheel <0) and FR
        # forward, which is a mecanum left strafe before the robot's own kStrafeInvert handedness
        # correction, the same one the RC path goes through.
        fwd, rot, strafe = d.to_firmware(*d.drive_units(0.0, 0.4, 0.0, 0.5, 0.4, 1.0))
        fl, fr, rl, rr = wheels(fwd, rot, strafe)
        self.assertLess(fl, 0); self.assertGreater(fr, 0); self.assertGreater(rl, 0); self.assertLess(rr, 0)


class TrimTest(unittest.TestCase):
    def test_trim_counters_the_bleed(self):
        vx, vy, wz = d.strafe_trim(0.0, 0.1, 0.0, fwd_bleed=0.2, yaw_bleed=0.5)
        self.assertAlmostEqual(vx, -0.02); self.assertAlmostEqual(vy, 0.1); self.assertAlmostEqual(wz, -0.05)

    def test_trim_per_direction_as_measured_on_opendubs2(self):
        # strafing RIGHT at 0.18 m/s yawed -0.262 rad/s: k = -0.262 / -0.18 = 1.46. Left barely bled.
        yaw = (0.0, 1.46)
        _, _, wz_right = d.strafe_trim(0.0, -0.12, 0.0, yaw_bleed=yaw)
        _, _, wz_left = d.strafe_trim(0.0, +0.12, 0.0, yaw_bleed=yaw)
        self.assertAlmostEqual(wz_right, 1.46 * 0.12)      # turns CCW to cancel the clockwise bleed
        self.assertEqual(wz_left, 0.0)

    def test_bleed_from_env(self):
        env = {"OPENDUBS_STRAFE_YAW_BLEED_RIGHT": "1.46"}
        self.assertEqual(d.bleed_from_env(env, "YAW"), (0.0, 1.46))
        self.assertEqual(d.bleed_from_env({"OPENDUBS_STRAFE_FWD_BLEED": "0.1"}, "FWD"), (0.1, 0.1))
        self.assertEqual(d.bleed_from_env({"OPENDUBS_STRAFE_YAW_BLEED": "0.3", "OPENDUBS_STRAFE_YAW_BLEED_LEFT": "0"}, "YAW"),
                         (0.0, 0.3))
        self.assertEqual(d.bleed_from_env({}, "YAW"), (0.0, 0.0))

    def test_no_trim_by_default(self):
        self.assertEqual(d.strafe_trim(0.1, 0.2, 0.3), (0.1, 0.2, 0.3))


if __name__ == "__main__":
    unittest.main()
