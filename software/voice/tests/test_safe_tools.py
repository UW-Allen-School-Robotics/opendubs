#!/usr/bin/env python3
"""The safety boundary: a language model reaches the face and a read-only status, never motion.

    python3 software/voice/tests/test_safe_tools.py
"""
import os
import re
import unittest

import fakes  # noqa: F401  (puts the service on sys.path)

import face
import safe_tools as st

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.dirname(HERE)

MOTION_WORDS = ("GAZE", "TRACK", "TOUCH", "PARK", "MAST", "DRIVE", "ESTOP", "LIFT", "ARM", "RUN",
                "ROUTINE", "CMD_VEL", "RELAY")


class Boundary(unittest.TestCase):
    def test_every_effect_is_a_face_line_a_status_read_or_the_end(self):
        effects = st.every_possible_effect()
        self.assertGreater(len(effects), 15)
        for a in effects:
            self.assertIsNone(a.refused, a)
            if a.head_line:
                self.assertIn(a.head_line.split()[0], st.ALLOWED_HEAD_VERBS)
                self.assertNotIn("\n", a.head_line)
                face.check(a.head_line)                   # and the door client accepts it
            elif a.http:
                self.assertIn(a.http, st.ALLOWED_HTTP)
                self.assertEqual(a.http[0], "GET")
            else:
                self.assertTrue(a.end_conversation)

    def test_no_motion_tool_exists_even_if_routines_are_listed(self):
        os.environ["OPENDUBS_VOICE_ROUTINES"] = "wave,bow"
        try:
            names = {t["name"] for t in st.tool_definitions()}
        finally:
            del os.environ["OPENDUBS_VOICE_ROUTINES"]
        self.assertEqual(names, {"set_expression", "face_gesture", "robot_status", "end_conversation"})
        self.assertEqual(st.plan("request_routine", {"routine": "wave"}, st.Limiter()).refused is not None, True)

    def test_vocabularies_come_from_the_protocol(self):
        import head_protocol as hp
        self.assertEqual(set(st.EMOTE_NAMES) - {"auto"}, {e.lower() for e in hp.EMOTES})
        self.assertEqual(set(st.MOVE_NAMES), set(hp.MOVES))
        for t in st.tool_definitions():
            self.assertTrue(t["strict"])
            self.assertFalse(t["input_schema"]["additionalProperties"])

    def test_hostile_arguments_are_refused(self):
        lim = st.Limiter()
        for name, args in [
            ("set_expression", {"expression": "SOFT\nGAZE 0 0"}),
            ("set_expression", {"expression": "soft", "then": "drive"}),
            ("set_expression", {"expression": 3}),
            ("set_expression", "soft"),
            ("face_gesture", {"gesture": "wink_l; MAST UP"}),
            ("face_gesture", {"gesture": "../../etc"}),
            ("robot_status", {"path": "/api/estop/clear"}),
            ("end_conversation", {"now": True}),
            ("drive", {"forward": 1}),
            ("", {}),
            (None, None),
        ]:
            a = st.plan(name, args, lim, now=0)
            self.assertIsNotNone(a.refused, (name, args))
            self.assertIsNone(a.head_line)
            self.assertIsNone(a.http)

    def test_face_changes_are_rate_limited(self):
        lim = st.Limiter()
        ok = [st.plan("face_gesture", {"gesture": "blink2"}, lim, now=0.1 * i).refused is None
              for i in range(8)]
        self.assertEqual(ok.count(True), st.FACE_BURST)
        self.assertIsNone(st.plan("face_gesture", {"gesture": "blink2"}, lim, now=10).refused)

    def test_the_door_client_refuses_anything_else(self):
        for bad in ("GAZE 0 0", "EMOTE SOFT\nGAZE 0 0", "PARK", "emote soft", "MAST UP", "EMOTE " + "A" * 80,
                    "DRIVE 1 0 0", "EMOTE SOFT;GAZE"):
            with self.assertRaises(face.FaceRefused, msg=bad):
                face.check(bad)
        for good in ("EMOTE SOFT", "MOVE wink_l", "CONVO LISTEN 3000", "CONVO IDLE"):
            face.check(good)

    def test_status_says_unknown_never_fine(self):
        s = st.status_summary({"estop": None, "ch5": None, "fault": None, "arms": None})
        self.assertIn("e-stop: unknown", s)
        self.assertIn("cannot be read", st.status_summary(None))

    def test_the_service_has_no_other_way_out(self):
        """No module of the service may write to the robot by any other route: no POST, no ROS
        publisher, no serial port, no socket to anything but the door's loopback UDP."""
        for name in os.listdir(SERVICE):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(SERVICE, name), encoding="utf-8") as f:
                src = f.read()
            for pat in (r'method\s*=\s*"POST"', r"\brclpy\b", r"\bserial\b", r"/api/(estop|mast|drive|choreo/run)",
                        r"SOCK_STREAM"):
                self.assertIsNone(re.search(pat, src), f"{name}: {pat}")


if __name__ == "__main__":
    unittest.main()
