#!/usr/bin/env bash
# V2, trick or treat on the REAL right arm: the arm service run by hand from a staged copy of this branch, for supervised
# testing (no /etc change, no deploy; the systemd unit and the dashboard keep their own code).
#   treat_arm_service.sh up        start it in the background as kiosk: motors on, HOLDING where the arm is (no motion)
#   treat_arm_service.sh down      SIGINT, as systemd stops it: the arm holds, the motors stay stiff
#   treat_arm_service.sh status    running or not; the service's own status; whether /right_arm/command is listed
#   treat_arm_service.sh log       follow its log
#   treat_arm_service.sh ctl CMD   supervise | unsupervise | status | hold | activate | park
#                                  (activate and park MOVE THE ARM: hold the deadman)
# `up` refuses while the systemd unit, the old stack (openarm-up) or another right arm service runs, or while arm_right
# is not ERROR-ACTIVE. Do not press the dashboard's arm buttons while it runs: they start the old stack.
#
# STAGING ($D, default /opt/treat-follow-archit; kiosk must read it), once per code change, with the service down:
#   $D/repo                  this branch's tree (git archive HEAD), and a REVISION file naming the commit
#   $D/ws                    opendubs_arm_interfaces built with colcon (source /opt/ros/jazzy/setup.bash;
#                            cp -r repo/software/openarm/interfaces/opendubs_arm_interfaces ws/src/;
#                            colcon build --packages-select opendubs_arm_interfaces)
#   $D/arm-setup.bash        source /opt/gafro/setup.bash, then $D/ws/install/setup.bash
#
# THE TEST (opendubs2, 2026-10-28, worked): CH5 MANUAL, CH7 OFF, the e-stop in reach, both arms at stow and free.
#   openarm-up down (--drop only with both arms at stow)      the old stack off
#   sudo systemctl restart openarm-can@arm_right              if `up` says the link is not ERROR-ACTIVE; if the driver
#                                                             still says "not healthy", cycle the e-stop first (the
#                                                             motors kept a state from the old stack until it was)
#   treat_arm_service.sh up; status; ctl supervise            no motion
#   deadman held: ctl activate                                stow -> ready
#   deadman held: treat_follow.sh reach-real                  ready -> reach the held-out hand (0.3 rad/s, 20 cm short)
#                                                             -> 5 s -> ready; releasing the deadman stops it for good
#   deadman held: ctl park; then ctl unsupervise; down        ready -> stow
set -eo pipefail
D="${TREAT_ARM_DIR:-/opt/treat-follow-archit}"
SOCK_DIR=/run/opendubs-arm
SOCK=$SOCK_DIR/opendubs-arm-service-right.sock
RUN=/tmp/treat-arm-kiosk
SERVICE=$D/repo/software/openarm/gafro/arm_service.py
CFG=$(sed -n 's/^OPENDUBS_ARM_CONFIG=//p' /etc/opendubs/arm-service.env | tail -1)

ctl() {
  sudo -n -u kiosk env OPENDUBS_ARM_SOCKET_DIR=$SOCK_DIR bash -c \
    "set +u; source $D/arm-setup.bash >/dev/null 2>&1; exec python $SERVICE ctl --side right \"\$@\"" _ "$@"
}
running() { pgrep -f "^python[0-9.]* -u $SERVICE run"; }   # anchored: never matches this script's own command line

case "${1:-}" in
  up)
    if pid=$(running); then echo "already running (pid $pid)"; exit 0; fi
    if systemctl is-active -q opendubs-arm@right; then echo "the systemd unit opendubs-arm@right is running: stop it first" >&2; exit 1; fi
    if pgrep -f "^/opt/ros/[a-z]*/lib/controller_manager/ros2_control_node" >/dev/null; then
      echo "the old arm stack (openarm-up) is running: 'openarm-up down' first (--drop only with both arms at stow)" >&2; exit 1
    fi
    if pgrep -f "^python[0-9.]* -u /.*arm_service.py run --side right --socket $SOCK_DIR" >/dev/null; then
      echo "another right arm service is running" >&2; exit 1
    fi
    state=$(ip -details link show arm_right | grep -o "state ERROR-[A-Z]*\|state BUS-OFF" || true)
    if [ "$state" != "state ERROR-ACTIVE" ]; then
      echo "arm_right is '${state:-missing}', not ERROR-ACTIVE: with the arm powered, sudo systemctl restart openarm-can@arm_right" >&2; exit 1
    fi
    [ -n "$CFG" ] || { echo "no OPENDUBS_ARM_CONFIG in /etc/opendubs/arm-service.env" >&2; exit 1; }
    sudo -n install -d -o kiosk -g kiosk -m 0750 $SOCK_DIR
    sudo -n install -d -o kiosk -g kiosk -m 0755 $RUN
    sudo -n -u kiosk rm -f $SOCK $RUN/arm-service.log
    sudo -n -u kiosk env OPENDUBS_ARM_SOCKET_DIR=$SOCK_DIR setsid nohup bash -c \
      "set +u; umask 022; source $D/arm-setup.bash > $RUN/arm-service.log 2>&1; exec python -u $SERVICE run --side right --socket $SOCK --hardware-config $CFG >> $RUN/arm-service.log 2>&1" \
      >/dev/null 2>&1 </dev/null &
    for _ in $(seq 1 60); do grep -q -E "holding the right arm|Traceback" $RUN/arm-service.log 2>/dev/null && break; sleep 1; done
    grep -v "more than one kinematic chain" $RUN/arm-service.log | tail -5 || true
    if pid=$(running); then echo "up (pid $pid), code $(cat $D/repo/REVISION): holding the arm, nothing moved"; else echo "did not start: see $RUN/arm-service.log" >&2; exit 1; fi
    if grep -qi "no arm command action" $RUN/arm-service.log; then echo "WARNING: the command action is NOT served (interfaces not loaded)" >&2; fi
    ;;
  down)
    pid=$(running) || { echo "not running"; exit 0; }
    sudo -n kill -INT "$pid"
    for _ in $(seq 1 20); do sudo -n kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    echo "stopped (the arm holds where it is; the motors stay stiff until the e-stop power cycle)"
    ;;
  status)
    if pid=$(running); then echo "service: running (pid $pid), code $(cat $D/repo/REVISION)"; ctl status | head -3; else echo "service: stopped"; fi
    (set +u; source /opt/ros/jazzy/setup.bash; timeout 8 ros2 action list 2>/dev/null | grep right_arm) || echo "/right_arm/command: not listed"
    ;;
  log)
    tail -f $RUN/arm-service.log
    ;;
  ctl)
    shift; [ $# -ge 1 ] || { echo "usage: $0 ctl supervise|unsupervise|status|hold|activate|park" >&2; exit 1; }
    ctl "$@"
    ;;
  *)
    sed -n 2,11p "$0"; exit 1
    ;;
esac
