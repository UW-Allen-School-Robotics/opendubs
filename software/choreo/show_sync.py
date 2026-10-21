#!/usr/bin/env python3
"""Put a show on a robot WITHOUT the repository: into the robot's own shows folder, which the deploy
never touches, and the routine palette lists beside the repository's shows.

    python3 show_sync.py push opendubs2 my-show.yaml --dry-run    # what WOULD happen, nothing moved
    python3 show_sync.py push opendubs2 my-show.yaml               # check it here, then put it there
    python3 show_sync.py push opendubs2 my-show.yaml --replace     # a different show already has its file
    python3 show_sync.py push opendubs2 my-show.yaml --dry-run --limits-from-robot
                                    # the whole pre-run check, against the ROBOT's joint limits (read-only)
    python3 show_sync.py status opendubs2                          # the robot's own shows
    python3 show_sync.py delete opendubs2 my-show                  # take one off the robot

WHY. The deploy rsyncs software/choreo/shows from the repository to /opt/opendubs/choreo/shows with
--delete, so a show copied there by hand vanishes at the next deploy. And some shows must never be in
the repository: one whose poses come from someone's photo, say. Those live in the robot's own folder,
/var/lib/opendubs/shows (runner.py LOCAL_SHOWS), beside the show library.

WHAT A PUSH CHECKS, here, before anything moves (the robot's runner checks every run again):
  * the show validates as the robot will see it — the self-collision stamp current and clear
    (sweep.py), against the arms' joint limits given here (--limits; the robot re-checks against its
    own file, which is the one that decides);
  * it has a `palette:` block (without one the Shows page does not list it);
  * everything it plays comes from the show library — a `music: file:` or a sound file beside the
    show would not travel with it — and the robot's library HAS each item (song, skins). A missing
    one is refused with the library_sync command that sends it;
  * its id is not a repository show's id on the robot (the palette would refuse both).
Only the YAML moves. It is written to <folder>/<id>.yaml atomically (a temp file, then a rename).

A HOST of the form `local:/some/folder` is a fake robot on this computer, as in library_sync.py: the
folder is the robot's shows folder, and --library and --repo-shows name its library and its
repository shows.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cue  # noqa: E402
import library_sync as LS  # noqa: E402

REMOTE_LOCAL_SHOWS = "/var/lib/opendubs/shows"     # runner.py LOCAL_SHOWS on the robot
REMOTE_LIMITS = "/etc/opendubs/arm-limits/joint_limits.yaml"   # the runner's OPENDUBS_ARM_LIMITS
# ... which is a bind mount that exists only INSIDE the runner's service (provision-runner.sh writes it
# as this drop-in). From a login shell the file is read where the drop-in says it really is.
RUNNER_LIMITS_DROPIN = "/etc/systemd/system/opendubs-runner.service.d/arm-limits.conf"


class ShowSyncError(Exception):
    pass


def _yaml():
    import yaml
    return yaml


def _run(t: LS.Target, cmd: str, stdin=None):
    return t.run(cmd, stdin)


def library_ids(show, tl: dict) -> list:
    """The library items a show plays: its song, every skin the face does not draw in code, and its videos."""
    ids = []
    music = tl.get("music") or {}
    if music.get("song"):
        ids.append(music["song"])
    for e in (tl.get("tracks") or {}).get("FACE", []):
        c = e.get("cue") or {}
        if c.get("do") == "skin" and c.get("name") and c["name"] not in cue.CODE_SKINS and c["name"] not in ids:
            ids.append(c["name"])
    for e in (tl.get("tracks") or {}).get("VIDEO", []):        # projector videos (kind `video`)
        v = (e.get("cue") or {}).get("video")
        if v and v not in ids:
            ids.append(v)
    return ids


def check_here(path: str, limits_path: str) -> tuple:
    """(show, timeline, problems): the show as the robot's runner will judge it, checked here."""
    show = cue.load_show(path)
    show.require_sweep = True
    limits = cue.load_limits(limits_path)
    tl = show.export(limits, limits_path)
    problems = [q["msg"] for q in tl["problems"]]
    if not isinstance(show.doc.get("palette"), dict):
        problems.append("it has no `palette:` block (e.g. palette: { label: \"My show\", order: 50 }) — "
                        "the Shows page lists only palette shows")
    m = show.doc.get("music")
    if isinstance(m, dict) and m.get("file"):
        problems.append(f"its music is a file beside it ({m['file']!r}), which does not travel with a show — "
                        "add it to the show library and name it with `music: song:`")
    if show.doc.get("sounds"):
        problems.append("it plays sound files beside it (`sounds:`), which do not travel with a show")
    return show, tl, problems


def _robot_library(t: LS.Target, lib_root: str) -> dict:
    p = _run(t, f"cat {shlex.quote(os.path.join(lib_root, 'index.yaml'))}")
    if p.returncode != 0:
        return {}
    items = _yaml().safe_load(p.stdout or b"") or []
    return {e["id"]: e for e in items if isinstance(e, dict) and e.get("id")}


def _repo_ids(t: LS.Target, repo_shows: str) -> dict:
    """{show id: file} for the repository's shows on the robot."""
    q = shlex.quote(repo_shows)
    p = _run(t, f"cd {q} 2>/dev/null && grep -H '^id:' *.yaml *.yml 2>/dev/null")
    out = {}
    for line in (p.stdout or b"").decode(errors="replace").splitlines():
        f, _, rest = line.partition(":id:")
        if rest.strip():
            out[rest.strip().strip("'\"")] = f
    return out


def _folder_ready(t: LS.Target, create: bool) -> None:
    q = shlex.quote(t.root)
    if _run(t, f"test -d {q} && test -w {q}").returncode == 0:
        return
    if not create:
        raise ShowSyncError(f"the robot's shows folder {t.root} on {t.host} is missing or not writable — "
                            "create it once with --create")
    cmd = (f"mkdir -p {q}" if t.local else
           f"sudo mkdir -p {q} && sudo chown $(id -un):$(id -gn) {q} && chmod 755 {q}")
    p = _run(t, cmd)
    if p.returncode != 0:
        raise ShowSyncError(f"could not create {t.root} on {t.host}: {p.stderr.decode(errors='replace').strip()}")


def _target(host: str, root: str) -> LS.Target:
    t = LS.Target(host, root, LS.REMOTE_PYTHON)
    if t.local and root == REMOTE_LOCAL_SHOWS:
        t.root = host[len("local:"):]
    return t


def robot_limits(t: LS.Target, remote_path: str, dropin: str = RUNNER_LIMITS_DROPIN) -> str:
    """The robot's own joint-limits file, read (only read) into a temporary file here: the one its
    runner validates against, so a show is judged by the robot's limits before it is sent.

    The runner's path is a bind mount visible only inside its service, so when it cannot be read
    from here the file is read where the runner's drop-in binds it from (BindReadOnlyPaths) — with
    `sudo -n cat` on a real robot, since that checkout may belong to another user. Read, never written."""
    import tempfile
    q = shlex.quote
    p = _run(t, f"cat {q(remote_path)}")
    if p.returncode != 0 or not p.stdout:
        d = _run(t, f"cat {q(dropin)}")
        src = None
        for line in (d.stdout or b"").decode(errors="replace").splitlines():
            if line.startswith("BindReadOnlyPaths="):
                spec = line.split("=", 1)[1].lstrip("-")
                if spec.split(":", 1)[-1].rstrip("/") == os.path.dirname(REMOTE_LIMITS):
                    src = spec.split(":", 1)[0]
        if src:
            sudo = "" if t.local else "sudo -n "
            p = _run(t, f"{sudo}cat {q(os.path.join(src, os.path.basename(REMOTE_LIMITS)))}")
    if p.returncode != 0 or not p.stdout:
        raise ShowSyncError(f"could not read the robot's joint limits {remote_path} on {t.host}: "
                            f"{p.stderr.decode(errors='replace').strip() or 'empty'}")
    fd, path = tempfile.mkstemp(prefix="robot-joint-limits-", suffix=".yaml")
    with os.fdopen(fd, "wb") as fh:
        fh.write(p.stdout)
    return path


def push(host: str, paths: list, root: str, limits_path: str, lib_root: str, repo_shows: str,
         dry: bool, replace: bool, create: bool, out=print, limits_from_robot: str | None = None,
         limits_dropin: str = RUNNER_LIMITS_DROPIN) -> int:
    t = _target(host, root)
    if limits_from_robot:
        limits_path = robot_limits(t, limits_from_robot, limits_dropin)
        out(f"  checking against the robot's joint limits ({limits_from_robot})")
    robot_lib = _robot_library(t, lib_root)
    repo = _repo_ids(t, repo_shows)
    failed = False
    for path in paths:
        name = os.path.basename(path)
        try:
            show, tl, problems = check_here(path, limits_path)
        except Exception as e:  # noqa: BLE001  a show that does not even load
            out(f"  REFUSED  {name}: {e}")
            failed = True
            continue
        missing = [i for i in library_ids(show, tl) if i not in robot_lib]
        if missing:
            problems.append(f"the robot's library lacks {', '.join(missing)} — send them first: "
                            f"python3 library_sync.py push {host} {' '.join(missing)}")
        if show.id in repo:
            problems.append(f"its id {show.id!r} is the repository show {repo[show.id]} on the robot — rename it")
        if problems:
            out(f"  REFUSED  {name} ({show.id}):")
            for q in problems:
                out(f"             - {q}")
            failed = True
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        dest = os.path.join(t.root, f"{show.id}.yaml")
        there = _run(t, f"cat {shlex.quote(dest)}")
        if there.returncode == 0 and there.stdout == data:
            out(f"  SKIP     {name}: the robot has it, identical")
            continue
        if there.returncode == 0 and not replace:
            out(f"  CONFLICT {name}: the robot has a DIFFERENT {show.id}.yaml — --replace to overwrite it")
            failed = True
            continue
        if dry:
            out(f"  WOULD    {name} -> {host}:{dest} ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:12]})")
            continue
        _folder_ready(t, create)
        tmp = os.path.join(t.root, f".{show.id}.yaml.tmp")
        p = _run(t, f"cat > {shlex.quote(tmp)} && mv {shlex.quote(tmp)} {shlex.quote(dest)}", data)
        if p.returncode != 0:
            out(f"  FAILED   {name}: {p.stderr.decode(errors='replace').strip()}")
            failed = True
            continue
        out(f"  SENT     {name} -> {host}:{dest} ({len(data)} bytes)")
    return 1 if failed else 0


def status(host: str, root: str, out=print) -> int:
    t = _target(host, root)
    q = shlex.quote(t.root)
    p = _run(t, f"cd {q} 2>/dev/null && for f in *.yaml; do [ -f \"$f\" ] && "
                f"printf '%s %s ' \"$f\" $(wc -c < \"$f\") && grep -m1 '^id:' \"$f\" || true; done")
    if p.returncode != 0 and not p.stdout:
        out(f"  no shows folder at {t.root} on {host}")
        return 0
    lines = (p.stdout or b"").decode(errors="replace").split("\n")
    shown = [x for x in lines if x.strip()]
    out(f"  {len(shown)} show(s) in {t.root} on {host}")
    for x in shown:
        out(f"    {x}")
    return 0


def delete(host: str, root: str, show_id: str, out=print) -> int:
    t = _target(host, root)
    dest = os.path.join(t.root, f"{show_id}.yaml")
    p = _run(t, f"test -f {shlex.quote(dest)} && rm {shlex.quote(dest)}")
    if p.returncode != 0:
        out(f"  REFUSED  {show_id}: not in {t.root} on {host}")
        return 1
    out(f"  DELETED  {show_id} from {host}:{t.root}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("push")
    ps.add_argument("host")
    ps.add_argument("shows", nargs="+")
    ps.add_argument("--dry-run", action="store_true")
    ps.add_argument("--replace", action="store_true")
    ps.add_argument("--create", action="store_true", help="create the robot's shows folder if missing (sudo)")
    ps.add_argument("--limits-from-robot", nargs="?", const=REMOTE_LIMITS, default=None, metavar="PATH",
                    help=f"validate against the robot's own joint limits, read over ssh (default {REMOTE_LIMITS}, "
                         "found through the runner's drop-in when that path is the service's own bind mount)")
    ps.add_argument("--limits-dropin", default=RUNNER_LIMITS_DROPIN, help=argparse.SUPPRESS)
    ps.add_argument("--limits", default=cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS)
                    else os.path.join(HERE, "tests", "joint_limits_fixture.yaml"))
    for p_ in (ps, sub.add_parser("status"), sub.add_parser("delete")):
        if p_.prog.endswith("push"):
            p_.add_argument("--library", default=LS.REMOTE_ROOT, help="the robot's show library")
            p_.add_argument("--repo-shows", default=LS.REMOTE_SHOWS, help="the robot's repository shows")
        else:
            p_.add_argument("host")
            if p_.prog.endswith("delete"):
                p_.add_argument("id")
        p_.add_argument("--root", default=REMOTE_LOCAL_SHOWS, help="the robot's own shows folder")
    a = ap.parse_args()
    try:
        if a.cmd == "push":
            return push(a.host, a.shows, a.root, a.limits, a.library, a.repo_shows, a.dry_run, a.replace, a.create,
                        limits_from_robot=a.limits_from_robot, limits_dropin=a.limits_dropin)
        if a.cmd == "status":
            return status(a.host, a.root)
        return delete(a.host, a.root, a.id)
    except (ShowSyncError, LS.SyncError) as e:
        print(f"refused: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
