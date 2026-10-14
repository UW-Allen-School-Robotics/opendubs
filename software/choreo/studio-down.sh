#!/bin/bash
# Stops what ./studio-up.sh started — the MuJoCo twin, the face twin and the composer — and nothing
# else: only the process groups it recorded in $OPENDUBS_STUDIO_DIR, never a process found by name
# or port (another session's twins may be running on this machine).
set -u
DIR="${OPENDUBS_STUDIO_DIR:-$HOME/.cache/opendubs-studio}"
stopped=0
for name in body face composer; do
    f="$DIR/$name.pid"
    [ -f "$f" ] || continue
    pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
        for _ in $(seq 1 25); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.2
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
        fi
        echo "studio: $name stopped (pid $pid)"
        stopped=1
    fi
    rm -f "$f"
done
[ "$stopped" = 1 ] || echo "studio: nothing of ours was running"
