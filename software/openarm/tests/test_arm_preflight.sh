#!/bin/bash
# opendubs-arm-preflight (software/openarm/opendubs-arm-preflight), off the robot: ip / systemctl / pgrep are stubs, the bundle and the DDS profile are a temp tree.
# What it must do: refuse the old stack (2) and a missing bundle or DDS profile (78) before touching a link, leave a healthy link alone, restart only this arm's link when it is in
# error and go on once it is ERROR-ACTIVE, and refuse (1) naming the fix when a restart does not bring it back. Run: bash software/openarm/tests/test_arm_preflight.sh
PRE=$(cd "$(dirname "$0")/.." && pwd)/opendubs-arm-preflight
pass=0; fail=0
setup() {  # setup LEFT_STATE RIGHT_STATE FIXES(yes|no) OLD_STACK(yes|no)
  T=$(mktemp -d); mkdir -p "$T/bin" "$T/bundle/software/openarm/gafro" "$T/bundle/description/openarm/gafro" "$T/bundle/sim"
  touch "$T/bundle/software/openarm/gafro/arm_service.py" "$T/bundle/description/openarm/gafro/openarm_bimanual.xml" "$T/bundle/sim/clearance.py" "$T/profile.xml"
  echo "$1" > "$T/arm_left"; echo "$2" > "$T/arm_right"; echo "$3" > "$T/fixes"; echo "$4" > "$T/old"; : > "$T/restarts"
  cat > "$T/bin/ip" <<STUB
#!/bin/bash
i=\${@: -1}; [ -f "$T/\$i" ] || exit 1; st=\$(cat "$T/\$i"); [ "\$st" = MISSING ] && exit 1
echo "5: \$i: <NOARP,UP,LOWER_UP,ECHO> mtu 72 qdisc pfifo_fast state UP"
echo "    can <FD> state \$st (berr-counter tx 0 rx 0) restart-ms 0"
STUB
  cat > "$T/bin/systemctl" <<STUB
#!/bin/bash
[ "\$1" = restart ] || exit 0
for u in "\${@:2}"; do i=\${u#openarm-can@}; echo "\$i" >> "$T/restarts"
  [ "\$(cat $T/fixes)" = yes ] && echo ERROR-ACTIVE > "$T/\$i"; done
exit 0
STUB
  printf '#!/bin/bash\n[ "$(cat %s/old)" = yes ]\n' "$T" > "$T/bin/pgrep"
  printf '#!/bin/bash\nexit 0\n' > "$T/bin/sleep"
  chmod +x "$T"/bin/*
}
run() {  # run SIDE
  out=$(PATH="$T/bin:$PATH" OPENDUBS_ARM_BUNDLE="$T/bundle" OPENDUBS_DDS_PROFILE="$T/profile.xml" OPENDUBS_LINK_WAIT=1 bash "$PRE" "$1" 2>&1); rc=$?
}
check() {  # check NAME CONDITION
  if eval "$2"; then echo "  ok    $1"; pass=$((pass+1)); else echo "  FAIL  $1 (rc=$rc)"; echo "$out" | sed 's/^/        /'; fail=$((fail+1)); fi
}
setup ERROR-ACTIVE ERROR-ACTIVE yes no; run left
check "healthy link: exit 0, no restart" '[ $rc = 0 ] && [ ! -s "$T/restarts" ]'
setup ERROR-WARNING ERROR-ACTIVE yes no; run left
check "left in ERROR-WARNING: only the left link is restarted, exit 0" '[ $rc = 0 ] && [ "$(cat $T/restarts)" = arm_left ] && grep -q "ERROR-ACTIVE again" <<<"$out"'
setup BUS-OFF BUS-OFF yes no; run right
check "right BUS-OFF: the left link is left alone" '[ $rc = 0 ] && [ "$(cat $T/restarts)" = arm_right ]'
setup BUS-OFF ERROR-ACTIVE no no; run left
check "a restart that does not help: exit 1 naming power and connectors" '[ $rc = 1 ] && grep -q "e-stop" <<<"$out" && grep -q "connector" <<<"$out"'
setup MISSING ERROR-ACTIVE yes no; run left
check "missing interface: exit 1, not restarted" '[ $rc = 1 ] && [ ! -s "$T/restarts" ] && grep -q "does not exist" <<<"$out"'
setup BUS-OFF ERROR-ACTIVE yes yes; run left
check "old stack running: exit 2 before any link is touched" '[ $rc = 2 ] && [ ! -s "$T/restarts" ] && grep -q "openarm-up down" <<<"$out"'
setup ERROR-ACTIVE ERROR-ACTIVE yes no; rm "$T/bundle/sim/clearance.py"; run left
check "bundle incomplete: exit 78 naming the file" '[ $rc = 78 ] && grep -q "sim/clearance.py" <<<"$out"'
setup BUS-OFF ERROR-ACTIVE yes no; rm "$T/profile.xml"; run left
check "no DDS profile: exit 78, link not touched" '[ $rc = 78 ] && [ ! -s "$T/restarts" ] && grep -q "DDS profile" <<<"$out"'
setup ERROR-ACTIVE ERROR-ACTIVE yes no; run middle
check "a bad side: exit 64" '[ $rc = 64 ]'
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
