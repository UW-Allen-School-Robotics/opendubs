#!/usr/bin/env python3
"""The butler's voice files are used only as pinned, and spoken in British English at the butler's pace.
Stdlib only (the synthesis needs Kokoro and is exercised on the robot).

    python3 software/architect/tests/test_butler.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import butler  # noqa: E402


class Butler(unittest.TestCase):
    def test_a_file_that_does_not_match_its_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(butler.speech.BackendUnavailable):
                butler.verify(root)                                  # nothing fetched
            for (repo, rev, path) in butler.FILES:
                p = butler._local(root, repo, rev, path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(b"not the pinned file")
            with self.assertRaises(butler.speech.BackendUnavailable) as e:
                butler.verify(root)
            self.assertIn("does not match its pin", str(e.exception))

    def test_bm_george_in_british_english(self):
        calls = []

        class Result:
            audio = [0.0, 0.5]

        def pipeline(text, voice, speed):
            calls.append((voice, speed))
            return [Result()]
        tts = butler.ButlerTTS("/models", pipeline=pipeline)
        self.assertEqual(tts.lang, "b")
        self.assertEqual(len(tts._synth("Good evening.")), 4)
        voice, speed = calls[0]
        self.assertTrue(voice.endswith("/voices/bm_george.pt"), voice)
        self.assertEqual(speed, butler.SPEED)


if __name__ == "__main__":
    unittest.main()
