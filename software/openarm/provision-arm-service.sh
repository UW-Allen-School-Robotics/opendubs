#!/usr/bin/env bash
# Install the arm service as a systemd unit: opendubs-arm@.service (one per arm), the wrapper it runs, this robot's settings file and the rule that lets the
# dashboard start and stop the units. Idempotent; run ON the robot as root:
#
#   sudo /opt/opendubs/openarm/provision-arm-service.sh       (provision-openarm.sh runs it too)
#
# It does not enable or start anything: the arms never power up by themselves, and starting the service enables the motors (see README.md).
#
#   ROOT=<dir>   install under <dir> instead of / and change nothing else (no root needed, no daemon-reload): to see what goes where, and to test.
#
# What it puts where:
#   /usr/local/lib/opendubs/opendubs-arm     the wrapper (always replaced)
#   /usr/local/bin/arm                       the operator's command: arm start|status|activate|follow|viewer|doctor [left|right] (always replaced)
#   /etc/systemd/system/opendubs-arm@.service  the unit (always replaced)
#   /etc/opendubs/arm-service.env            this robot's settings, from opendubs-arm.env.example: only if absent, a deploy never overwrites it
#   /opt/opendubs/ws                         the ROS workspace with opendubs_arm_interfaces (the `command` action's type), built from interfaces/ here; the wrapper
#                                            sources it. Skipped, with a warning, where ROS or colcon is missing; ROOT= does not build it
#   /etc/sudoers.d/opendubs-arm              kiosk may `systemctl start|stop|restart opendubs-arm@left|right.service` and nothing else; checked with visudo first
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-}"
USER_NAME=kiosk                           # the unit's User= and the sudoers rule's user

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "provision-arm-service.sh: run it as root (sudo), or set ROOT=<dir> to install somewhere else" >&2
  exit 1
fi
for f in opendubs-arm arm opendubs-arm@.service opendubs-arm.env.example opendubs-arm.sudoers; do
  [ -f "$DIR/$f" ] || { echo "provision-arm-service.sh: $DIR/$f is missing" >&2; exit 1; }
done

# The sudoers rule is checked BEFORE anything is installed: a broken file in /etc/sudoers.d breaks sudo for everyone.
if ! command -v visudo >/dev/null 2>&1; then
  echo "provision-arm-service.sh: no visudo, so the sudoers rule cannot be checked; nothing installed" >&2
  exit 1
fi
visudo -cf "$DIR/opendubs-arm.sudoers" >/dev/null || { echo "provision-arm-service.sh: opendubs-arm.sudoers is not valid; nothing installed" >&2; exit 1; }

owner=()
[ -z "$ROOT" ] && owner=(-o root -g root)

install -D -m0755 "${owner[@]}" "$DIR/opendubs-arm" "$ROOT/usr/local/lib/opendubs/opendubs-arm"
install -D -m0755 "${owner[@]}" "$DIR/arm" "$ROOT/usr/local/bin/arm"
install -D -m0644 "${owner[@]}" "$DIR/opendubs-arm@.service" "$ROOT/etc/systemd/system/opendubs-arm@.service"
if [ -e "$ROOT/etc/opendubs/arm-service.env" ]; then
  echo "-- keeping this robot's $ROOT/etc/opendubs/arm-service.env"
else
  install -D -m0644 "${owner[@]}" "$DIR/opendubs-arm.env.example" "$ROOT/etc/opendubs/arm-service.env"
  echo "-- installed $ROOT/etc/opendubs/arm-service.env from the template: EDIT IT (OPENDUBS_REPO, OPENDUBS_ARM_CONFIG)"
fi
install -D -m0440 "${owner[@]}" "$DIR/opendubs-arm.sudoers" "$ROOT/etc/sudoers.d/opendubs-arm"

if [ -n "$ROOT" ]; then
  echo "== installed under $ROOT (nothing else changed) =="
  exit 0
fi

# No arm controller starts at boot (notes/design/arm-controller-handover.md): the operator chooses one. The unit has no [Install], so only a link made by hand can start it;
# on 2026-10-27 the units on opendubs2 had been installed from a checkout outside any deploy. Remove any such link, and say so. Moves nothing: it changes only what the next boot does.
for link in /etc/systemd/system/*.wants/opendubs-arm@*.service; do
  [ -L "$link" ] || continue
  rm -f "$link"
  echo "-- removed $link: no arm controller starts at boot"
done
systemctl daemon-reload

# The interfaces package, into a workspace the unit's user can read. Built from the copy of this directory that is being installed.
ws="${OPENDUBS_ARM_WS:-/opt/opendubs/ws}"
ros_setup="$(ls -d "${GAFRO_ROS_ROOT:-/opt/ros}"/*/setup.bash 2>/dev/null | sort | tail -n 1 || true)"
if [ -z "$ros_setup" ] || ! bash -c ". '$ros_setup' && command -v colcon" >/dev/null 2>&1; then
  echo "!! no ROS 2 or no colcon: opendubs_arm_interfaces is not built, so the arm service serves no command action"
else
  install -d -m0755 "$ws/src"
  rm -rf "$ws/src/opendubs_arm_interfaces"
  cp -a "$DIR/interfaces" "$ws/src/opendubs_arm_interfaces"
  bash -c "set +u; . '$ros_setup'; set -u; cd '$ws' && colcon build --packages-select opendubs_arm_interfaces" >/dev/null \
    || { echo "provision-arm-service.sh: colcon build of opendubs_arm_interfaces failed (in $ws)" >&2; exit 1; }
  chmod -R a+rX "$ws"
  echo "-- built opendubs_arm_interfaces into $ws"
fi
echo "== arm service installed (not enabled, not started) =="

# What would stop it from starting, said now rather than at the first start. Warnings only.
if ! id "$USER_NAME" >/dev/null 2>&1; then
  echo "!! no user '$USER_NAME' on this machine: the unit runs as it (User=$USER_NAME in opendubs-arm@.service)"
fi
repo=$(sed -n 's/^OPENDUBS_REPO=//p' /etc/opendubs/arm-service.env | tail -n 1)
if [ -z "$repo" ]; then
  echo "!! OPENDUBS_REPO is not set in /etc/opendubs/arm-service.env"
elif id "$USER_NAME" >/dev/null 2>&1 && ! sudo -u "$USER_NAME" test -r "$repo/software/openarm/gafro/arm_service.py"; then
  echo "!! $USER_NAME cannot read $repo/software/openarm/gafro/arm_service.py (OPENDUBS_REPO in /etc/opendubs/arm-service.env)"
fi
[ -r /opt/gafro/setup.bash ] || echo "!! no /opt/gafro/setup.bash: the gafro environment is not installed (gafro-deploy)"
echo "   start an arm:  arm start right      then:  arm status | activate | follow | viewer | doctor      logs:  journalctl -u opendubs-arm@right -f"
