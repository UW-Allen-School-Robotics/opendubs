#!/bin/bash
# One controller per arm: `openarm-up` refuses to start or activate while the gafro arm service
# (opendubs-arm@<side>) or any other sender commands that arm. (The handover between the two
# stacks is the dashboard's: software/kiosk-dashboard/backend/arm_handover.py and its tests.) Off
# the robot: ip / systemctl / sudo / docker / python3 (gafro's socket) / openarm-motors are stubs.
# Run: bash software/openarm/tests/test_stack_exclusion.sh
UP=${UP_OVERRIDE:-$(cd "$(dirname "$0")/.." && pwd)/openarm-up}
pass=0; fail=0
setup() {  # setup GAFRO_SIDES PARK(ok|refuse) FOREIGN(yes|no)
  T=$(mktemp -d); mkdir -p "$T/bin"; : > "$T/log"
  for s in $1; do : > "$T/gafro_$s"; done
  echo "$2" > "$T/park"; [ "$3" = yes ] && : > "$T/foreign"
  echo "LEFT_STOW=0,0,0,0,0,0,0" > "$T/stow.conf"; echo "RIGHT_STOW=0,0,0,0,0,0,0" >> "$T/stow.conf"
  cat > "$T/bin/systemctl" <<EOF
#!/bin/bash
case "\$1" in
  is-active) u=\${@: -1}; s=\${u#opendubs-arm@}; s=\${s%.service}; [ -f "$T/gafro_\$s" ] ;;
  stop) u=\$2; s=\${u#opendubs-arm@}; s=\${s%.service}; echo "stop \$s" >> "$T/log"; rm -f "$T/gafro_\$s" ;;
  restart) echo "restart \$2" >> "$T/log" ;;
esac
EOF
  cat > "$T/bin/python3" <<EOF
#!/bin/bash
cat >/dev/null; sock=\$3; s=\${sock##*service-}; s=\${s%.sock}; echo "ctl \$s \$4" >> "$T/log"
case "\$4:\$(cat $T/park)" in
  park:ok) echo "park started" ;;
  park:refuse) echo "refused: CH6 (the deadman) is released" ;;
  status:*) echo "\$s arm, hardware: held; last: park done" ;;
  hold:*) echo "holding" ;;
esac
EOF
  cat > "$T/bin/motors" <<EOF
#!/bin/bash
case "\$1" in
  senders) echo "senders \$2" >> "$T/log"
           if [ -f "$T/foreign" ]; then echo "motor 1 (MIT): 120 frames"; exit 1; fi; echo quiet ;;
  disable) echo "disable \${2:-all}" >> "$T/log"; echo "enabled=0 disabled=8 faults=0 missing=0" ;;
  links) for i in arm_left arm_right; do echo "\$i present=1 up=1 state=ERROR-ACTIVE"; done ;;
  summary) echo "enabled=0 disabled=16 faults=0 missing=0" ;;
  *) for i in arm_left arm_right; do echo \$i
       for n in 1 2 3 4 5 6 7 8; do echo "  id \$n (joint \$n)  DISABLED            pos    +0.00 deg  MOS  25C  rotor  23C"; done
     done ;;
esac
EOF
  cat > "$T/bin/ip" <<'EOF'
#!/bin/bash
i=${@: -1}; echo "5: $i: <NOARP,UP,LOWER_UP,ECHO> mtu 72 qdisc pfifo_fast state UP"
echo "    can <FD> state ERROR-ACTIVE (berr-counter tx 0 rx 0) restart-ms 0"
EOF
  printf '#!/bin/bash\nexec "$@"\n' > "$T/bin/sudo"
  # The container exists; the arm stack is not running, and a launch never comes up.
  printf '#!/bin/bash\n[ "$1" = inspect ] && { echo running; exit 0; }\n[ "$1" = exec ] && [ "$2" = -d ] && echo launch >> %s/log\nexit 1\n' "$T" > "$T/bin/docker"
  chmod +x "$T"/bin/*
}
run() { PATH="$T/bin:$PATH" OPENARM_MOTORS="$T/bin/motors" OPENARM_STOW_CONF="$T/stow.conf" bash "$UP" "$@" 2>&1; }
check() {  # check NAME CONDITION
  if eval "$2"; then echo "  ok    $1"; pass=$((pass+1)); else echo "  FAIL  $1"; echo "$out" | sed 's/^/        /'; cat "$T/log" | sed 's/^/        log: /'; fail=$((fail+1)); fi
}

setup left ok no; out=$(run activate left)
check "activate under a gafro unit: refused, naming the handover; nothing launched or restarted" \
  'grep -q "gafro arm service (opendubs-arm@left) is running" <<<"$out" && grep -q "Hand arms to ROS 2" <<<"$out" && ! grep -qE "launch|restart" "$T/log"'
setup right ok no; out=$(run start)
check "bring-up with gafro on the OTHER arm: refused (the launch opens both buses)" \
  'grep -q "opendubs-arm@right" <<<"$out" && ! grep -q launch "$T/log"'
setup "" ok yes; out=$(run activate left)
check "activate while another sender commands the arm: refused, naming what was seen" \
  'grep -q "something else is commanding" <<<"$out" && grep -q "motor 1 (MIT)" <<<"$out" && ! grep -q launch "$T/log"'
setup "" ok no; out=$(run activate left)
check "no other controller: the guard passes (the stub launch then fails, later)" \
  'grep -q launch "$T/log"'
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
