#!/usr/bin/env python3
"""Push show-library items (songs, clips, skins) from this computer to a robot's library, over ssh.

    python3 library_sync.py status opendubs2                      # what the robot has, each item verified
    python3 library_sync.py push opendubs2 my-song --dry-run      # what WOULD move, nothing moved
    python3 library_sync.py push opendubs2 my-song other-skin     # push these
    python3 library_sync.py push opendubs2 --all                  # every item here
    python3 library_sync.py push opendubs2 my-song --replace      # an item the robot has with OTHER content
    python3 library_sync.py delete opendubs2 old-song             # refused while a show there names it

THE LIBRARY (library.py): files outside the repository plus an index, on this computer (--library,
else $OPENDUBS_LIBRARY, else /var/lib/opendubs/library) and on the robot (--root, default
/var/lib/opendubs/library). The robot's library is the source of truth that the show validator,
the runner and the tablet (plan 5.3) read. Nothing here is ever committed — this tool and its
tests are; library contents never.

WHAT A PUSH DOES, for each chosen item:
  * checks it HERE first: a complete entry (source and licence required — an item without them is
    refused, not sent) whose file still has the hash the entry records;
  * compares with the robot's index BY CONTENT (sha256): an item the robot already has, identical
    and intact on its disk, is skipped; one it has under the same id with different content is a
    conflict, refused unless --replace; a file path another id already uses is refused;
  * sends the rest as one tar stream over ssh into a staging folder on the robot, then the robot
    re-hashes every staged item and adds only those whose hash and size match, under a lock, with
    the index replaced in one step. An item that arrived damaged is refused and reported.
--dry-run stops after the comparison and reports what would move.

DELETING happens only by `delete`, never as a side effect of a push, and is refused while any show
in the robot's shows folder (--shows, default /opt/opendubs/choreo/shows) still names the item.

TRANSPORT: ssh to the robot, as the deploy uses (deploy-all.sh) — no new open port, no service. The
tool copies itself (this file and library.py) into <root>/.tool/ on the robot and runs there with
the robot's python3, so both ends always run the same code. The library folder must exist and be
writable by the ssh user: `--create` makes it the way the deploy makes /opt/opendubs (sudo mkdir,
then chown to the ssh user); the robot's services read it as another user, so files are world-readable.

A HOST of the form `local:/some/folder` is a fake robot on this computer — for tests and rehearsal.
"""
from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import library  # noqa: E402

REMOTE_ROOT = "/var/lib/opendubs/library"
REMOTE_SHOWS = "/opt/opendubs/choreo/shows"
REMOTE_PYTHON = "/usr/bin/python3"
TOOL_DIR = ".tool"
STAGING = ".incoming"


# ---- the robot end: run THERE, as `library_sync.py remote <op>`, reading/writing JSON ------------

def _remote_state(root: str, verify: list) -> dict:
    exists = os.path.isdir(root)
    writable = exists and os.access(root, os.W_OK)
    idx = library.load_index(root) or {} if exists else {}
    checked = {}
    for item_id in verify:
        e = idx.get(item_id)
        if e is None:
            continue
        f = os.path.join(root, e.get("file", ""))
        checked[item_id] = ("missing" if not os.path.exists(f)
                            else "intact" if library.item_hash(f) == e.get("sha256") else "changed")
    return {"exists": exists, "writable": writable, "index": list(idx.values()), "verified": checked}


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    """Extract only regular files and folders, only inside `dest`."""
    real = os.path.realpath(dest)
    for m in tar.getmembers():
        p = os.path.realpath(os.path.join(dest, m.name))
        if not (p == real or p.startswith(real + os.sep)) or not (m.isfile() or m.isdir()):
            raise ValueError(f"refused a tar member outside the staging folder or not a plain file: {m.name!r}")
    for m in tar.getmembers():
        tar.extract(m, dest)
        os.chmod(os.path.join(dest, m.name), 0o755 if m.isdir() else 0o644)


def _remote_stage(root: str, run: str) -> dict:
    dest = os.path.join(root, STAGING, run)
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tar:
        # a stream: collect members as they arrive, then check them all before writing any
        buf = tempfile.TemporaryFile()
        out = tarfile.open(fileobj=buf, mode="w")
        for m in tar:
            out.addfile(m, tar.extractfile(m) if m.isfile() else None)
        out.close()
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as t:
        _safe_extract(t, dest)
    return {"staged": run}


def _remote_commit(root: str, run: str, entries: list, replace: bool) -> dict:
    staged = os.path.join(root, STAGING, run)
    report = []
    with open(os.path.join(root, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)              # one push at a time; readers never need it
        idx = library.load_index(root) or {}
        by_file = {e.get("file"): i for i, e in idx.items()}
        for e in entries:
            item_id, rel = e["id"], e["file"]
            src = os.path.join(staged, rel)
            why = None
            if not os.path.exists(src):
                why = "did not arrive"
            elif library.item_hash(src) != e["sha256"]:
                why = "arrived damaged: its hash differs from the sender's — nothing written"
            elif library.item_bytes(src) != e["bytes"]:
                why = "arrived with the wrong size — nothing written"
            elif library.entry_problems(e):
                why = "; ".join(library.entry_problems(e))
            elif by_file.get(rel) not in (None, item_id):
                why = f"its file {rel} belongs to {by_file[rel]!r} here"
            old = idx.get(item_id)
            if why is None and old is not None and old.get("sha256") != e["sha256"] and not replace:
                why = "the robot has other content under this id (use --replace)"
            if why:
                report.append({"id": item_id, "action": "refused", "why": why})
                continue
            dest = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if old is not None and old.get("file") != rel:
                _remove_path(os.path.join(root, old["file"]), root)
            _remove_path(dest, root)
            os.replace(src, dest)
            idx[item_id] = e
            by_file[rel] = item_id
            report.append({"id": item_id, "action": "replaced" if old else "added", "bytes": e["bytes"]})
        library._write_index(root, list(idx.values()))
    shutil.rmtree(staged, ignore_errors=True)
    return {"report": report}


def _remove_path(p: str, root: str) -> None:
    real = os.path.realpath(p)
    if real.startswith(os.path.realpath(root) + os.sep) and os.path.exists(real):
        (shutil.rmtree if os.path.isdir(real) else os.remove)(real)


def _remote_delete(root: str, shows: str, ids: list, dry: bool) -> dict:
    report = []
    with open(os.path.join(root, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        idx = library.load_index(root) or {}
        for item_id in ids:
            used = library.shows_using(item_id, shows) if os.path.isdir(shows) else []
            if item_id not in idx:
                report.append({"id": item_id, "action": "refused", "why": "not in the robot's library"})
            elif used:
                report.append({"id": item_id, "action": "refused",
                               "why": f"still named by {', '.join(used)} on the robot"})
            elif dry:
                report.append({"id": item_id, "action": "would delete"})
            else:
                library.remove(item_id, root)
                report.append({"id": item_id, "action": "deleted"})
    return {"report": report}


def remote_main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="library_sync.py remote")
    ap.add_argument("op", choices=("state", "stage", "commit", "delete", "migrate"))
    ap.add_argument("--root", required=True)
    ap.add_argument("--run")
    ap.add_argument("--verify", nargs="*", default=[])
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--shows")
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.op == "state":
            out = _remote_state(a.root, a.verify)
        elif a.op == "stage":
            out = _remote_stage(a.root, a.run)
        elif a.op == "commit":
            out = _remote_commit(a.root, a.run, json.load(sys.stdin), a.replace)
        elif a.op == "delete":
            out = _remote_delete(a.root, a.shows, a.ids, a.dry_run)
        else:
            out = {"migrated": library.migrate(a.root)}
    except Exception as e:  # noqa: BLE001 — the sender reports it; a traceback here helps no one there
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out))
    return 0 if "error" not in out else 1


# ---- the sending end ------------------------------------------------------------------------------

class Target:
    """Where the robot's library is: `host` over ssh, or `local:/folder` (a fake robot)."""

    def __init__(self, host: str, root: str, python: str):
        self.local = host.startswith("local:")
        self.host = host
        self.root = host[len("local:"):] if self.local and root == REMOTE_ROOT else root
        self.python = sys.executable if self.local and python == REMOTE_PYTHON else python

    def run(self, cmd: str, stdin=None) -> subprocess.CompletedProcess:
        argv = ["sh", "-c", cmd] if self.local else ["ssh", "-o", "BatchMode=yes", self.host, cmd]
        return subprocess.run(argv, input=stdin, capture_output=True)

    def tool(self, op: str, *args, stdin=None) -> dict:
        q = shlex.quote
        cmd = " ".join([q(self.python), q(os.path.join(self.root, TOOL_DIR, "library_sync.py")), "remote", op,
                        "--root", q(self.root)] + [q(x) for x in args])
        p = self.run(cmd, stdin)
        try:
            out = json.loads(p.stdout or b"{}")
        except json.JSONDecodeError:
            out = {}
        if p.returncode != 0 or "error" in out:
            raise SyncError(out.get("error") or (p.stderr.decode(errors="replace").strip()
                                                 or f"{op} failed on {self.host} (exit {p.returncode})"))
        return out

    def install_tool(self) -> None:
        """This file and library.py, into <root>/.tool/: both ends run the same code."""
        q = shlex.quote
        d = os.path.join(self.root, TOOL_DIR)
        for name in ("library.py", "library_sync.py"):
            with open(os.path.join(HERE, name), "rb") as fh:
                p = self.run(f"mkdir -p {q(d)} && cat > {q(os.path.join(d, name))}", fh.read())
            if p.returncode != 0:
                raise SyncError(f"could not copy the sync tool to {self.host}: "
                                f"{p.stderr.decode(errors='replace').strip()}")


class SyncError(Exception):
    pass


def _root_ready(t: Target, create: bool) -> None:
    q = shlex.quote(t.root)
    p = t.run(f"test -d {q} && test -w {q}")
    if p.returncode == 0:
        return
    if not create:
        how = (f"mkdir -p {t.root}" if t.local else
               f"ssh {t.host} 'sudo mkdir -p {t.root} && sudo chown $(id -un):$(id -gn) {t.root}'")
        raise SyncError(f"the library folder {t.root} on {t.host} is missing or not writable — create it "
                        f"once with --create, or: {how}")
    cmd = (f"mkdir -p {q}" if t.local else
           f"sudo mkdir -p {q} && sudo chown $(id -un):$(id -gn) {q} && chmod 755 {q}")
    p = t.run(cmd)
    if p.returncode != 0:
        raise SyncError(f"could not create {t.root} on {t.host}: {p.stderr.decode(errors='replace').strip()}")


def _mb(n) -> str:
    return f"{(n or 0) / 1048576:.1f} MB"


def plan_push(local_root: str, ids: list, remote: dict, replace: bool) -> list:
    """One row per chosen item: {id, kind, bytes, action: send|skip|refused|conflict, why, entry}."""
    idx = library.load_index(local_root) or {}
    remote_idx = {e["id"]: e for e in remote["index"]}
    remote_files = {e.get("file"): e["id"] for e in remote["index"]}
    rows = []
    for item_id in ids:
        e = idx.get(item_id)
        if e is None:
            rows.append({"id": item_id, "action": "refused", "why": "not in this computer's library"})
            continue
        f = os.path.join(local_root, e.get("file", ""))
        entry = dict(e)
        if os.path.exists(f):
            entry["bytes"] = library.item_bytes(f)       # the size travels with every entry
        row = {"id": item_id, "kind": e.get("kind"), "bytes": entry.get("bytes"), "entry": entry}
        problems = library.entry_problems(entry)
        if not os.path.exists(f):
            row.update(action="refused", why=f"its file {e.get('file')} is missing here")
        elif library.item_hash(f) != e.get("sha256"):
            row.update(action="refused", why="its file has changed since it was added here (hash differs) — "
                                             "add it again, then push")
        elif problems:
            row.update(action="refused", why="; ".join(problems))
        elif item_id in remote_idx and remote_idx[item_id].get("sha256") == e["sha256"] \
                and remote["verified"].get(item_id) == "intact":
            row.update(action="skip", why="the robot has it, identical")
        elif item_id in remote_idx and remote_idx[item_id].get("sha256") != e["sha256"] and not replace:
            row.update(action="conflict", why="the robot has OTHER content under this id — --replace to "
                                              "overwrite it (shows there use the new content from then on)")
        elif remote_files.get(e["file"]) not in (None, item_id):
            row.update(action="conflict", why=f"its file {e['file']} belongs to {remote_files[e['file']]!r} "
                                              "on the robot")
        else:
            why = ("replacing the robot's different content" if item_id in remote_idx and
                   remote_idx[item_id].get("sha256") != e["sha256"] else
                   "the robot's copy is missing or damaged" if item_id in remote_idx else "new on the robot")
            row.update(action="send", why=why)
        rows.append(row)
    return rows


def push(host: str, ids: list, everything: bool, local_root: str, root: str, python: str,
         dry: bool, replace: bool, create: bool, out=print) -> int:
    local_idx = library.load_index(local_root)
    if local_idx is None:
        raise SyncError(f"no library on this computer at {local_root}")
    ids = sorted(local_idx) if everything else ids
    if not ids:
        raise SyncError("name the items to push, or --all")
    t = Target(host, root, python)
    if dry and t.run(f"test -d {shlex.quote(t.root)}").returncode != 0:
        # nothing is created in a dry run: plan against an empty library, and say what would happen
        out(f"  the library folder {t.root} does not exist on {host} — "
            + ("--create would make it" if create else "a push needs --create to make it"))
        remote = {"index": [], "verified": {}}
    else:
        _root_ready(t, create and not dry)
        t.install_tool()
        if not dry:
            for line in t.tool("migrate")["migrated"]:
                out(f"  robot index: {line}")
        remote = t.tool("state", "--verify", *ids)
    rows = plan_push(local_root, ids, remote, replace)
    send = [r for r in rows if r["action"] == "send"]
    out(f"{'DRY RUN — nothing moved. ' if dry else ''}{len(ids)} item(s) → {host}:{t.root}")
    for r in rows:
        out(f"  {r['action'].upper():9s} {r['id']:28s} {r.get('kind') or '':5s} {_mb(r.get('bytes')):>9s}  {r['why']}")
    if dry or not send:
        out(f"{'would send' if dry else 'sent'} {len(send)} item(s), {_mb(sum(r['bytes'] for r in send))}"
            if send else "nothing to send")
        return 1 if any(r["action"] in ("refused", "conflict") for r in rows) else 0
    run = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    with tempfile.TemporaryFile() as tf:
        with tarfile.open(fileobj=tf, mode="w") as tar:
            for r in send:
                tar.add(os.path.join(local_root, r["entry"]["file"]), arcname=r["entry"]["file"])
        tf.seek(0)
        t.tool("stage", "--run", run, stdin=tf.read())
    res = t.tool("commit", "--run", run, *(["--replace"] if replace else []),
                 stdin=json.dumps([r["entry"] for r in send]).encode())["report"]
    moved = [x for x in res if x["action"] in ("added", "replaced")]
    for x in res:
        out(f"  {x['action'].upper():9s} {x['id']:28s} {x.get('why', 'verified on the robot (hash and size)')}")
    out(f"moved {len(moved)} item(s), {_mb(sum(x['bytes'] for x in moved))}; "
        f"{sum(r['action'] == 'skip' for r in rows)} already there")
    return 1 if len(moved) < len(send) or any(r["action"] in ("refused", "conflict") for r in rows) else 0


def status(host: str, root: str, python: str, out=print) -> int:
    t = Target(host, root, python)
    _root_ready(t, False)
    t.install_tool()
    idx = t.tool("state")["index"]
    v = t.tool("state", "--verify", *[e["id"] for e in idx])["verified"]
    out(f"{host}:{t.root} — {len(idx)} item(s)")
    for e in sorted(idx, key=lambda e: (e.get("kind", ""), e["id"])):
        out(f"  {v.get(e['id'], '?'):8s} {e['id']:28s} {e.get('kind', ''):5s} {_mb(e.get('bytes')):>9s}  "
            f"{e.get('licence', '')}")
    return 0 if all(x == "intact" for x in v.values()) else 1


def delete(host: str, ids: list, root: str, shows: str, python: str, dry: bool, out=print) -> int:
    t = Target(host, root, python)
    _root_ready(t, False)
    t.install_tool()
    res = t.tool("delete", "--shows", shows, "--ids", *ids, *(["--dry-run"] if dry else []))["report"]
    for x in res:
        out(f"  {x['action'].upper():12s} {x['id']:28s} {x.get('why', '')}")
    return 1 if any(x["action"] == "refused" for x in res) else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "remote":
        return remote_main(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", default=None, help="this computer's library (default: $OPENDUBS_LIBRARY, "
                                                     "else /var/lib/opendubs/library)")
    ap.add_argument("--root", default=REMOTE_ROOT, help="the robot's library folder")
    ap.add_argument("--python", default=REMOTE_PYTHON, help="python3 on the robot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("push")
    ps.add_argument("host")
    ps.add_argument("ids", nargs="*")
    ps.add_argument("--all", action="store_true")
    ps.add_argument("--dry-run", action="store_true")
    ps.add_argument("--replace", action="store_true")
    ps.add_argument("--create", action="store_true", help="create the robot's library folder if missing (sudo)")
    st = sub.add_parser("status")
    st.add_argument("host")
    dl = sub.add_parser("delete")
    dl.add_argument("host")
    dl.add_argument("ids", nargs="+")
    dl.add_argument("--shows", default=REMOTE_SHOWS, help="the robot's shows folder, checked for users")
    dl.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    local_root = os.path.realpath(a.library or library.root())
    try:
        if a.cmd == "push":
            return push(a.host, a.ids, a.all, local_root, a.root, a.python, a.dry_run, a.replace, a.create)
        if a.cmd == "status":
            return status(a.host, a.root, a.python)
        return delete(a.host, a.ids, a.root, a.shows, a.python, a.dry_run)
    except SyncError as e:
        print(f"refused: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
