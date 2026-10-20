#!/usr/bin/env python3
"""Reconcile OpenDubs fleet SSH access from access/manifest.ini.

Human-in-the-loop by design: you run --apply, nothing runs on a schedule. The
manifest shape is deliberately the same one a pull-based agent would want, so
moving to GitOps later is a new runner rather than a new data model.

    tools/access.py --check              validate the manifest (no robot needed)
    tools/access.py --diff  <robot>      show current vs desired, and the exact script
    tools/access.py --apply <robot>      run it

Safety properties, in order of how much they matter:

  * Only accounts in the MANAGED_GROUP are ever touched. That group is created and
    owned by this tool, so pre-existing accounts are invisible to it.
  * PROTECTED names are refused outright even if somehow in the group — belt and
    braces against a manifest edit that could lock you out of your own robot.
  * A robot not listed under [fleet] is refused. Robot 1 is absent on purpose.
  * --apply prints the script it is about to run. Nothing happens off-screen.

Push access to access/ is equivalent to root on every managed robot. Protect it.
"""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import pathlib
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ACCESS = ROOT / "access"
MANIFEST = ACCESS / "manifest.ini"

MANAGED_GROUP = "opendubs-managed"          # membership = "this tool owns you"
GROUPS = {"admin": "opendubs-admin", "operator": "opendubs-operator"}
PROTECTED = {"root", "emmar", "opendubs", "nobody", "daemon", "sync", "ubuntu"}

# Services an operator may cycle. Enumerated rather than wildcarded: a sudoers
# wildcard on a command argument is a well-known way to hand out more than you meant.
OPERATOR_UNITS = [
    "opendubs-dash", "opendubs-rplidar",
    "opendubs-rosbridge", "opendubs-foxglove", "opendubs-telemetry-log",
]


# ---------------------------------------------------------------- manifest ----
class Person:
    def __init__(self, name, cfg, section):
        self.name = name
        # One person, possibly several machines. Comma-separate the .pub files rather
        # than creating a second account -- attribution should follow the human, not the
        # laptop, and revoking someone should not mean hunting down their other entries.
        self.key_files = [ACCESS / k.strip()
                          for k in section.get("key", "").split(",") if k.strip()]
        self.group = section.get("group", "")
        self.robots = [r.strip() for r in section.get("robots", "").split(",") if r.strip()]
        self.expires = section.get("expires", "")

    @property
    def expiry_date(self):
        return dt.date.fromisoformat(self.expires)

    def expired(self, today=None):
        return self.expiry_date < (today or dt.date.today())

    def public_keys(self):
        return [f.read_text(encoding="utf-8").strip() for f in self.key_files]


def load():
    if not MANIFEST.exists():
        sys.exit(f"no manifest at {MANIFEST}")
    cfg = configparser.ConfigParser()
    cfg.read(MANIFEST, encoding="utf-8")
    if "fleet" not in cfg:
        sys.exit("manifest has no [fleet] section — nothing can be managed")
    fleet = [r.strip() for r in cfg["fleet"].get("robots", "").split(",") if r.strip()]
    people = [Person(n, cfg, cfg[n]) for n in cfg.sections() if n != "fleet"]
    return fleet, people


def check(fleet, people):
    """Validate everything that can be validated without touching a robot."""
    problems, warnings = [], []
    if not fleet:
        problems.append("[fleet] robots is empty")
    for p in people:
        if p.group not in GROUPS:
            problems.append(f"{p.name}: group '{p.group}' is not one of {sorted(GROUPS)}")
        if not p.robots:
            problems.append(f"{p.name}: no robots listed")
        for r in p.robots:
            if r not in fleet:
                problems.append(f"{p.name}: robot '{r}' is not in [fleet]")
        if not p.key_files:
            problems.append(f"{p.name}: no key listed")
        for kf in p.key_files:
            if not kf.exists():
                problems.append(f"{p.name}: key file {kf.name} not found")
                continue
            key = kf.read_text(encoding="utf-8").strip()
            if not key.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-")):
                problems.append(f"{p.name}: {kf.name} does not look like a public key")
            if "PRIVATE KEY" in key:
                problems.append(f"{p.name}: {kf.name} IS A PRIVATE KEY — remove it now")
        if p.name in PROTECTED:
            problems.append(f"{p.name}: name is protected and cannot be managed")
        try:
            if p.expired():
                warnings.append(f"{p.name}: expired {p.expires} — will be REMOVED on apply")
            elif (p.expiry_date - dt.date.today()).days < 14:
                warnings.append(f"{p.name}: expires {p.expires} (under two weeks)")
        except ValueError:
            problems.append(f"{p.name}: expires '{p.expires}' is not YYYY-MM-DD")
    return problems, warnings


# ------------------------------------------------------------------ remote ----
def ssh(robot, cmd, capture=True):
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", robot, cmd]
    r = subprocess.run(args, capture_output=capture, text=True)
    if r.returncode != 0 and capture:
        sys.exit(f"ssh to {robot} failed: {(r.stderr or '').strip() or r.returncode}")
    return (r.stdout or "").strip()


def current_managed(robot):
    """Members of MANAGED_GROUP — the only accounts we are allowed to consider."""
    out = ssh(robot, f"getent group {shlex.quote(MANAGED_GROUP)} || true")
    if not out:
        return []
    members = out.split(":")[-1]
    return sorted(m for m in (x.strip() for x in members.split(",")) if m)


def build_script(robot, people):
    """Emit the reconcile script. Printed verbatim by --diff, so there are no
    surprises about what runs as root."""
    desired = {p.name: p for p in people if robot in p.robots and not p.expired()}
    lines = [
        "set -euo pipefail",
        f"getent group {MANAGED_GROUP} >/dev/null || groupadd {MANAGED_GROUP}",
    ]
    for g in GROUPS.values():
        lines.append(f"getent group {g} >/dev/null || groupadd {g}")

    # sudoers, rendered fresh each run so the manifest stays authoritative
    lines += [
        f"cat > /etc/sudoers.d/opendubs-admin <<'SUDOERS'",
        f"%{GROUPS['admin']} ALL=(ALL) NOPASSWD:ALL",
        "SUDOERS",
        "chmod 0440 /etc/sudoers.d/opendubs-admin",
    ]
    units = ", ".join(f"/usr/bin/systemctl {v} {u}"
                      for u in OPERATOR_UNITS for v in ("start", "stop", "restart", "status"))
    lines += [
        f"cat > /etc/sudoers.d/opendubs-operator <<'SUDOERS'",
        f"%{GROUPS['operator']} ALL=(root) NOPASSWD: {units}",
        "SUDOERS",
        "chmod 0440 /etc/sudoers.d/opendubs-operator",
        "visudo -c >/dev/null",     # refuse to leave a broken sudoers behind
    ]

    for name, p in sorted(desired.items()):
        g = GROUPS[p.group]
        keys = p.public_keys()
        lines += [
            f"id -u {name} >/dev/null 2>&1 || useradd -m -s /bin/bash -c 'OpenDubs {p.group}' {name}",
            f"usermod -p '*' {name}",                       # key-only: no password hash
            f"usermod -aG {MANAGED_GROUP},{g} {name}",
            # exactly one group of ours, so a demotion actually demotes
            f"gpasswd -d {name} {GROUPS['admin'] if p.group != 'admin' else GROUPS['operator']} "
            f">/dev/null 2>&1 || true",
            f"install -d -m 700 -o {name} -g {name} /home/{name}/.ssh",
            f"cat > /home/{name}/.ssh/authorized_keys <<'KEY'",
            "\n".join(keys),          # every machine this person uses
            "KEY",
            f"chown {name}:{name} /home/{name}/.ssh/authorized_keys",
            f"chmod 600 /home/{name}/.ssh/authorized_keys",
        ]

    lines += [
        "# Remove managed accounts that are no longer listed (deleted or expired).",
        f"for u in $(getent group {MANAGED_GROUP} | cut -d: -f4 | tr ',' ' '); do",
        "  case \"$u\" in",
        "    " + "|".join(sorted(PROTECTED)) + ") echo \"refusing to touch protected user $u\"; continue ;;",
    ]
    if desired:
        lines.append("    " + "|".join(sorted(desired)) + ") continue ;;")
    lines += [
        "  esac",
        "  echo \"removing $u\"",
        "  userdel -r \"$u\" || true",
        "done",
    ]
    return "\n".join(lines), desired


# -------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate the manifest")
    ap.add_argument("--diff", metavar="ROBOT", help="show what would change")
    ap.add_argument("--apply", metavar="ROBOT", help="reconcile the robot")
    a = ap.parse_args()

    fleet, people = load()
    problems, warnings = check(fleet, people)
    for w in warnings:
        print(f"  warn: {w}")
    if problems:
        for p in problems:
            print(f"  ERROR: {p}")
        sys.exit(1)

    if a.check or not (a.diff or a.apply):
        print(f"OK — {len(people)} entries, fleet: {', '.join(fleet)}")
        for p in people:
            state = "EXPIRED" if p.expired() else f"until {p.expires}"
            nk = f"{len(p.key_files)} key" + ("s" if len(p.key_files) != 1 else "")
            print(f"  {p.name:12} {p.group:9} {','.join(p.robots):12} {nk:7} {state}")
        return

    robot = a.diff or a.apply
    if robot not in fleet:
        sys.exit(f"'{robot}' is not in [fleet] — refusing. Managed robots: {', '.join(fleet)}")

    script, desired = build_script(robot, people)
    have = current_managed(robot)
    want = sorted(desired)
    print(f"\n{robot}:")
    print(f"  managed now : {', '.join(have) or '(none)'}")
    print(f"  should be   : {', '.join(want) or '(none)'}")
    for n in sorted(set(want) - set(have)):
        print(f"  + add    {n} ({desired[n].group})")
    for n in sorted(set(have) - set(want)):
        print(f"  - REMOVE {n}")
    for n in sorted(set(have) & set(want)):
        print(f"  = keep   {n} ({desired[n].group}, key re-asserted)")

    if a.diff:
        print("\n--- script that --apply would run as root ---")
        print(script)
        return

    print("\napplying ...")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", robot, "sudo bash -s"],
                       input=script, text=True)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
