#!/usr/bin/env bash
# READ-ONLY guided check that each joint of the RIGHT arm reads the way the model says it should, with the motors OFF (limp, after the e-stop cycle).
# One joint at a time: it says how to move the joint, reads the arm for a few seconds while you do, and tells you whether the joint's number ROSE (matches
# the model), FELL (the opposite: its direction in the robot's config would need to be -1) or did not move. Only the health tool's status queries are sent:
# no enable, disable or command, and never --toggle.
#   bash arm_direction_check.sh [joint ...]          (default: 1 2 3 4 5 6 7;  SECS=12 sets the window)
prefix=${ARM_PREFIX:-$HOME/coding/local}
bin="$prefix/bin/openarm-can-health"
secs=${SECS:-12}
side=right
TEXT=(
    ""
    "swing the WHOLE ARM FORWARD, toward where the face looks"
    "swing the arm OUT TO THE RIGHT, away from the body"
    "roll the UPPER ARM CLOCKWISE seen from above (a hand held in front of you goes to the right)"
    "bend the ELBOW so the hand goes FORWARD"
    "roll the FOREARM CLOCKWISE seen from above"
    "tip the HAND so its lower end moves TOWARD THE BODY'S CENTRE (to the robot's left)"
    "rotate the HAND (wrist roll) so its lower end goes FORWARD"
)

ANALYSE='
import os, re, sys
joint = int(sys.argv[1])
rows, enabled = {}, set()
for line in os.environ["DATA"].splitlines():
    m = re.match(r"\s*0x0([1-7])\s+(-?\d+\.\d+)\s+\S+\s+\S+\s+\d+\s+\d+\s+(\w+)", line)
    if not m or "never" in line:                       # a motor that never answered in this window reads 0.000: not a reading
        continue
    j = int(m.group(1))
    rows.setdefault(j, []).append(float(m.group(2)))
    if m.group(3) == "ENABLED":
        enabled.add(j)
if joint not in rows or len(rows[joint]) < 3:
    print(f"  joint {joint}: no usable readings (the motor did not answer): repeat, and say so if it keeps happening")
    sys.exit(0)
if enabled:
    print(f"  the motors of joints {sorted(enabled)} are ENABLED (stiff): this reading is not valid. Do the e-stop power cycle first.")
    sys.exit(0)
exc = {j: (max(v) - v[0], v[0] - min(v)) for j, v in rows.items()}          # (rise, fall) relative to the first reading
up, down = exc[joint]
if max(up, down) < 0.05:
    print(f"  joint {joint}: did not move (under 3 deg). Move it further and repeat.")
elif min(up, down) > 0.4 * max(up, down):
    print(f"  joint {joint}: moved BOTH ways (rose {up:.2f}, fell {down:.2f}): inconclusive. Repeat and move it only the way described, then back.")
elif up > down:
    print(f"  joint {joint}: ROSE by {up:+.2f} rad ({up * 57.3:.0f} deg)   -> matches the model")
else:
    print(f"  joint {joint}: FELL by {-down:+.2f} rad ({down * 57.3:.0f} deg)   -> the OPPOSITE of the model (if you moved it the way described)")
others = [f"j{j} ({max(a, b):.2f})" for j, (a, b) in sorted(exc.items()) if j != joint and max(a, b) > 0.15]
if others:
    print("  also moved by more than 0.15 rad: " + ", ".join(others) + "  (a limp wrist or forearm swings with the arm: not a problem by itself)")
'

analyse() { DATA="$(cat)" python3 -c "$ANALYSE" "$1"; }

main() {
    [ -x "$bin" ] || { echo "no $bin (set ARM_PREFIX)" >&2; exit 1; }
    export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    local joints=("$@"); [ ${#joints[@]} -gt 0 ] || joints=(1 2 3 4 5 6 7)
    echo "Right arm, motors OFF (limp) and SUPPORTED. For each joint: read the instruction, press Enter, then within ${secs} s do it by about 10 degrees and bring it back."
    declare -A result
    for j in "${joints[@]}"; do
        echo
        echo "Joint $j: ${TEXT[$j]}"
        read -r -p "  press Enter to start the ${secs} s reading... " _ < /dev/tty
        echo "  reading: MOVE JOINT $j NOW, then back..."
        out=$("$bin" "arm_$side" "$secs" 7 2>&1)
        verdict=$(printf '%s\n' "$out" | analyse "$j")
        echo "$verdict"
        result[$j]=$(echo "$verdict" | head -1 | sed 's/^ *//')
    done
    echo; echo "=== summary ==="
    for j in "${joints[@]}"; do echo "${result[$j]}"; done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
