#!/usr/bin/env bash
# Bench the deploy-all.sh rosbridge listener check with a fake ss and robot.env, on the Mac; not in CI.
# Run from the repo root: bash software/ros/tools/rosbridge-bind-bench.sh .
repo=$1; tmp=$(mktemp -d)
body=$(awk "/^ssh \"\\\$HOST\" 'bash -s' <<'EOF'/{f=1;next} f&&/^EOF\$/{exit} f" "$repo/deploy-all.sh" | sed "s#/etc/opendubs/robot.env#$tmp/robot.env#; s#sleep 0.5#:#")
run() {  # $1=label $2=ss local addresses (space-separated) $3=robot.env contents $4=expected exit
  printf '%s' "$3" > "$tmp/robot.env"
  out=$(LISTEN="$2" bash -c "ss() { for a in \$LISTEN; do echo \"LISTEN 0 4096 \$a 0.0.0.0:*\"; done; }; $body" 2>&1); rc=$?
  [ "$rc" = "$4" ] && r=PASS || r=FAIL; printf '%s %-28s rc=%s  %s\n' "$r" "$1" "$rc" "$(echo "$out" | tr '\n' ' ')"
}
run "default loopback"      "127.0.0.1:9090"              ""  0
run "wildcard v4"           "0.0.0.0:9090"                ""  1
run "wildcard v6"           "[::]:9090"                   ""  1
run "star"                  "*:9090"                      ""  1
run "LAN configured"        "10.42.2.10:9090"             $'OPENDUBS_ROSBRIDGE_ADDRESS=10.42.2.10\nOPENDUBS_ROSBRIDGE_URL=ws://10.42.2.10:9090\n' 0
run "LAN quoted values"     "10.42.2.10:9090"             $'OPENDUBS_ROSBRIDGE_ADDRESS="10.42.2.10"\nOPENDUBS_ROSBRIDGE_URL="ws://10.42.2.10:9090"\n' 0
run "LAN, URL unset"        "10.42.2.10:9090"             $'OPENDUBS_ROSBRIDGE_ADDRESS=10.42.2.10\n' 1
run "other iface, not set"  "192.168.55.1:9090"           ""  1
run "other iface vs LAN"    "192.168.55.1:9090"           $'OPENDUBS_ROSBRIDGE_ADDRESS=10.42.2.10\nOPENDUBS_ROSBRIDGE_URL=ws://10.42.2.10:9090\n' 1
run "not listening"         ""                            ""  1
run "loopback v6 + v4"      "127.0.0.1:9090 [::1]:9090"   ""  0
rm -rf "$tmp"
