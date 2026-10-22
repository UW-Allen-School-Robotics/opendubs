#!/bin/bash
# `openarm-up relink ARM` (the tablet's RESTART LINK) refuses under an energized arm, and otherwise
# restarts that arm's link alone. Off the robot: ip / systemctl / sudo / docker / openarm-motors are
# stubs. Run: bash software/openarm/tests/test_relink.sh
UP=${UP_OVERRIDE:-$(cd "$(dirname "$0")/.." && pwd)/openarm-up}
pass=0; fail=0
setup() {  # setup LEFT_STATE LEFT_ENABLED FIXES(yes|no)
  T=$(mktemp -d); mkdir -p "$T/bin"
  echo "$1" > "$T/arm_left"; echo ERROR-ACTIVE > "$T/arm_right"; echo "$3" > "$T/fixes"; : > "$T/restarts"
  cat > "$T/bin/ip" <<EOF
#!/bin/bash
i=\${@: -1}; [ -f "$T/\$i" ] || exit 1; st=\$(cat "$T/\$i")
echo "5: \$i: <NOARP,UP,LOWER_UP,ECHO> mtu 72 qdisc pfifo_fast state UP"
echo "    can <FD> state \$st (berr-counter tx 0 rx 0) restart-ms 0"
EOF
  cat > "$T/bin/systemctl" <<EOF
#!/bin/bash
[ "\$1" = restart ] || exit 0
i=\${2#openarm-can@}; echo "\$i" >> "$T/restarts"
[ "\$(cat $T/fixes)" = yes ] && echo ERROR-ACTIVE > "$T/\$i"
exit 0
EOF
  cat > "$T/bin/motors" <<EOF
#!/bin/bash
if [ "\$1" = links ]; then
  for i in arm_left arm_right; do echo "\$i present=1 up=1 state=\$(cat $T/\$i) warn=3"; done; exit 0
fi
echo arm_left
for n in 1 2 3 4 5 6 7 8; do
  if [ \$n -le $2 ]; then echo "  id \$n (joint \$n)  ENABLED             pos    +0.00 deg  MOS  25C  rotor  23C"
  else echo "  id \$n (joint \$n)  DISABLED            pos    +0.00 deg  MOS  25C  rotor  23C"; fi
done
echo arm_right
EOF
  printf '#!/bin/bash\nexec "$@"\n' > "$T/bin/sudo"
  printf '#!/bin/bash\nexit 1\n' > "$T/bin/docker"      # no arm stack running
  chmod +x "$T"/bin/*
}
run() { PATH="$T/bin:$PATH" OPENARM_MOTORS="$T/bin/motors" bash "$UP" relink left 2>&1; }
check() {  # check NAME CONDITION
  if eval "$2"; then echo "  ok    $1"; pass=$((pass+1)); else echo "  FAIL  $1"; echo "$out" | sed 's/^/        /'; fail=$((fail+1)); fi
}
setup ERROR-PASSIVE 3 yes; out=$(run)
check "energized arm: refused, the link is not restarted" '[ ! -s "$T/restarts" ] && grep -q "not restarting" <<<"$out"'
setup ERROR-PASSIVE 0 yes; out=$(run)
check "not energized, link in error: restarts the left link only, and it recovers" '[ "$(cat $T/restarts)" = arm_left ] && grep -q "ERROR-ACTIVE" <<<"$out"'
setup ERROR-PASSIVE 0 no; out=$(run)
check "restart does not fix it: says so, naming the cable and power" '[ "$(cat $T/restarts)" = arm_left ] && grep -q "still in error" <<<"$out" && grep -q "CAN cable and power" <<<"$out"'
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
