#!/bin/bash
# capture-pins.sh — compare what a robot's hand-built arm stack runs with the image's manifest.
#
#   capture-pins.sh [SRC_DIR] [SSIK_DIR]      run ON the robot; read-only
#
# SRC_DIR is the host directory mounted at /openarm_ws/src (default: ~/openarm/openarm_robot-code),
# SSIK_DIR the one mounted at /openarm_ws/ssik (default: ~/openarm/ssik). Nothing on the robot is
# changed.
#
# The manifest (repos.lock) takes each repository from its maintainer (arms plan DEC-27), so the
# robot is expected to differ where it runs a student fork. What this answers is everything else:
#   - for each repos.lock entry: the robot's remote and commit, and SAME / DIFFERS against the lock
#     (the PROVISIONAL enactic pins are settled by this);
#   - the robot's workspace root and ssik checkout, which the manifest replaces;
#   - repositories on the robot that the manifest leaves out;
#   - the CAN library and uv versions in the running container, against pins.env.
#
# REFUSES (exit 1) when a repository has uncommitted changes, untracked files included. Those are
# configuration that exists only on this robot, and they must become commits (on the org's copy, or
# upstream) before anything can be compared with them. The offending files are listed so they can be.
set -o pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$HOME/openarm/openarm_robot-code}
SSIK=${2:-$HOME/openarm/ssik}
CONTAINER=openarm
G() { git -c safe.directory='*' -C "$1" "${@:2}"; }

[ -d "$SRC" ] || { echo "capture-pins: no source directory at $SRC" >&2; exit 2; }
problems=0

inspect() {  # inspect NAME DIR [LOCK_COMMIT] -> prints one row
  local name=$1 dir=$2 want=${3:-} url sha dirty verdict
  if [ ! -e "$dir/.git" ]; then
    printf '%-26s %-8s %s\n' "$name" "MISSING" "(no git checkout at $dir)"; return
  fi
  url=$(G "$dir" remote get-url origin 2>/dev/null)
  sha=$(G "$dir" rev-parse HEAD 2>/dev/null)
  # COLCON_IGNORE is the marker setup.sh (and build.sh) puts in every uv member: not configuration.
  dirty=$(G "$dir" status --porcelain 2>/dev/null | grep -v '^?? COLCON_IGNORE$')
  if [ -n "$dirty" ]; then
    echo "capture-pins: $name has uncommitted changes — commit them before comparing:" >&2
    echo "$dirty" | sed 's/^/    /' >&2
    problems=$((problems + 1))
  fi
  if [ -z "$want" ]; then verdict="--"
  elif [ "$sha" = "$want" ]; then verdict=SAME
  else verdict=DIFFERS; fi
  printf '%-26s %-8s %-60s %s\n' "$name" "$verdict" "$url" "$sha"
}

echo "# repos.lock entries: the robot's remote and commit (SAME / DIFFERS from the lock)"
while read -r name _ _ commit; do
  inspect "$name" "$SRC/$name" "$commit"
done < <(sed -n '/^[a-z]/p' "$HERE/repos.lock")

echo
echo "# replaced by the manifest (workspace/ and the ssik uv source), reported for the record"
inspect "workspace-root" "$SRC"
inspect "ssik" "$SSIK"

echo
echo "# on the robot, not in the manifest"
for d in "$SRC"/*/; do
  n=$(basename "$d")
  [ -e "$d/.git" ] || continue
  grep -q "^$n " "$HERE/repos.lock" || inspect "$n" "$d"
done

echo
echo "# the running container, against pins.env"
# shellcheck source=/dev/null
. "$HERE/pins.env"
if sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
  # The library's own version, whether the container has the PPA's package or the source build.
  can=$(sudo docker exec "$CONTAINER" sed -n 's/.*PACKAGE_VERSION "\([^"]*\)".*/\1/p' \
        /usr/lib/aarch64-linux-gnu/cmake/OpenArmCAN/OpenArmCANConfigVersion.cmake 2>/dev/null | head -1)
  uvv=$(sudo docker exec "$CONTAINER" uv --version 2>/dev/null | awk '{print $2}')
  echo "OPENARM_CAN         robot=$can pins.env=1.4.0 tag (commit ${OPENARM_CAN_COMMIT:0:7})"
  echo "UV_VERSION          robot=$uvv pins.env=$UV_VERSION"
else
  echo "# no '$CONTAINER' container on this host"
fi

if [ "$problems" -gt 0 ]; then
  echo "capture-pins: $problems repository(ies) with uncommitted changes — not a faithful record yet" >&2
  exit 1
fi
