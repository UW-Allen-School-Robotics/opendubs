#!/usr/bin/env python3
"""The studio launcher (studio/studio.py) and its wheel builder: ports, settings, stopping exactly
what it started, and what the wheel may carry. No composer, no MuJoCo window: CI's studio workflow
runs the whole app."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "studio"))

import build_wheel  # noqa: E402
import studio  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


home = tempfile.mkdtemp(prefix="studio-test-")
os.environ["OPENDUBS_STUDIO_HOME"] = os.path.join(home, "studio")
for k in ("OPENDUBS_STUDIO_PORT", "OPENDUBS_STUDIO_SHOWS", "OPENDUBS_LIBRARY", "OPENDUBS_FACE_WEB_PORTS",
          "OPENDUBS_STUDIO_HEAD", "OPENDUBS_STUDIO_LIMITS"):
    os.environ.pop(k, None)

print("ports: never one in use")
tcp = socket.socket()
tcp.bind(("127.0.0.1", 0))
tcp.listen()
busy = tcp.getsockname()[1]
p = studio.pick(busy)
check("a TCP port something listens on is not picked; another is", p != busy and studio.free(p), (busy, p))
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("127.0.0.1", 0))
ubusy = udp.getsockname()[1]
check("... nor a bound UDP port", studio.pick(ubusy, socket.SOCK_DGRAM) != ubusy)
fresh = studio.pick(0)
check("a free port is used as asked", studio.pick(fresh) == fresh)
check("... and never one already handed out", studio.pick(fresh, taken={fresh}) != fresh)
tcp.close(), udp.close()

print("\nsettings and folders")
fake_home = Path(home) / "user"
fake_home.mkdir()
os.environ["HOME"] = os.environ["USERPROFILE"] = str(fake_home)
s = studio.load_settings()
check("the defaults: composer 8091, the tablet face, the galaxy tablet head",
      s["composer_port"] == 8091 and s["head"] == "tablet" and s["body_head"] == "galaxy_tablet", s)
sd = studio.shows_dir(s)
check("no ~/opendubs-shows: a shows folder in the studio's data folder, filled with the example shows",
      sd == Path(home, "studio", "data", "shows") and any(sd.glob("*.yaml")), (sd, list(sd.glob("*"))[:3]))
(fake_home / "opendubs-shows").mkdir()
check("... ~/opendubs-shows when it exists", studio.shows_dir(s) == fake_home / "opendubs-shows")
cfg = Path(home, "studio", "config")
cfg.mkdir(parents=True)
(cfg / "studio.toml").write_text('composer_port = 9191\nshows_dir = "~/elsewhere"\nnot_a_setting = 1\n')
s = studio.load_settings()
check("studio.toml sets what it names, and nothing it does not know",
      s["composer_port"] == 9191 and "not_a_setting" not in s and studio.shows_dir(s) == fake_home / "elsewhere", s)
os.environ["OPENDUBS_STUDIO_PORT"], os.environ["OPENDUBS_FACE_WEB_PORTS"] = "9292", "1,2,3,4"
s = studio.load_settings()
check("environment variables win over the file", s["composer_port"] == 9292 and s["door_ports"] == [1, 2, 3, 4], s)
del os.environ["OPENDUBS_STUDIO_PORT"], os.environ["OPENDUBS_FACE_WEB_PORTS"]
check("no robot's limits saved: the TEST fixture (None)", studio.limits_file(s) is None)

print("\nprocesses: stop exactly what was started, children included")
logdir = Path(home, "logs")
code = ("import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
c = studio.spawn("t", [sys.executable, "-c", code], dict(os.environ), Path(home), logdir)
time.sleep(1.0)
import psutil  # noqa: E402
kids = psutil.Process(c["pid"]).children(recursive=True)
check("a component runs, with its child", studio.alive(c) and len(kids) == 1, kids)
check("a recorded pid with another start time is NOT ours (a reused pid is never stopped)",
      not studio.alive({**c, "started": c["started"] - 100}))
check("stop ends it ...", studio.stop(c))
time.sleep(0.5)
check("... and its child too", not studio.alive(c) and not any(k.is_running() and k.status() != psutil.STATUS_ZOMBIE
                                                              for k in kids), kids)
check("stopping it again says it was not running", studio.stop(c) is False)

print("\nthe wheel: what it may carry")
files = build_wheel.tracked()
check("the composer, the bridge, the twin, the face page, sim and the description are in",
      all(any(f.startswith(p) for f in files) for p in ("software/choreo/composer/", "software/choreo/face_bridge.py",
                                                        "software/choreo/twin_view.py", "software/face-web/door_dev.py",
                                                        "sim/scene.py", "description/openarm/")), len(files))
check("no tests, no deploy files, no vendored MediaPipe, no face-web test photo",
      not [f for f in files if f.startswith(build_wheel.EXCLUDE) and f not in build_wheel.KEEP],
      [f for f in files if f.startswith(build_wheel.EXCLUDE)][:5])
check("... but the arm limits fixture the composer falls back to", "software/choreo/tests/joint_limits_fixture.yaml" in files)
untracked = subprocess.run(["git", "-C", str(build_wheel.REPO), "ls-files", "--others", "--", *build_wheel.SUBTREES],
                           capture_output=True, text=True).stdout.split()
check("nothing git does not track, even when it lies in the checkout", not set(untracked) & set(files),
      sorted(set(untracked) & set(files))[:5])

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
