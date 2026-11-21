#!/usr/bin/env python3
"""The three faces of the robot API agree: the spec the model reads, the live ROS 2 client and
the dry-run robot. A function the spec names but the client lacks is a NameError in the middle
of a run on the robot; a signature that differs passes dry-run at the desk and fails live.

    python3 software/architect/tests/test_api.py      # stdlib only; no ROS, no ssa
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import api                      # noqa: E402
from opendubs_architect.client import OpenDubsRobotClient  # noqa: E402
from opendubs_architect.dryrun import DryRunRobot       # noqa: E402

failures = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(what)


spec_names = set(re.findall(r"^([a-z_]+)\(", api.API_SPEC, flags=re.M))
names = set(api.PRIMITIVE_NAMES)
check("every primitive is in the spec", names <= spec_names, sorted(names - spec_names))
check("the spec names no function the robot lacks", spec_names <= names, sorted(spec_names - names))
check("primitive names are unique", len(names) == len(api.PRIMITIVE_NAMES))
check("motion primitives are primitives", api.MOTION_PRIMITIVES <= names, sorted(api.MOTION_PRIMITIVES - names))

for name in api.PRIMITIVE_NAMES:
    live, dry = getattr(OpenDubsRobotClient, name, None), getattr(DryRunRobot, name, None)
    if live is None or dry is None:
        check(f"{name} exists on both", False, f"live={live is not None} dry={dry is not None}")
        continue
    sig = lambda f: [(p.name, p.default) for p in inspect.signature(f).parameters.values()]
    check(f"{name}: dry-run signature matches live", sig(live) == sig(dry), f"{sig(live)} != {sig(dry)}")

# The show backend and the robot node: only where their imports (the choreo modules, PyYAML) load
try:
    from opendubs_architect.robot_node import HANDLED
    from opendubs_architect.show_backend import ShowRobot
except ImportError as e:
    print(f"skip the show backend and the robot node: {e}")
else:
    for name, f in vars(ShowRobot).items():
        if name in names:
            live = getattr(OpenDubsRobotClient, name)
            check(f"{name}: show backend signature matches live", sig(live) == sig(f), f"{sig(live)} != {sig(f)}")
    check("the robot node serves only primitives", set(HANDLED) <= names, sorted(set(HANDLED) - names))

for prof, offered in api.PROFILES.items():
    shown = set(re.findall(r"^([a-z_]+)\(", api.spec_for(offered), flags=re.M))
    check(f"the {prof} profile's spec shows exactly its primitives", shown == set(offered) and set(offered) <= names,
          sorted(shown ^ set(offered)))

check("'scene' still means the head camera (task_scorers passes it)", api.check_camera("scene") == "head")

print(f"\n{len(failures)} failure(s)" if failures else "\nOK")
sys.exit(1 if failures else 0)
