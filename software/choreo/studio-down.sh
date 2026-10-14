#!/bin/bash
# Stops the choreography studio (= opendubs-studio down): only what it started, never another
# session's twins. Also stops a studio the earlier version of studio-up.sh started, from the process
# groups it recorded in ~/.cache/opendubs-studio.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OLD="${OPENDUBS_STUDIO_DIR:-$HOME/.cache/opendubs-studio}"
for name in body face composer; do
    f="$OLD/$name.pid"
    [ -f "$f" ] || continue
    pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
        for _ in $(seq 1 25); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
        kill -0 "$pid" 2>/dev/null && { kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null; }
        echo "studio: $name stopped (pid $pid, started by the earlier studio-up.sh)"
    fi
    rm -f "$f"
done
exec "$(command -v uv || echo "$HOME/.local/bin/uv")" run -q "$HERE/studio/studio.py" down "$@"
