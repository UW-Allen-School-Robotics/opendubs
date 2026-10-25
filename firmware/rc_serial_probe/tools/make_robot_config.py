#!/usr/bin/env python3
"""Make a robot's src/robot_config.h from its OLDER main.cpp, which carried the values inline.

    python3 tools/make_robot_config.py ROBOT_MAIN.cpp [--reference REPO_MAIN.cpp] > robot_config.h

It copies each per-robot value (kDriveCh, kDriveInvert, kStrafeInvert, the ESC shaping, the lift
relays) from ROBOT_MAIN.cpp into src/robot_config.h.example's layout, so NOTHING CHANGES: the new
firmware drives exactly as the old one did. It refuses if a value is missing, rather than guessing.

With --reference (the repository's main.cpp from before robot_config.h, e.g.
`git show e360030:firmware/rc_serial_probe/src/main.cpp`) it also reports, on stderr, every OTHER
constant whose value differs, and whether the drive mixer differs: hand edits a robot carried in its
main.cpp that robot_config.h does not hold. Read that list before the old main.cpp is replaced; each
item is either moved into robot_config.h on purpose or knowingly dropped.
"""
import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = HERE / "src" / "robot_config.h.example"
KNOWN = ["kDriveCh", "kDriveInvert", "kStrafeInvert", "kEscNeutralUs", "kEscSpanUs", "kEscSlewUs",
         "kEscRevDwellTicks", "kRelayMastUp", "kRelayMastDown"]
OPTIONAL = {"kStrafeInvert": "1"}     # older main.cpp files may not have it: 1 is "no change"
DEF = re.compile(r"^\s*static\s+constexpr\s+[\w:]+\s+(k\w+)\s*(\[\d*\])?\s*=\s*([^;]+);", re.M)
DEFINE = re.compile(r"^\s*#define\s+([A-Z_][A-Z0-9_]*)\s+(.+?)\s*(?://.*)?$", re.M)


def values(text):
    out = {m.group(1): " ".join(m.group(3).split()) for m in DEF.finditer(text)}
    out.update({m.group(1): " ".join(m.group(2).split()) for m in DEFINE.finditer(text)})
    return out


def body(text, fn):
    m = re.search(r"static\s+void\s+" + fn + r"\s*\([^)]*\)\s*\{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return re.sub(r"//[^\n]*", "", text[m.end():i]).split()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("robot_main")
    ap.add_argument("--reference", help="the repository's main.cpp from before robot_config.h")
    a = ap.parse_args()
    robot = pathlib.Path(a.robot_main).read_text()
    have = values(robot)
    out = TEMPLATE.read_text()
    missing = []
    for name in KNOWN:
        if name not in have:
            if name in OPTIONAL:
                have[name] = OPTIONAL[name]
                print(f"note: {name} is not in {a.robot_main}; using {OPTIONAL[name]} (no change). "
                      "If that robot negates strafe some other way, the mixer check below says so.", file=sys.stderr)
            else:
                missing.append(name)
                continue
        pat = re.compile(r"^(\s*static\s+constexpr\s+[\w:]+\s+" + name + r"\s*(?:\[\d*\])?\s*=\s*)([^;]+)(;)", re.M)
        out, n = pat.subn(lambda m: m.group(1) + have[name] + m.group(3), out)
        if n != 1:
            sys.exit(f"the template has {n} definitions of {name}; expected one")
    if missing:
        sys.exit(f"{a.robot_main} lacks {', '.join(missing)}: refusing to guess them")
    out = out.replace("// robot_config.h — THIS ROBOT's calibration",
                      f"// robot_config.h — made by tools/make_robot_config.py from {pathlib.Path(a.robot_main).name}.\n"
                      "// THIS ROBOT's calibration", 1)
    sys.stdout.write(out)

    if a.reference:
        ref_text = pathlib.Path(a.reference).read_text()
        ref = values(ref_text)
        diffs = [(k, ref.get(k), v) for k, v in sorted(have.items())
                 if k not in KNOWN and k in ref and ref[k] != v]
        only_robot = sorted(k for k in have if k not in ref and k not in KNOWN)
        print("\n== other differences between the robot's main.cpp and the reference ==", file=sys.stderr)
        for k, r, v in diffs:
            print(f"  {k}: reference {r}  robot {v}", file=sys.stderr)
        for k in only_robot:
            print(f"  {k}: only on the robot = {have[k]}", file=sys.stderr)
        mix_same = body(robot, "driveMix") == body(ref_text, "driveMix")
        print(f"  driveMix: {'same as the reference' if mix_same else 'DIFFERS from the reference: compare by hand'}", file=sys.stderr)
        if not diffs and not only_robot and mix_same:
            print("  none: robot_config.h carries everything the robot's main.cpp changed", file=sys.stderr)


if __name__ == "__main__":
    main()
