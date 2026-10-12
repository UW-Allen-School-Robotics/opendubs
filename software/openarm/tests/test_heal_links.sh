#!/bin/bash
# openarm-up heals a bus-off arm CAN link before starting (arms plan A7), off the robot: its CAN
# step runs against stubbed ip / systemctl / sudo / docker. Run: bash software/openarm/tests/test_heal_links.sh
UP=${UP_OVERRIDE:-$(cd "$(dirname "$0")/.." && pwd)/openarm-up}
pass=0; fail=0
setup() {  # setup LEFT_STATE RIGHT_STATE FIXES(yes|no)
  T=$(mktemp -d); mkdir -p "$T/bin"
  echo "$1" > "$T/arm_left"; echo "$2" > "$T/arm_right"; echo "$3" > "$T/fixes"; : > "$T/restarts"
  cat > "$T/bin/ip" <<EOF
#!/bin/bash
i=\${@: -1}; [ -f "$T/\$i" ] || exit 1; st=\$(cat "$T/\$i"); [ "\$st" = MISSING ] && exit 1
echo "5: \$i: <NOARP,UP,LOWER_UP,ECHO> mtu 72 qdisc pfifo_fast state UP"
echo "    can <FD> state \$st (berr-counter tx 0 rx 0) restart-ms 0"
EOF
  cat > "$T/bin/systemctl" <<EOF
#!/bin/bash
[ "\$1" = restart ] || exit 0
for u in "\${@:2}"; do i=\${u#openarm-can@}; echo "\$i" >> "$T/restarts"
  [ "\$(cat $T/fixes)" = yes ] && echo ERROR-ACTIVE > "$T/\$i"; done
EOF
  printf '#!/bin/bash\nexec "$@"\n' > "$T/bin/sudo"
  printf '#!/bin/bash\nexit 1\n' > "$T/bin/docker"      # stops the run right after the CAN step
  chmod +x "$T"/bin/*
}
run() { PATH="$T/bin:$PATH" bash "$UP" start 2>&1; }
check() {  # check NAME CONDITION
  if eval "$2"; then echo "  ok    $1"; pass=$((pass+1)); else echo "  FAIL  $1"; echo "$out" | sed 's/^/        /'; fail=$((fail+1)); fi
}
setup ERROR-ACTIVE ERROR-ACTIVE yes; out=$(run)
check "healthy links: no restart, goes on to the container" '[ ! -s "$T/restarts" ] && grep -q "no .openarm. container" <<<"$out"'
setup BUS-OFF ERROR-ACTIVE yes; out=$(run)
check "left bus-off, restart fixes it: restarts only the left, goes on" '[ "$(cat $T/restarts)" = arm_left ] && grep -q "restarting the link" <<<"$out" && grep -q "no .openarm. container" <<<"$out"'
setup BUS-OFF ERROR-PASSIVE no; out=$(run)
check "restart does not fix: one restart each, then the fix-naming refusal" '[ "$(sort $T/restarts | tr "\n" " ")" = "arm_left arm_right " ] && grep -q "CAN connectors" <<<"$out" && ! grep -q "no .openarm. container" <<<"$out"'
setup MISSING ERROR-ACTIVE yes; out=$(run)
check "missing interface: not restarted, reported as MISSING" '[ ! -s "$T/restarts" ] && grep -q "MISSING" <<<"$out" && grep -q "CAN links not ready" <<<"$out"'
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
