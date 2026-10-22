# Source this on the robot's host (`source env.sh`), do not run it: the toolchain, the prefix, the venv and a few helpers for building and first-running
# the arms on the gafro path. Everything is overridable by setting the variable first. It sets no shell options and changes nothing until you call a function.
#
#   ARM_CODING   where everything lives                      (default ~/coding)
#   ARM_PREFIX   the install prefix of Eigen 5, openarm_can  (default $ARM_CODING/local)
#   ARM_VENV     the venv with gafro, RI and the driver      (default $ARM_CODING/lab/venv)
#   ARM_REPO     the opendubs checkout with software/openarm/gafro  (found under $ARM_CODING if not set)
#   ARM_DRIVER   the openarm-driver checkout                 (found under $ARM_CODING if not set)
#   ARM_HW_CONFIG  this robot's copy of hardware.example.yaml        (default /etc/opendubs/openarm-gafro-hardware.yaml)
#   ARM_JOBS     compiler processes, the build cap           (default 2; gafro's files take 1-2 GB each)
#
# `arm_help` lists the functions. The CAN tools are NOT put on PATH: openarm_can installs scripts that rewrite the motors' zero and registers
# next to the read-only ones, so only `arm_can_health` is offered.

if [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "source this file, do not run it:  source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

export ARM_CODING="${ARM_CODING:-$HOME/coding}"
export ARM_PREFIX="${ARM_PREFIX:-$ARM_CODING/local}"
export ARM_VENV="${ARM_VENV:-$ARM_CODING/lab/venv}"
export ARM_HW_CONFIG="${ARM_HW_CONFIG:-/etc/opendubs/openarm-gafro-hardware.yaml}"
export ARM_JOBS="${ARM_JOBS:-2}"

# The commits the lab venv was verified at; a build of anything else is a different test.
ARM_GAFRO_REV="272c223fe7437d04bf0efb65130fab28e24d4694"
ARM_RI_REV="087cab970aa6a9141c4b6340b5a5536483a24465"

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

_arm_need_venv() {
    [ -n "${VIRTUAL_ENV:-}" ] || { echo "no venv active (source this file after creating $ARM_VENV)" >&2; return 1; }
}

_arm_confirm() {  # _arm_confirm <what is about to happen>
    echo "$1"
    printf 'The arm is supported, a person is at the e-stop, and the other arm is clear? [y/N] '
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

arm_build_driver() {  # configure, build at the cap, install into the venv
    _arm_need_venv || return 1
    local src site
    src=$(_arm_driver) || return 1
    site=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") || return 1
    cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -DOPENARM_DRIVER_CAN=ON -DOPENARM_DRIVER_PYTHON_DIR="$site" || return 1
    nice -n 10 cmake --build "$src/build" -j "$ARM_JOBS" || return 1
    cmake --install "$src/build"
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
    v=$(pkg-config --modversion openarm-can 2>/dev/null);                                       echo "openarm_can : ${v:-missing} (want 1.4.1)"
    local pkg pin got
    for pkg in gafro:$ARM_GAFRO_REV gafro_robot_interface:$ARM_RI_REV; do
        got=$(cat "$VIRTUAL_ENV"/lib/python*/site-packages/"${pkg%%:*}"-*.dist-info/direct_url.json 2>/dev/null | python -c "import sys,json; print(json.load(sys.stdin)['vcs_info']['commit_id'])" 2>/dev/null)
        pin=${pkg##*:}
        echo "${pkg%%:*}: ${got:-not installed}$([ -n "$got" ] && { [ "$got" = "$pin" ] && echo ' (the verified commit)' || echo " (NOT the verified $pin)"; })"
    done
    echo "driver      : $(python -c 'import gafro, gafro_robot_interface, openarm_driver; print("imports")' 2>&1 | tail -1)"
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
arm_lapse() { _arm_confirm "Stage 4: moves the $1 arm's joint ${2:-3} slowly by ${3:-0.05} rad, then stops renewing the permit." && _arm_bringup lapse "$1" --joint "${2:-3}" --delta "${3:-0.05}"; }
arm_move()  { _arm_confirm "Stage 5: moves the $1 arm's joint ${2:-3} by ${3:-0.05} rad and back." && _arm_bringup move "$1" --joint "${2:-3}" --delta "${3:-0.05}"; }

_arm_engage() {  # _arm_engage <side> [engage.py args]
    local repo side=$1
    repo=$(_arm_repo) || return 1
    shift
    python "$repo/software/openarm/gafro/engage.py" --model "$repo/description/openarm/gafro/openarm_bimanual.xml" --backend hardware \
        --hardware-config "$ARM_HW_CONFIG" --side "$side" "$@"
}

arm_engage() { _arm_confirm "Moves the $1 arm from where it is to the ready pose (11 s from stow), then holds it under track." && _arm_engage "$1" --speed "${2:-0.15}"; }
arm_park()   { _arm_confirm "Moves the $1 arm to the stow pose of /etc/opendubs/openarm-stow.conf and holds it." && _arm_engage "$1" --park --speed "${2:-0.15}"; }

arm_help() {
    cat <<'EOF'
building   arm_install_gafro | arm_install_ri | arm_build_driver | arm_test_driver
looking    arm_doctor | arm_can_health [args]
first run  arm_hold <side> [seconds]        stage 3: enabled and held, nothing commanded
           arm_lapse <side> [joint] [rad]   stage 4: the permit stops being renewed mid-move
           arm_move <side> [joint] [rad]    stage 5: one joint a small step and back
           arm_engage <side|both> [rad/s]   stage 6/7: to the ready pose, then track (default 0.15 rad/s)
           arm_park <side|both> [rad/s]     back to the stow pose
Each first-run function asks first. <side> is left or right. Variables: ARM_CODING ARM_PREFIX ARM_VENV ARM_REPO ARM_DRIVER ARM_HW_CONFIG ARM_JOBS.
EOF
}
