#!/bin/bash
# Is the gafro stack on this robot current, and does it import? Read-only: nothing is started, stopped or written.
#
#   bash software/openarm/gafro/tools/check_stack.sh            (from a fresh login shell: an old shell can carry a stale AMENT_PREFIX_PATH)
#
# It compares every wheel of the newest gafro-deploy release (~/gafro-deploy/dist/*/manifest-aarch64-py3.12.json) with the commit its remote is at now,
# then checks what is actually installed in /opt/gafro/venv: the imports (ssik's native module included, which pip can silently replace by a pure-Python
# build), libgafro's exports (tinyxml2 hidden, yaml-cpp exported), the driver's gripper poll and validated link recovery, the repositories on the robot,
# the ROS 2 bridge's age against libgafro (it is not built by gafro-deploy and must be rebuilt after every gafro release), the services, and the CAN
# controller state of each arm. `[ !! ]` is something to fix, `[ .. ]` is information. See docs/openarm-arm-service.md.
source /opt/gafro/setup.bash >/dev/null 2>&1
SP=/opt/gafro/venv/lib/python3.12/site-packages
ok() { printf "  [ ok ] %s\n" "$*"; }; bad() { printf "  [ !! ] %s\n" "$*"; }; note() { printf "  [ .. ] %s\n" "$*"; }
head_of() { git ls-remote "$1" "refs/heads/${2:-main}" 2>/dev/null | cut -c1-8; }
echo "== shell"; command -v ros2 >/dev/null && ok "ros2: $(command -v ros2)" || bad "ros2 not on PATH after sourcing /opt/gafro/setup.bash (AMENT_PREFIX_PATH='${AMENT_PREFIX_PATH:-unset}')"
echo "== wheels built from the release manifest vs the remotes' main"
M=$(ls -d ~/gafro-deploy/dist/*/manifest-aarch64-py3.12.json | tail -1); note "manifest: $M"
python3 - "$M" <<'PY' | while read name commit src ref; do
import json, sys
j = json.load(open(sys.argv[1]))["wheels"]
for n, v in j.items(): print(n, v["commit"][:8], v["source"], v["ref"])
PY
  now=$(head_of "$src" "$ref"); [ -z "$now" ] && now=$(git ls-remote "$src" "refs/tags/$ref^{}" 2>/dev/null | cut -c1-8)
  if [ "$commit" = "$now" ]; then ok "$name $commit = remote $ref"; else bad "$name built from $commit, remote $ref is $now"; fi
done
echo "== what is actually installed"
python -I -c "import gafro, gafro_bridge, gafro_robot_interface, openarm_driver, openarm_assets, self_motion_charts, ssik._ssik_native" 2>&1 | tail -1 | grep -q . && bad "an import fails" || ok "imports (incl. ssik native, self_motion_charts)"
pip list 2>/dev/null | grep -i -E "^(ssik|gafro|openarm|self.motion)" | awk '{printf "         %s %s\n",$1,$2}'
t=$(nm -D --defined-only $SP/gafro/libgafro.so | grep -c tinyxml2); y=$(nm -D --defined-only $SP/gafro/libgafro.so | grep -c _ZTIN4YAML13BadConversionE)
[ "$t" -le 1 ] && [ "$y" -ge 1 ] && ok "libgafro: tinyxml2 hidden ($t), YAML::BadConversion exported" || bad "libgafro exports: tinyxml2=$t BadConversion=$y"
D=$SP/openarm_driver/libopenarm_driver.so
[ "$(nm -DC $D | grep -c pollGripper)" -ge 1 ] && ok "driver: gripper poll" || bad "driver LACKS the gripper poll"
note "installed driver is whatever was last pip-installed: the manifest above says what the release built"; [ "$(strings $D | grep -c 'after the link came back')" -ge 1 ] && ok "driver: validated link recovery" || bad "driver LACKS the validated link recovery"
[ "$(nm -DC $D | grep -c 'openarm::can')" -ge 1 ] && ok "driver: CAN backend" || bad "driver has NO CAN backend"
echo "== repos on the robot"
for r in opendubs gafro-ri-ros2-bridge gafro-deploy; do d=~/$r; [ -d $d/.git ] || continue
  git -C $d fetch -q 2>/dev/null; b=$(git -C $d branch --show-current); h=$(git -C $d rev-parse --short=8 HEAD); u=$(git -C $d rev-parse --short=8 origin/main 2>/dev/null)
  behind=$(git -C $d rev-list --count HEAD..origin/main 2>/dev/null); ahead=$(git -C $d rev-list --count origin/main..HEAD 2>/dev/null); dirty=$(git -C $d status -s | grep -v '^??' | wc -l)
  if [ "$behind" != 0 ]; then bad "$r: $b $h is $behind commit(s) BEHIND origin/main ($u)"; elif [ "$ahead" != 0 ]; then note "$r: $b $h is $ahead commit(s) AHEAD of origin/main ($u), unpushed or on a branch"; else ok "$r: $b $h = origin/main"; fi
  [ "$dirty" -gt 0 ] && note "$r: $dirty modified file(s)"
done
n=$(strings /opt/gafro/venv/lib/python3.12/site-packages/gafro_ri_ros2_bridge/_native*.so 2>/dev/null | head -0); b=$(ls -l --time-style=long-iso /opt/gafro/lib/libgafro_ri_ros2_bridge.so | awk '{print $6,$7}'); g=$(ls -l --time-style=long-iso $SP/gafro/libgafro.so | awk '{print $6,$7}')
[ "$b" \> "$g" ] && ok "ros2 bridge lib ($b) is newer than libgafro ($g)" || bad "ros2 bridge lib ($b) is OLDER than libgafro ($g): rebuild it (ABI)"
echo "== services and config"
for u in opendubs-arm@left opendubs-arm@right openarm-can@arm_left openarm-can@arm_right; do note "$u: $(systemctl is-active $u.service) (enabled: $(systemctl is-enabled $u.service 2>&1 | head -1))"; done
for f in openarm-gafro-left.yaml openarm-gafro-right.yaml fastdds-robot.xml; do [ -r /etc/opendubs/$f ] && ok "/etc/opendubs/$f" || bad "missing /etc/opendubs/$f"; done
grep -q "^FASTRTPS_DEFAULT_PROFILES_FILE=" /etc/opendubs/arm-service.env && ok "arm-service.env: DDS profile set" || bad "arm-service.env has no DDS profile (cross-user ROS calls will hang)"
grep -q "^OPENDUBS_ARM_CONFIG=" /etc/opendubs/arm-service.env && note "arm-service.env: OPENDUBS_ARM_CONFIG is set (overrides the per-side files)" || ok "arm-service.env: per-side config files in use"
grep -q "openarm-gafro-\$side.yaml" /usr/local/lib/opendubs/opendubs-arm && ok "wrapper: per-side config fallback" || bad "wrapper is the old one (no per-side config)"
echo "== left arm bus"; ip -details link show arm_left 2>/dev/null | grep -o "can <FD> state [A-Z-]*" | head -1 | sed 's/^/  [ .. ] arm_left  /'; ip -details link show arm_right 2>/dev/null | grep -o "can <FD> state [A-Z-]*" | head -1 | sed 's/^/  [ .. ] arm_right /'
