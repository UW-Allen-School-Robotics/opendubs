#!/usr/bin/env python3
"""English only, no GPL: the guard's rules on fake package metadata and a fake filesystem, and the
pinned requirement files themselves. No packages installed.

    python3 software/voice/tests/test_no_gpl.py
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "..", "image")
sys.path.insert(0, IMAGE)

import check_no_gpl as g  # noqa: E402


class Meta(dict):
    def get_all(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else None


def dist(name, **meta):
    return type("D", (), {"metadata": Meta(Name=name, **meta)})()


class Rules(unittest.TestCase):
    def test_classify(self):
        cases = {
            "GPL-3.0-or-later": "gpl",
            "GNU GENERAL PUBLIC LICENSE\n Version 3, 29 June 2007": "gpl",
            "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)": "gpl",
            "AGPL-3.0": "gpl",
            "LGPL": "lgpl",
            "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)": "lgpl",
            "LGPL-2.1 OR GPL-2.0": "gpl",
            "MIT": "ok",
            "Apache-2.0": "ok",
            "BSD-3-Clause | License :: OSI Approved :: BSD License": "ok",
            "Python Software Foundation License (GPL-compatible)": "ok",
            "": "ok",
        }
        for text, want in cases.items():
            self.assertEqual(g.classify(text), want, text)

    def test_distributions(self):
        found = g.check_distributions([
            dist("phonemizer-fork", License="whatever it says"),
            dist("espeakng_loader"),
            dist("some-gpl-thing", **{"License-Expression": "GPL-2.0-only"}),
            dist("num2words", License="LGPL"),
            dist("unreviewed-lgpl", Classifier=["License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"]),
            dist("torch", License="BSD-3-Clause"),
            dist("kokoro", Classifier=["License :: OSI Approved :: Apache Software License"]),
        ])
        text = "\n".join(found)
        for bad in ("phonemizer-fork", "espeakng-loader", "some-gpl-thing", "unreviewed-lgpl"):
            self.assertIn(bad, text)
        for ok in ("num2words", "torch", "kokoro"):
            self.assertNotIn(f"package {ok} ", text)
        self.assertEqual(len(found), 4)

    def test_files_and_data(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "site", "espeakng_loader", "espeak-ng-data", "voices"))
            open(os.path.join(d, "site", "espeakng_loader", "libespeak-ng.so.1"), "w").close()
            open(os.path.join(d, "site", "fine.so"), "w").close()
            found = g.check_files([d])
        self.assertEqual(len(found), 2, found)

    def test_dpkg(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("Package: libc6\nStatus: install ok installed\n\n"
                    "Package: espeak-ng-data\nStatus: install ok installed\n\n"
                    "Package: libespeak-ng1\nStatus: deinstall ok config-files\n\n")
        try:
            found = g.check_dpkg(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(found, ["system package espeak-ng-data is installed"])


class Requirements(unittest.TestCase):
    def lines(self, name):
        with open(os.path.join(IMAGE, name)) as f:
            return [ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()]

    def test_no_gpl_package_is_named(self):
        for name in os.listdir(IMAGE):
            if name.startswith("requirements"):
                for ln in self.lines(name):
                    pkg = g.canonical(re.split(r"[=@<>\[ ]", ln, maxsplit=1)[0])
                    self.assertNotIn(pkg, g.BANNED, f"{name}: {ln}")
                    self.assertNotIn("[en]", ln, f"{name}: misaki[en] pulls in GPL code")

    def test_everything_is_pinned(self):
        for name in ("requirements.txt", "requirements-speech.txt", "requirements-speech-nodeps.txt",
                     "requirements-torch-cpu.txt"):
            for ln in self.lines(name):
                if ln.startswith("--index-url"):
                    continue
                self.assertTrue("==" in ln or re.search(r"@ https://\S+", ln), f"{name}: {ln} is not pinned")

    def test_kokoro_and_misaki_come_without_their_dependencies(self):
        nodeps = {g.canonical(re.split(r"[=@ ]", ln)[0]) for ln in self.lines("requirements-speech-nodeps.txt")}
        self.assertEqual(nodeps, {"kokoro", "misaki"})
        misaki = next(ln for ln in self.lines("requirements-speech-nodeps.txt") if ln.startswith("misaki"))
        self.assertRegex(misaki, r"/archive/[0-9a-f]{40}\.tar\.gz$")      # a commit, never a branch
        speech = " ".join(self.lines("requirements-speech.txt"))
        self.assertNotIn("kokoro", speech)
        self.assertNotIn("misaki", speech)

    def test_the_image_runs_the_guard_last(self):
        with open(os.path.join(IMAGE, "Dockerfile.voice")) as f:
            df = f.read()
        self.assertIn("--no-deps -r /opt/voice/requirements-speech-nodeps.txt", df)
        runs = [ln for ln in df.splitlines() if ln.startswith("RUN ")]
        self.assertIn("check_no_gpl.py", runs[-2] + runs[-1])           # after every install
        self.assertNotRegex(df, r"(COPY|ADD) .*(\.pth|\.onnx|\.safetensors|models/)",
                            "weights must not be copied into the image")


if __name__ == "__main__":
    unittest.main()
