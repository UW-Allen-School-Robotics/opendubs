#!/usr/bin/env python3
"""The weights' manifest and their fetcher: everything pinned, every byte verified, nothing kept
that does not match. A fake downloader; no network.

    python3 software/voice/tests/test_fetch_models.py
"""
import hashlib
import os
import re
import tempfile
import unittest

import fakes  # noqa: F401

import fetch_models as fm

HEX40, HEX64 = re.compile(r"^[0-9a-f]{40}$"), re.compile(r"^[0-9a-f]{64}$")


def spec_for(path: str, data: bytes, big: bool) -> dict:
    s = {"path": path, "size": len(data)}
    if big:
        s["sha256"] = hashlib.sha256(data).hexdigest()
    else:
        s["git_blob"] = fm.git_blob_id(data)
    return s


class Manifest(unittest.TestCase):
    def test_everything_is_pinned(self):
        models = fm.load_lock()
        self.assertEqual({m["name"] for m in models},
                         {"parakeet", "kokoro", "g2p-fallback", "qwen3-30b-a3b-w4a16", "qwen3-4b"})
        for m in models:
            self.assertRegex(m["revision"], HEX40, m["name"])
            self.assertIn(m["license"], {"CC-BY-4.0", "Apache-2.0"}, m["name"])
            for f in m["files"]:
                self.assertGreater(f["size"], 0)
                self.assertNotIn("..", f["path"])
                self.assertFalse(f["path"].startswith("/"))
                self.assertEqual(("sha256" in f) + ("git_blob" in f), 1, f)
                self.assertRegex(f.get("sha256") or f["git_blob"], HEX64 if "sha256" in f else HEX40)

    def test_git_blob_id_is_gits(self):
        self.assertEqual(fm.git_blob_id(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")


class Fetch(unittest.TestCase):
    def setUp(self):
        self.dest = tempfile.mkdtemp()
        self.files = {"big.bin": b"\x01" * 5000, "small.json": b'{"a": 1}\n'}
        self.model = {"name": "m", "repo": "org/name", "revision": "a" * 40, "license": "Apache-2.0",
                      "dest": "m", "files": [spec_for("big.bin", self.files["big.bin"], True),
                                             spec_for("sub/small.json", self.files["small.json"], False)]}
        self.urls = []

    def get(self, url, out):
        self.urls.append(url)
        name = url.rsplit("/", 1)[1]
        with open(out, "wb") as f:
            f.write(self.files["big.bin" if name == "big.bin" else "small.json"])

    def test_fetches_and_verifies_from_the_pinned_revision(self):
        n = fm.fetch(self.dest, [self.model], get=self.get, log=lambda *a: None)
        self.assertEqual(n, 2)
        self.assertEqual(self.urls, [f"https://huggingface.co/org/name/resolve/{'a' * 40}/big.bin",
                                     f"https://huggingface.co/org/name/resolve/{'a' * 40}/sub/small.json"])
        with open(os.path.join(self.dest, "m", "sub", "small.json"), "rb") as f:
            self.assertEqual(f.read(), self.files["small.json"])
        for name in ("big.bin", "sub/small.json"):       # readable by the non-root voice container
            self.assertEqual(os.stat(os.path.join(self.dest, "m", name)).st_mode & 0o777, 0o644)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "NOTICE.md")))   # credits travel with the weights
        # a second run fetches nothing, and --check passes
        self.assertEqual(fm.fetch(self.dest, [self.model], get=self.get, log=lambda *a: None), 0)
        fm.fetch(self.dest, [self.model], check_only=True)

    def test_a_tampered_download_is_refused_and_not_kept(self):
        def bad(url, out):
            with open(out, "wb") as f:
                f.write(b"\x02" * 5000)                  # right size, wrong bytes
        with self.assertRaises(fm.Mismatch):
            fm.fetch(self.dest, [self.model], get=bad, log=lambda *a: None)
        left = [f for _, _, fs in os.walk(self.dest) for f in fs]
        self.assertEqual(left, [])

    def test_a_tampered_file_on_disk_is_refused(self):
        fm.fetch(self.dest, [self.model], get=self.get, log=lambda *a: None)
        with open(os.path.join(self.dest, "m", "big.bin"), "r+b") as f:
            f.write(b"\x00")
        with self.assertRaises(fm.Mismatch):
            fm.fetch(self.dest, [self.model], check_only=True)

    def test_check_does_not_download(self):
        with self.assertRaises(fm.Mismatch):
            fm.fetch(self.dest, [self.model], get=self.get, check_only=True)
        self.assertEqual(self.urls, [])

    def test_hf_cache_layout(self):
        m = dict(self.model, hf_cache=True)
        fm.fetch(self.dest, [m], get=self.get, log=lambda *a: None)
        base = os.path.join(self.dest, "hf", "hub", "models--org--name")
        self.assertTrue(os.path.exists(os.path.join(base, "snapshots", "a" * 40, "big.bin")))
        with open(os.path.join(base, "refs", "main")) as f:
            self.assertEqual(f.read(), "a" * 40)

    def test_cli_rejects_an_unknown_model(self):
        self.assertEqual(fm.main(["--dest", self.dest, "--only", "piper"]), 2)


if __name__ == "__main__":
    unittest.main()
