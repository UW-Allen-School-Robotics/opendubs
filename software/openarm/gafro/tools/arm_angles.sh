#!/usr/bin/env bash
# READ-ONLY: the seven joint angles of one arm, one line per reading as it arrives, and whether its motors are enabled (STIFF) or not (limp).
# Sends only the health tool's status queries; no enable, disable or command, and never --toggle.
#   bash arm_angles.sh [right|left] [seconds]        (defaults: right, 90)
# Plain bash, not awk: Ubuntu's awk is mawk, which reads a pipe in blocks and printed everything at the end.
side=${1:-right}
secs=${2:-90}
prefix=${ARM_PREFIX:-$HOME/coding/local}
bin="$prefix/bin/openarm-can-health"
[ -x "$bin" ] || { echo "arm_angles: no $bin (set ARM_PREFIX)" >&2; exit 1; }
export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"      # libopenarm_can lives there; a shell without env.sh lacks it
echo "arm_$side for $secs s: each line is j1..j7 in rad; STIFF(n) = n motors enabled, limp = none (Ctrl-C to stop)"

declare -A pos enabled
stdbuf -oL "$bin" "arm_$side" "$secs" 7 | while IFS= read -r line; do
    read -r -a f <<< "$line"                                                   # id pos vel tau mos rtr status ...
    if [[ ${f[0]} =~ ^0x0[1-7]$ ]]; then
        pos[${f[0]}]=${f[1]}
        [[ ${f[6]} == ENABLED ]] && enabled[${f[0]}]=1 || enabled[${f[0]}]=0
        if [[ ${f[0]} == 0x07 ]]; then                                         # the last row of a reading
            n=0
            for k in "${!enabled[@]}"; do (( n += enabled[$k] )); done
            state=limp; (( n )) && state="STIFF($n)"
            printf '%-9s j1 %+.3f  j2 %+.3f  j3 %+.3f  j4 %+.3f  j5 %+.3f  j6 %+.3f  j7 %+.3f\n' "$state" \
                "${pos[0x01]}" "${pos[0x02]}" "${pos[0x03]}" "${pos[0x04]}" "${pos[0x05]}" "${pos[0x06]}" "${pos[0x07]}"
        fi
    fi
done
exit "${PIPESTATUS[0]}"
