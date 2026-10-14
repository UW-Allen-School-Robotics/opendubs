# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "numpy", "mujoco", "glfw", "websockets", "psutil", "platformdirs", "pillow"]
# ///
"""The choreography studio: the composer, the face twin and the MuJoCo body twin, as one app.

    opendubs-studio up            # start all three, open the composer and the face in the browser
    opendubs-studio status        # what runs, where (--json for scripts)
    opendubs-studio open          # open the pages again
    opendubs-studio down          # stop what `up` started, and nothing else
    opendubs-studio paths         # the folders and settings it uses
    opendubs-studio limits --from-robot opendubs2   # use that robot's arm limits from now on
    opendubs-studio selftest      # render one off-screen frame of the twin (CI uses this)

Installed from a release wheel, or run from a checkout without installing:

    uv run software/choreo/studio/studio.py up

WHAT RUNS. The composer (composer/server.py), the face page and its door (software/face-web,
door_dev.py) with the bridge that plays the composer's FACE track to it (face_bridge.py), and the
MuJoCo window (twin_view.py, under `mjpython` on macOS). Each binds 127.0.0.1 only and none of
them can move a robot. Each runs in its own process group, recorded with its start time in the
state folder: `down` stops exactly those processes and their children, never anything found by
name or port, because other sessions may run their own twins on the same machine.

PORTS. The usual ones (composer 8091; face page 8765/8766; face and neck 4244/4245) when they are
free; otherwise the system picks free ones, and `status` says which. A busy port is never taken over.

FOLDERS. Shows: $OPENDUBS_STUDIO_SHOWS, else `shows_dir` in the settings file, else ~/opendubs-shows
when it exists, else a `shows` folder in the platform's data folder, first filled with the example
shows. The library: $OPENDUBS_LIBRARY, else `library_dir`, else ~/opendubs-library when it exists,
else `library` in the data folder. Neither is ever inside the app or the repository. Settings live
in studio.toml in the platform's config folder (`paths` prints it).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = "opendubs-studio"
DEFAULTS = {
    "composer_port": 8091,
    "door_ports": [8765, 8766, 4244, 4245],     # face page http, its websocket, the face, the neck
    "head": "tablet",                            # the face twin: tablet (the web face) or none
    "body_head": "galaxy_tablet",                # the head the MuJoCo twin draws
    "open_browser": True,
    "shows_dir": None,
    "library_dir": None,
    "limits": None,                              # the arm limits file; none: the test fixture
}
READY_TIMEOUT_S = 40.0


# ---- where things are ----------------------------------------------------------------------------
def tree_root() -> Path:
    """The code the studio runs: the copy inside an installed wheel, else this repository."""
    for t in (HERE / "tree", HERE.parents[2] if len(HERE.parents) > 2 else HERE):
        if (t / "software" / "choreo" / "composer" / "server.py").is_file():
            return t
    sys.exit(f"{APP}: cannot find the composer next to {HERE} — a broken install?")


def dirs():
    """The platform's own folders, or all under $OPENDUBS_STUDIO_HOME (tests, CI, a second studio)."""
    if os.environ.get("OPENDUBS_STUDIO_HOME"):
        h = Path(os.environ["OPENDUBS_STUDIO_HOME"]).expanduser()
        return {k: h / k for k in ("config", "data", "state", "log")}
    import platformdirs
    return {"config": Path(platformdirs.user_config_dir(APP, appauthor=False)),
            "data": Path(platformdirs.user_data_dir(APP, appauthor=False)),
            "state": Path(platformdirs.user_state_dir(APP, appauthor=False)),
            "log": Path(platformdirs.user_log_dir(APP, appauthor=False))}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    f = dirs()["config"] / "studio.toml"
    if f.is_file():
        import tomllib
        with open(f, "rb") as fh:
            s.update({k: v for k, v in tomllib.load(fh).items() if k in DEFAULTS})
    env = {"composer_port": ("OPENDUBS_STUDIO_PORT", int), "head": ("OPENDUBS_STUDIO_HEAD", str),
           "body_head": ("OPENDUBS_STUDIO_BODY_HEAD", str), "shows_dir": ("OPENDUBS_STUDIO_SHOWS", str),
           "library_dir": ("OPENDUBS_LIBRARY", str), "limits": ("OPENDUBS_STUDIO_LIMITS", str)}
    for k, (var, typ) in env.items():
        if os.environ.get(var):
            s[k] = typ(os.environ[var])
    if os.environ.get("OPENDUBS_FACE_WEB_PORTS"):
        s["door_ports"] = [int(x) for x in os.environ["OPENDUBS_FACE_WEB_PORTS"].split(",")]
    if os.environ.get("OPENDUBS_NO_BROWSER"):
        s["open_browser"] = False
    return s


def shows_dir(s: dict) -> Path:
    if s["shows_dir"]:
        return Path(s["shows_dir"]).expanduser()
    home = Path.home() / "opendubs-shows"
    if home.is_dir():
        return home
    d = dirs()["data"] / "shows"
    if not d.exists():                           # first run: the example shows, to start from
        shutil.copytree(tree_root() / "software" / "choreo" / "shows", d,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return d


def library_dir(s: dict) -> Path:
    if s["library_dir"]:
        return Path(s["library_dir"]).expanduser()
    home = Path.home() / "opendubs-library"
    if home.is_dir():
        return home
    d = dirs()["data"] / "library"
    d.mkdir(parents=True, exist_ok=True)
    return d


def limits_file(s: dict) -> Path | None:
    if s["limits"]:
        return Path(s["limits"]).expanduser()
    saved = dirs()["data"] / "joint_limits.yaml"     # `limits --from-robot`
    return saved if saved.is_file() else None


# ---- ports ---------------------------------------------------------------------------------------
def free(port: int, kind=socket.SOCK_STREAM) -> bool:
    """Nothing listens there. On macOS and Linux a TCP port a stopped server left in TIME_WAIT counts
    as free (its next server binds with SO_REUSEADDR, as these do), while a live listener still
    blocks the bind; on Windows SO_REUSEADDR would let a bind steal a port, so it is never set."""
    with socket.socket(socket.AF_INET, kind) as sk:
        if kind == socket.SOCK_STREAM and os.name != "nt":
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick(port: int, kind=socket.SOCK_STREAM, taken=()) -> int:
    """`port` when it is free, else one the system picks — never a port in use."""
    if port and port not in taken and free(port, kind):
        return port
    while True:
        with socket.socket(socket.AF_INET, kind) as sk:
            sk.bind(("127.0.0.1", 0))
            p = sk.getsockname()[1]
        if p not in taken:
            return p


# ---- processes -----------------------------------------------------------------------------------
def state_file() -> Path:
    return dirs()["state"] / "state.json"


def read_state() -> dict | None:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return None


def alive(c: dict) -> bool:
    import psutil
    try:
        p = psutil.Process(c["pid"])
        return abs(p.create_time() - c["started"]) < 1.0 and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, KeyError, TypeError):
        return False


def spawn(name: str, argv: list, env: dict, cwd: Path, logdir: Path) -> dict:
    import psutil
    logdir.mkdir(parents=True, exist_ok=True)
    log = open(logdir / f"{name}.log", "ab")
    kw = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "cwd": str(cwd), "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True          # its own group: a Ctrl-C here never reaches it
    p = subprocess.Popen([str(a) for a in argv], **kw)
    return {"pid": p.pid, "started": psutil.Process(p.pid).create_time(), "log": str(logdir / f"{name}.log")}


def stop(c: dict, timeout=5.0) -> bool:
    """Stop one recorded component and every process it started. False if it was not running."""
    import psutil
    if not alive(c):
        return False
    root = psutil.Process(c["pid"])
    procs = [root] + root.children(recursive=True)
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, left = psutil.wait_procs(procs, timeout=timeout)
    for p in left:
        try:
            p.kill()
        except psutil.Error:
            pass
    return True


def tail(path: str, n=8) -> str:
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def wait_http(url: str, comp: dict, timeout=READY_TIMEOUT_S) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return True
        except OSError:
            pass
        if not alive(comp):
            return False
        time.sleep(0.3)
    return False


def mujoco_python() -> str:
    """macOS: a script-driven MuJoCo window must run under mjpython (the mujoco package's own)."""
    if platform.system() != "Darwin":
        return sys.executable
    for cand in (Path(sys.executable).with_name("mjpython"), shutil.which("mjpython")):
        if cand and Path(cand).exists():
            return str(cand)
    return sys.executable


# ---- commands ------------------------------------------------------------------------------------
def cmd_up(a) -> int:
    st = read_state()
    if st and any(alive(c) for c in st["components"].values()):
        print(f"{APP}: already running — `{APP} status`, or `{APP} down` first", file=sys.stderr)
        return 1
    s, d, tree = load_settings(), dirs(), tree_root()
    choreo, face_web = tree / "software" / "choreo", tree / "software" / "face-web"
    shows, lib, lim = shows_dir(s), library_dir(s), limits_file(s)
    env = {**os.environ, "OPENDUBS_LIBRARY": str(lib), "PYTHONUNBUFFERED": "1",
           "OPENDUBS_FACE_FRAMES": str(d["state"] / "face-frames.bin")}
    d["state"].mkdir(parents=True, exist_ok=True)
    py, comps, ports = sys.executable, {}, {}

    def save():
        state_file().write_text(json.dumps({"components": comps, "ports": ports, "urls": urls,
                                            "tree": str(tree), "shows": str(shows), "library": str(lib)}, indent=1))

    def fail(name, why) -> int:
        print(f"{APP}: {name} {why} — its log:\n{tail(comps[name]['log'])}", file=sys.stderr)
        for c in comps.values():
            stop(c)
        state_file().unlink(missing_ok=True)
        return 1

    urls = {}
    ports["composer"] = pick(s["composer_port"])
    comp_argv = [py, choreo / "composer" / "server.py", "--port", ports["composer"], "--shows", shows]
    if lim:
        comp_argv += ["--limits", lim]
    comps["composer"] = spawn("composer", comp_argv, env, choreo / "composer", d["log"])
    urls["composer"] = f"http://127.0.0.1:{ports['composer']}/"
    save()
    if not wait_http(urls["composer"] + "api/twin", comps["composer"]):
        return fail("composer", "did not start")

    if s["head"] == "tablet":
        taken = {ports["composer"]}
        http, ws = (pick(p, taken=taken) for p in s["door_ports"][:2])
        taken |= {http, ws}
        face = pick(s["door_ports"][2], socket.SOCK_DGRAM, taken)
        neck = pick(s["door_ports"][3], socket.SOCK_DGRAM, taken | {face})
        ports.update(face_http=http, face_ws=ws, face=face, neck=neck)
        comps["face_door"] = spawn("face_door", [py, face_web / "door_dev.py", "--http", http, "--ws", ws,
                                                 "--face", face, "--neck", neck], env, face_web, d["log"])
        urls["face"] = f"http://127.0.0.1:{http}/?door=ws://127.0.0.1:{ws}&hud=1"
        save()
        if not wait_http(f"http://127.0.0.1:{http}/", comps["face_door"]):
            return fail("face_door", "did not start")
        comps["face_bridge"] = spawn("face_bridge", [py, choreo / "face_bridge.py", "--composer", urls["composer"],
                                                     "--face", f"127.0.0.1:{face}", "--neck", f"127.0.0.1:{neck}"],
                                     env, choreo, d["log"])
    if not a.headless:
        comps["body_twin"] = spawn("body_twin", [mujoco_python(), choreo / "twin_view.py", "--composer",
                                                 urls["composer"], "--head", s["body_head"]], env, choreo, d["log"])
    save()
    time.sleep(2.0)
    for name, c in comps.items():
        if not alive(c):
            return fail(name, "stopped at once")
    print(f"{APP}: up — composer {urls['composer']}" + (f" · face {urls['face']}" if "face" in urls else "")
          + ("" if a.headless else " · the MuJoCo window") + f"\n  shows: {shows}\n  library: {lib}"
          + f"\n  arm limits: {lim or 'the TEST fixture (`limits --from-robot HOST` for a robot)'}"
          + f"\n  logs: {d['log']}\n  stop: {APP} down")
    if s["open_browser"] and not a.no_browser and not a.headless:
        cmd_open(a)
    return 0


def cmd_down(a) -> int:
    st = read_state()
    if not st:
        print(f"{APP}: nothing of ours is running")
        return 0
    for name in ("body_twin", "face_bridge", "face_door", "composer"):
        c = st["components"].get(name)
        if c and stop(c):
            print(f"{APP}: stopped {name}")
    state_file().unlink(missing_ok=True)
    return 0


def cmd_status(a) -> int:
    st = read_state()
    out = {"running": {}, "urls": {}, "ports": {}}
    if st:
        out = {"running": {n: alive(c) for n, c in st["components"].items()}, "urls": st["urls"],
               "ports": st["ports"], "shows": st.get("shows"), "library": st.get("library")}
    if a.json:
        print(json.dumps(out, indent=1))
    elif not st:
        print(f"{APP}: not running")
    else:
        for n, ok in out["running"].items():
            print(f"  {n:12s} {'running' if ok else 'STOPPED'}")
        for n, u in out["urls"].items():
            print(f"  {n:12s} {u}")
    return 0 if st and all(out["running"].values()) else 1


def cmd_open(a) -> int:
    st = read_state()
    if not st:
        print(f"{APP}: not running — `{APP} up`", file=sys.stderr)
        return 1
    for u in st["urls"].values():
        webbrowser.open(u)
    return 0


def cmd_paths(a) -> int:
    s, d = load_settings(), dirs()
    print(json.dumps({"code": str(tree_root()), "settings_file": str(d["config"] / "studio.toml"),
                      "shows": str(shows_dir(s)), "library": str(library_dir(s)),
                      "arm_limits": str(limits_file(s) or "TEST fixture"), "logs": str(d["log"]),
                      "state": str(d["state"]), "settings": s}, indent=1))
    return 0


def cmd_limits(a) -> int:
    """Read a robot's arm limits over ssh (the OpenSSH client every platform has), keep them."""
    out = dirs()["data"] / "joint_limits.yaml"
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", a.from_robot, "cat", a.path], capture_output=True)
    if r.returncode or not r.stdout.strip():
        print(f"{APP}: could not read {a.path} on {a.from_robot}: {r.stderr.decode(errors='replace').strip()}",
              file=sys.stderr)
        return 1
    import yaml
    yaml.safe_load(r.stdout)                         # refuse what is not YAML before keeping it
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.stdout)
    print(f"{APP}: {a.from_robot}'s arm limits saved to {out}; `{APP} up` uses them from now on")
    return 0


def cmd_selftest(a) -> int:
    """One off-screen frame of the whole robot (the twin's own model), checked for content."""
    tree = tree_root()
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "frame.png"
        r = subprocess.run([sys.executable, tree / "sim" / "scene.py", "--render", png], capture_output=True,
                           cwd=tree / "sim", timeout=180)
        if r.returncode or not png.is_file() or png.stat().st_size < 2000:
            print(f"{APP}: selftest FAILED — no frame:\n{r.stdout.decode(errors='replace')[-800:]}"
                  f"{r.stderr.decode(errors='replace')[-1500:]}", file=sys.stderr)
            return 1
        print(f"{APP}: selftest ok — the twin rendered a {png.stat().st_size} B frame")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=APP, description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="start the composer, the face twin and the MuJoCo twin")
    up.add_argument("--headless", action="store_true", help="no MuJoCo window, no browser (CI)")
    up.add_argument("--no-browser", action="store_true")
    sub.add_parser("down", help="stop what `up` started")
    stp = sub.add_parser("status", help="what runs, and where")
    stp.add_argument("--json", action="store_true")
    sub.add_parser("open", help="open the composer and the face page")
    sub.add_parser("paths", help="the folders and settings in use")
    lim = sub.add_parser("limits", help="use a robot's arm joint limits")
    lim.add_argument("--from-robot", required=True, metavar="HOST")
    lim.add_argument("--path", default="/etc/opendubs/arm-limits/joint_limits.yaml")
    sub.add_parser("selftest", help="render one off-screen frame of the twin")
    a = ap.parse_args(argv)
    return {"up": cmd_up, "down": cmd_down, "status": cmd_status, "open": cmd_open, "paths": cmd_paths,
            "limits": cmd_limits, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
