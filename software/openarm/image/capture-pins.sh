#!/bin/bash
# capture-pins.sh — record what a robot's hand-built arm stack actually runs, as pins for the image.
#
#   capture-pins.sh [SRC_DIR] [SSIK_DIR]      run ON the robot; read-only
#
# SRC_DIR is the host directory mounted at /openarm_ws/src (default: ~/openarm/openarm_robot-code),
# SSIK_DIR the one mounted at /openarm_ws/ssik (default: ~/openarm/ssik). Prints a filled-in
# repos.lock and pins.env on stdout; nothing on the robot is changed.
#
# REFUSES (exit 1) when a repository has uncommitted changes, untracked files included. Those are
# configuration that exists only on this robot — the reason this image work exists — and they must
# become commits (on the org's copy, or upstream) before they can be pinned. The offending files are
# listed so they can be.
set -o pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$HOME/openarm/openarm_robot-code}
SSIK=${2:-$HOME/openarm/ssik}
CONTAINER=openarm
G() { git -c safe.directory='*' -C "$1" "${@:2}"; }

[ -d "$SRC" ] || { echo "capture-pins: no source directory at $SRC" >&2; exit 2; }
problems=0

pin() {  # pin NAME KIND DIR
  local name=$1 kind=$2 dir=$3 url sha dirty
  if [ ! -e "$dir/.git" ]; then
    echo "capture-pins: $name: not a git checkout ($dir)" >&2; problems=$((problems + 1)); return
  fi
  url=$(G "$dir" remote get-url origin 2>/dev/null)
  sha=$(G "$dir" rev-parse HEAD 2>/dev/null)
  dirty=$(G "$dir" status --porcelain 2>/dev/null)
  if [ -n "$dirty" ]; then
    echo "capture-pins: $name has uncommitted changes — commit them before pinning:" >&2
    echo "$dirty" | sed 's/^/    /' >&2
    problems=$((problems + 1))
  fi
  printf '%-26s %-6s %-60s %s\n' "$name" "$kind" "$url" "$sha"
}

# The repositories, in repos.lock's order and with its kinds. Read by redirection, not a pipe, so
# the loop runs in this shell and its problem count survives.
while read -r name kind _; do
  case "$kind" in
    top)  pin "$name" "$kind" "$SRC" ;;
    ssik) pin "$name" "$kind" "$SSIK" ;;
    *)    pin "$name" "$kind" "$SRC/$name" ;;
  esac
done < <(sed -n '/^[a-z]/p' "$HERE/repos.lock")

# Repositories in the source tree that repos.lock does not list are not in the image either.
for d in "$SRC"/*/; do
  n=$(basename "$d")
  [ -e "$d/.git" ] || continue
  grep -q "^$n " "$HERE/repos.lock" || { echo "capture-pins: $n is in $SRC but not in repos.lock" >&2; problems=$((problems + 1)); }
done

echo
echo "# pins.env, from the running container"
if sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "OPENARM_CAN_VERSION=$(sudo docker exec "$CONTAINER" dpkg-query -W -f='${Version}' libopenarm-can-dev 2>/dev/null)"
  echo "UV_VERSION=$(sudo docker exec "$CONTAINER" uv --version 2>/dev/null | awk '{print $2}')"
else
  echo "# no '$CONTAINER' container on this host — fill OPENARM_CAN_VERSION and UV_VERSION by hand" >&2
fi

if [ "$problems" -gt 0 ]; then
  echo "capture-pins: $problems problem(s) above — these pins are NOT a faithful record yet" >&2
  exit 1
fi
