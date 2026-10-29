#!/usr/bin/env bash
# Get the REAL right arm and the treat services ready for `treat_follow.sh sequence`, in one go: every check and set-up
# step that came before a run on opendubs2, in order. It stops at the first thing it cannot put right safely, and says
# what to do.
#
#   treat_follow.sh prepare [--check] [service options]
#
#   1. the controller: CH5 MANUAL or AUTO, the e-stop clear, motion power on
#   2. the old arm stack (openarm-up) off: `openarm-up down`, which itself refuses while an arm is active
#   3. the right arm's CAN link ERROR-ACTIVE (restarted if not)
#   4. the arm service from the staged branch (treat_arm_service.sh: /opt/treat-follow-archit) up, holding (NO MOTION),
#      /right_arm/command listed, supervised
#   5. the arm at ready: already there -> nothing; at stow -> HOLD THE DEADMAN, `activate` (THE ARM MOVES); anywhere else
#      -> stop: that is `ctl park --allow-large` with a person watching
#   6. the treat services (`services --real --head --left-stowed`, or the options given) up
#   then: "ready: hold the deadman and run ./treat_follow.sh sequence"
#
# --check: only look and report, change nothing, move nothing.
# Service options (after --check, if any) replace `--head --left-stowed`, e.g. `prepare --head --left-stowed --head-pan`.
set -eo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
A=/opt/treat-follow-archit/arm-service
T="${HERE}/treat_follow.sh"
DASH="${OPENDUBS_DASH:-http://127.0.0.1:8080}"
READY_DEG=(0 40.1 -28.6 103.1 -57.3 -5.7 -31.5)      # the model's ready keyframe, deg
NEAR_DEG=5                                            # the arm service's own start tolerance

CHECK=0
if [[ "${1:-}" == --check ]]; then CHECK=1; shift; fi
SERVICE_ARGS=("$@")
[[ ${#SERVICE_ARGS[@]} -gt 0 ]] || SERVICE_ARGS=(--head --left-stowed)

say() { printf '%s\n' "$*"; }
stop_here() { say ""; say "NOT READY: $*"; exit 1; }
robot() { curl -s -m 2 "${DASH}/api/choreo/robot" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1]))' "$1"; }
link_state() { ip -details link show arm_right 2>/dev/null | grep -o "state ERROR-[A-Z]*\|state BUS-OFF" || echo "state MISSING"; }
old_stack() { pgrep -f "^/opt/ros/[a-z]*/lib/controller_manager/ros2_control_""node" >/dev/null; }   # split: never matches this line
arm_up() { pgrep -f "^python[0-9.]* -u /opt/treat-follow-archit/repo/software/openarm/gafro/arm_service.py run" >/dev/null; }
services_up() { pgrep -f "^python[0-9.]* -u .*/treat_services.py .*--real" >/dev/null; }
joints() { "$A" ctl status 2>/dev/null | sed -n 's/^joints (deg) //p'; }
pose_is() {   # pose_is stow|ready "<7 joints>": all within NEAR_DEG
  python3 - "$1" "$2" "${READY_DEG[*]}" "$NEAR_DEG" <<'PY'
import sys
which, q, ready, tol = sys.argv[1], [float(v) for v in sys.argv[2].split()], [float(v) for v in sys.argv[3].split()], float(sys.argv[4])
ref = [0.0] * 7 if which == "stow" else ready
sys.exit(0 if len(q) == 7 and max(abs(a - b) for a, b in zip(q, ref)) <= tol else 1)
PY
}
wait_deadman() {
  say "   HOLD THE DEADMAN NOW (the arm will move)"
  for ((i = 0; i < 600; i++)); do
    [[ "$(robot deadman)" == True && "$(robot estop)" == False ]] && { say "   deadman held"; return 0; }
    sleep 0.2
  done
  stop_here "the deadman was not held within 2 minutes: nothing moved"
}

say "== 1. controller"
ch5="$(robot ch5)"; estop="$(robot estop)"; power="$(robot motion_power)"
say "   CH5 ${ch5}, e-stop ${estop}, motion power ${power}"
[[ "$ch5" == MANUAL || "$ch5" == AUTO ]] || stop_here "set CH5 to MANUAL"
[[ "$estop" == False ]] || stop_here "release the e-stop"
[[ "$power" == True ]] || stop_here "motion power is off (the contactor): power the arms"

say "== 2. old arm stack (openarm-up)"
if old_stack; then
  [[ $CHECK == 1 ]] && stop_here "the old stack is running: 'openarm-up down' (prepare does it)"
  say "   running: openarm-up down"
  openarm-up down || stop_here "openarm-up down refused (an arm is active): park it with openarm-up, or with both arms at stow 'openarm-up down --drop'"
  old_stack && stop_here "the old stack is still running"
fi
say "   off"

say "== 3. CAN link arm_right"
state="$(link_state)"
if [[ "$state" != "state ERROR-ACTIVE" ]] && ! arm_up; then
  [[ $CHECK == 1 ]] && stop_here "arm_right is '${state}' (prepare restarts it)"
  say "   ${state}: restarting"
  sudo -n systemctl restart openarm-can@arm_right; sleep 2
  state="$(link_state)"
fi
say "   ${state}"
[[ "$state" == "state ERROR-ACTIVE" ]] || arm_up || stop_here "arm_right stays '${state}': check the arm's power and CAN cable"

say "== 4. arm service"
if ! arm_up; then
  [[ $CHECK == 1 ]] && stop_here "the arm service is not running (prepare starts it)"
  say "   starting it (holds the arm: no motion)"
  "$A" up || stop_here "the arm service did not start. If it says 'CAN bus arm_right is not healthy': support the arm unless it is at stow, cycle the e-stop, run prepare again"
fi
"$A" status | sed 's/^/   /'
"$A" status | grep -q "/right_arm/command" || stop_here "/right_arm/command is not listed: the arm service skipped the command action"
if ! "$A" ctl status 2>/dev/null | grep -q "supervised: yes"; then
  [[ $CHECK == 1 ]] && stop_here "not supervised (prepare supervises)"
  "$A" ctl supervise | head -1 | sed 's/^/   /'
fi
say "   supervised"

say "== 5. arm at ready"
q="$(joints)"
say "   joints (deg) ${q}"
if pose_is ready "$q"; then
  say "   at ready"
elif pose_is stow "$q"; then
  [[ $CHECK == 1 ]] && stop_here "the arm is at stow (prepare activates it, with the deadman held)"
  wait_deadman
  say "   activate: stow -> ready"
  "$A" ctl activate | head -1 | sed 's/^/   /'
  for ((i = 0; i < 40; i++)); do "$A" ctl status 2>/dev/null | grep -q "activate done\|stopped\|refused" && break; sleep 0.5; done
  q="$(joints)"; say "   joints (deg) ${q}"
  pose_is ready "$q" || stop_here "activate did not reach ready: $("$A" ctl status 2>/dev/null | head -1)"
  say "   at ready"
else
  stop_here "the arm is neither at stow nor at ready. With the deadman held and a person watching the path: $A ctl park --allow-large --move-seconds 10, then prepare again"
fi

say "== 6. treat services"
if ! services_up; then
  [[ $CHECK == 1 ]] && stop_here "the treat services are not running (prepare starts them)"
  "$T" services --real "${SERVICE_ARGS[@]}" | tail -2 | sed 's/^/   /'
fi
services_up || stop_here "the treat services did not start: ${T} services --real ${SERVICE_ARGS[*]}"
say "   up (--real ${SERVICE_ARGS[*]})"

say ""
if [[ $CHECK == 1 ]]; then say "CHECK: everything is ready (nothing was changed)"; else say "READY: hold the deadman and run  ${T} sequence"; fi
