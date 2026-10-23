# Source this on the robot's host (`source env.sh`), do not run it: the toolchain, the prefix, the venv and a few helpers for building and first-running
# the arms on the gafro path. Everything is overridable by setting the variable first. It sets no shell options and changes nothing until you call a function.
#
#   ARM_CODING   where everything lives                      (REQUIRED: there is no default layout)
#   ARM_PREFIX   the install prefix of Eigen 5, openarm_can  (default $ARM_CODING/local)
#   ARM_VENV     the venv with gafro, RI and the driver      (default $ARM_CODING/lab/venv)
#   ARM_REPO     the opendubs checkout with software/openarm/gafro  (found under $ARM_CODING if not set)
#   ARM_DRIVER   the openarm-driver checkout                 (found under $ARM_CODING if not set)
#   ARM_RI       the gafro-robot-interface checkout          (found under $ARM_CODING if not set)
#   ARM_HW_CONFIG  this robot's copy of hardware.example.yaml        (default /etc/opendubs/openarm-gafro-hardware.yaml)
#   ARM_ASSETS   the openarm_assets checkout the clearance twin is built from  (no default: arm_lapse/arm_move pass it when set, else the twin looks
#                for $OPENARM_ASSETS, and the step is refused if it finds none)
#   ARM_JOBS     compiler processes, the build cap           (default 2; gafro's files take 1-2 GB each)
#
# `arm_help` lists the functions. The CAN tools are NOT put on PATH: openarm_can installs scripts that rewrite the motors' zero and registers
# next to the read-only ones, so only `arm_can_health` is offered.

if [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "source this file, do not run it:  source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

if [ -z "${ARM_CODING:-}" ]; then
    echo "env.sh: set ARM_CODING to the directory your checkouts and venv live under, then source this again" >&2
    return 1
fi
export ARM_CODING
export ARM_PREFIX="${ARM_PREFIX:-$ARM_CODING/local}"
export ARM_VENV="${ARM_VENV:-$ARM_CODING/lab/venv}"
export ARM_HW_CONFIG="${ARM_HW_CONFIG:-/etc/opendubs/openarm-gafro-hardware.yaml}"
export ARM_ASSETS="${ARM_ASSETS:-}"
export ARM_JOBS="${ARM_JOBS:-2}"

# The commits the lab venv was verified at; a build of anything else is a different test.
ARM_GAFRO_REV="272c223fe7437d04bf0efb65130fab28e24d4694"
ARM_RI_REV="087cab970aa6a9141c4b6340b5a5536483a24465"
# What the driver and the CAN library were built and tested at. openarm_can 1.4.1 is the version the driver's CAN backend has been compiled
# against; the existing stack's image carries 1.4.0 (f340d4b), and the driver has not been tried against it.
ARM_DRIVER_REV="f881bf4d2ad2ad39975a700808d9a95754c47283"
ARM_CAN_VERSION="1.4.1"
ARM_CAN_REV="bfd4e52194aefe38389fa6e670be3738136facb9"

# -- the toolchain: gafro is C++23, which is GCC 14 and up -----------------------------------------------------------------------
if command -v gcc-14 >/dev/null 2>&1 && command -v g++-14 >/dev/null 2>&1; then
    export CC=gcc-14 CXX=g++-14
else
    echo "env.sh: gcc-14 / g++-14 not found (sudo apt install g++-14); gafro will not compile with the system's GCC 13" >&2
fi
export CMAKE_PREFIX_PATH="$ARM_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export CMAKE_BUILD_PARALLEL_LEVEL="$ARM_JOBS"       # what pip's builds read; without it they use every core
export PKG_CONFIG_PATH="$ARM_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$ARM_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# -- the venv ---------------------------------------------------------------------------------------------------------------------
if [ -f "$ARM_VENV/bin/activate" ]; then
    # shellcheck disable=SC1091
    . "$ARM_VENV/bin/activate"
else
    echo "env.sh: no venv at $ARM_VENV yet:  python3 -m venv $ARM_VENV && source $ARM_VENV/bin/activate && pip install -U pip   (then source this again)" >&2
fi

# -- finding things ---------------------------------------------------------------------------------------------------------------
_arm_find() {  # _arm_find <file inside the checkout> <var> <candidate dir>...  -> prints the checkout, or nothing
    local marker=$1 override=$2 d
    shift 2
    for d in "$override" "$@"; do
        [ -n "$d" ] && [ -f "$d/$marker" ] && { echo "$d"; return 0; }
    done
    return 1
}

_arm_repo() {
    local found
    _arm_find software/openarm/gafro/engage.py "${ARM_REPO:-}" "$ARM_CODING/opendubs" "$ARM_CODING/lib/opendubs" "$ARM_CODING/src/opendubs" && return 0
    found=$(find "$ARM_CODING" -maxdepth 5 -path '*/software/openarm/gafro/engage.py' -not -path '*/.git/*' 2>/dev/null | head -1)
    if [ -n "$found" ]; then
        dirname "$(dirname "$(dirname "$(dirname "$found")")")"
        return 0
    fi
    echo "no opendubs checkout with software/openarm/gafro found under $ARM_CODING; set ARM_REPO" >&2
    return 1
}

_arm_driver() {
    local found
    _arm_find CMakeLists.txt "${ARM_DRIVER:-}" "$ARM_CODING/openarm-driver" "$ARM_CODING/lib/openarm-driver" "$ARM_CODING/src/openarm-driver" && return 0
    found=$(find "$ARM_CODING" -maxdepth 3 -type d -name 'openarm-driver*' -not -path '*/.git/*' 2>/dev/null | head -1)
    if [ -n "$found" ] && [ -f "$found/CMakeLists.txt" ]; then
        echo "$found"
        return 0
    fi
    echo "no openarm-driver checkout found under $ARM_CODING; set ARM_DRIVER" >&2
    return 1
}

_arm_ri() {
    local found
    _arm_find bindings/pyproject.toml "${ARM_RI:-}" "$ARM_CODING/src/gafro-robot-interface" "$ARM_CODING/lib/gafro-robot-interface" "$ARM_CODING/gafro-robot-interface" && return 0
    found=$(find "$ARM_CODING" -maxdepth 3 -type d -name 'gafro-robot-interface*' -not -path '*/.git/*' 2>/dev/null | head -1)
    if [ -n "$found" ] && [ -f "$found/bindings/pyproject.toml" ]; then
        echo "$found"
        return 0
    fi
    echo "no gafro-robot-interface checkout found under $ARM_CODING; set ARM_RI" >&2
    return 1
}

_arm_gafro_dir() {  # the gafro wheel ships its CMake config inside the package
    python -c "import gafro, os; print(os.path.join(os.path.dirname(gafro.__file__), 'cmake'))"
}

_arm_need_venv() {
    [ -n "${VIRTUAL_ENV:-}" ] || { echo "no venv active (source this file after creating $ARM_VENV)" >&2; return 1; }
}

_arm_confirm() {  # _arm_confirm <what is about to happen>
    echo "$1"
    printf 'The arm is supported, a person is at the e-stop, the other arm is clear, and head tracking is OFF with the neck centred? [y/N] '
    local answer
    read -r answer
    [ "$answer" = "y" ] || { echo "not done"; return 1; }
}

# -- building ---------------------------------------------------------------------------------------------------------------------
arm_install_gafro() {  # long: the C++ build; run it in tmux
    _arm_need_venv || return 1
    nice -n 10 pip install -v "git+https://github.com/personalrobotics/gafro.git@$ARM_GAFRO_REV#subdirectory=bindings/python"
}

arm_install_ri() {
    _arm_need_venv || return 1
    nice -n 10 pip install -v "git+https://github.com/personalrobotics/gafro-robot-interface.git@$ARM_RI_REV#subdirectory=bindings"
}

arm_build_can() {  # openarm_can at its pin, into the prefix. Needs libcli11-dev (sudo apt install libcli11-dev). Installs ITS tools, some of which rewrite the motors: they stay off PATH.
    local src=${ARM_CAN_SRC:-$ARM_CODING/lib/openarm_can}
    [ -d "$src/.git" ] || { echo "no openarm_can checkout at $src (git clone https://github.com/enactic/openarm_can there, or set ARM_CAN_SRC)" >&2; return 1; }
    git -C "$src" checkout --quiet "$ARM_CAN_REV" || return 1
    rm -rf "$src/build"
    cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$ARM_PREFIX" -DCMAKE_PREFIX_PATH="$ARM_PREFIX" || return 1
    nice -n 10 cmake --build "$src/build" -j "$ARM_JOBS" || return 1
    cmake --install "$src/build"
}

arm_build_ri_cxx() {  # RI's C++ library, headers and cmake config into the prefix, for the driver: the wheel installs none of them
    _arm_need_venv || return 1
    local src gdir
    src=$(_arm_ri) || return 1
    gdir=$(_arm_gafro_dir) || return 1
    cmake -S "$src" -B "$src/build-cxx" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$ARM_PREFIX" -Dgafro_DIR="$gdir" \
        -DROBOT_INTERFACE_BUILD_PYTHON=OFF -DROBOT_INTERFACE_BUILD_TESTS=OFF \
        -DROBOT_INTERFACE_DRIVER_UR=OFF -DROBOT_INTERFACE_DRIVER_XARM=OFF -DROBOT_INTERFACE_DRIVER_FRANKA=OFF || return 1
    nice -n 10 cmake --build "$src/build-cxx" -j "$ARM_JOBS" || return 1
    cmake --install "$src/build-cxx"
}

arm_build_driver() {  # configure, build at the cap, install into the venv
    _arm_need_venv || return 1
    local src site gdir head
    src=$(_arm_driver) || return 1
    head=$(git -C "$src" rev-parse HEAD 2>/dev/null)
    if [ "$head" != "$ARM_DRIVER_REV" ] && [ -z "${ARM_ALLOW_UNPINNED:-}" ]; then
        echo "arm_build_driver: $src is at ${head:-no git checkout}, not the pinned $ARM_DRIVER_REV (set ARM_ALLOW_UNPINNED=1 to build it anyway)" >&2
        return 1
    fi
    site=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") || return 1
    gdir=$(_arm_gafro_dir) || return 1
    cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -Dgafro_DIR="$gdir" -DOPENARM_DRIVER_CAN=ON -DOPENARM_DRIVER_PYTHON_DIR="$site" || return 1
    nice -n 10 cmake --build "$src/build" -j "$ARM_JOBS" || return 1
    cmake --install "$src/build" --prefix "$ARM_PREFIX"   # not the default /usr/local, which needs root; the Python package goes to site-packages regardless
}

arm_test_driver() {  # the tests are timing-based: run it a few times
    local src
    src=$(_arm_driver) || return 1
    ctest --test-dir "$src/build" --output-on-failure
}

# -- looking ----------------------------------------------------------------------------------------------------------------------
arm_doctor() {  # what is installed, at which version, and whether it is what was verified
    local v
    v=$(g++-14 --version 2>/dev/null | head -1);                                                echo "gcc-14      : ${v:-missing}"
    echo "cmake       : $(cmake --version | head -1)"
    echo "python      : $(python --version 2>&1) ($(command -v python))"
    echo "venv        : ${VIRTUAL_ENV:-none}"
    v=$(grep -h 'PACKAGE_VERSION ' "$ARM_PREFIX"/share/eigen3/cmake/Eigen3ConfigVersion.cmake 2>/dev/null | head -1); echo "Eigen       : ${v:-missing} (want 5.0.1)"
    v=$(pkg-config --modversion openarm-can 2>/dev/null);                                       echo "openarm_can : ${v:-missing} (want $ARM_CAN_VERSION)$([ -n "$v" ] && [ "$v" != "$ARM_CAN_VERSION" ] && echo '  <- NOT the version the driver was built against')"
    local drv dirty
    if drv=$(_arm_driver 2>/dev/null); then
        v=$(git -C "$drv" rev-parse HEAD 2>/dev/null); dirty=$(git -C "$drv" status --porcelain 2>/dev/null | head -1)
        echo "driver src  : ${v:0:8} ($([ "$v" = "$ARM_DRIVER_REV" ] && echo 'the pinned commit' || echo "NOT the pinned ${ARM_DRIVER_REV:0:8}"))${dirty:+, with uncommitted changes} at $drv"
    else
        echo "driver src  : no checkout found"
    fi
    local dist pin
    for dist in gafro:$ARM_GAFRO_REV gafro-robot-interface:$ARM_RI_REV; do
        pin=${dist##*:}
        python - "${dist%%:*}" "$pin" <<'PY'
import importlib.metadata as md, json, subprocess, sys, urllib.parse
name, pin = sys.argv[1], sys.argv[2]
try:
    dist = md.distribution(name)
except md.PackageNotFoundError:
    import importlib.util
    found = importlib.util.find_spec(name.replace("-", "_"))
    print(f"{name}: " + (f"imports from {found.origin}, but there is no pip metadata (a cmake install?), so the commit is not recorded"
                         if found else "not installed in this python"))
    raise SystemExit
origin = json.loads(dist.read_text("direct_url.json") or "{}")
commit, where = origin.get("vcs_info", {}).get("commit_id"), origin.get("url", "an index")
if commit is None and where.startswith("file://"):                  # built from a local clone: its checkout says which commit
    path = urllib.parse.unquote(urllib.parse.urlparse(where).path)
    for up in (path, path.rsplit("/", 1)[0]):
        try:
            commit = subprocess.check_output(["git", "-C", up, "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip(); break
        except Exception:
            pass
verdict = "unknown commit" if commit is None else ("the verified commit" if commit == pin else f"NOT the verified {pin[:8]}")
print(f"{name} {dist.version}: {(commit or '?')[:8]} ({verdict}), from {where}")
PY
    done
    for mod in gafro gafro_robot_interface openarm_driver; do
        echo "import ${mod}: $(python -c "import ${mod}; print(getattr(${mod}, '__file__', 'ok'))" 2>&1 | tail -1)"
    done
    echo "CAN links   :"; ip -br link show type can 2>/dev/null | sed 's/^/              /' || true
    echo "memory      : $(free -g | awk '/^Mem:/ {print $7 " GB available of " $2}')   compilers running: $(pgrep -c cc1plus || true)"
}

arm_can_health() {  # openarm-can-health, read-only (never pass --toggle: it enables and disables the motors)
    "$ARM_PREFIX/bin/openarm-can-health" "$@"
}

# -- first runs (each asks first: they enable or move an arm) ----------------------------------------------------------------------
_arm_bringup() {  # _arm_bringup <stage> <side> [args]
    local repo stage=$1 side=$2
    repo=$(_arm_repo) || return 1
    shift 2
    python "$repo/software/openarm/gafro/bringup.py" "$stage" --side "$side" --model "$repo/description/openarm/gafro/openarm_bimanual.xml" \
        --hardware-config "$ARM_HW_CONFIG" "$@"
}

arm_hold()  { _arm_confirm "Stage 3: enables the $1 arm's motors and holds where it is for ${2:-20} s, no permit, nothing commanded." && _arm_bringup hold "$1" --seconds "${2:-20}"; }
# A step has no default joint, size or direction: the right direction depends on what the arm stands next to. bringup.py checks the path in the lane's twin,
# prints the plan with the arm's real pose, and starts only when the joint and direction are typed back (3+ or 3-).
_arm_step_usage() { echo "usage: $1 <left|right> <joint 1-7> <step in rad WITH A SIGN, e.g. +0.01 or -0.01>$2" >&2; return 1; }
arm_lapse() {  # arm_lapse <side> <joint> <signed rad> [speed rad/s, default 0.03] [after s, default 0.3]
    [ $# -ge 3 ] || { _arm_step_usage arm_lapse " [speed] [after]"; return 1; }
    _arm_confirm "Stage 4: moves the $1 arm's joint $2 by $3 rad slowly, then stops renewing the permit; the arm must stop where it is. HOLD CH6 (the deadman) from when you type the sign back until it ends." \
        && _arm_bringup lapse "$1" --supervised --joint "$2" --delta "$3" ${4:+--speed "$4"} ${5:+--after "$5"} ${ARM_ASSETS:+--assets "$ARM_ASSETS"}
}
arm_move() {  # arm_move <side> <joint> <signed rad> [speed rad/s]
    [ $# -ge 3 ] || { _arm_step_usage arm_move " [speed]"; return 1; }
    _arm_confirm "Stage 5: moves the $1 arm's joint $2 by $3 rad and back. HOLD CH6 (the deadman) from when you type the sign back until it ends." \
        && _arm_bringup move "$1" --supervised --joint "$2" --delta "$3" ${4:+--speed "$4"} ${ARM_ASSETS:+--assets "$ARM_ASSETS"}
}

_arm_engage() {  # _arm_engage <side> [engage.py args]
    local repo side=$1
    repo=$(_arm_repo) || return 1
    shift
    python "$repo/software/openarm/gafro/engage.py" --model "$repo/description/openarm/gafro/openarm_bimanual.xml" --backend hardware \
        --hardware-config "$ARM_HW_CONFIG" --side "$side" "$@"
}

arm_engage() { _arm_confirm "Moves the $1 arm from where it is to the ready pose (11 s from stow), then holds it under track. HOLD CH6 (the deadman) from now until it ends." &&_arm_engage "$1" --supervised --speed "${2:-0.15}"; }
arm_park()   { _arm_confirm "Moves the $1 arm to the stow pose of /etc/opendubs/openarm-stow.conf and holds it. HOLD CH6 (the deadman) from now until it ends." &&_arm_engage "$1" --supervised --park --speed "${2:-0.15}"; }

arm_help() {
    cat <<'EOF'
building   arm_build_can | arm_install_gafro | arm_install_ri | arm_build_ri_cxx | arm_build_driver | arm_test_driver   (in that order)
looking    arm_doctor | arm_can_health [args]
first run  arm_hold <side> [seconds]        stage 3: enabled and held, nothing commanded
           arm_lapse <side> <joint> <+/-rad> [speed] [after]   stage 4: the permit stops being renewed mid-move (no default step or direction)
           arm_move <side> <joint> <+/-rad> [speed]            stage 5: one joint a small step and back (no default step or direction)
           arm_engage <side|both> [rad/s]   stage 6/7: to the ready pose, then track (default 0.15 rad/s)
           arm_park <side|both> [rad/s]     back to the stow pose
Each first-run function asks first. <side> is left or right. Variables: ARM_CODING ARM_PREFIX ARM_VENV ARM_REPO ARM_DRIVER ARM_HW_CONFIG ARM_JOBS.
EOF
}
