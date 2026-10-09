#!/usr/bin/env bash
# CONSTRUCT THE DEMO TREE, verifiably, in one command.
#
# This replaces the four-step manual recipe in README.md, which was correct but which nobody can
# follow without hitting at least one of the traps below. Every check in this script is here
# because the thing it checks for actually happened, on a clean run, on 2026-10-07:
#
#   1. `fetch_repos.py` SILENTLY SKIPS REPOS. The README already warned about this and it still
#      cost a round: after a clean run, `dependencies/lvgl` contained a bare `.git` and nothing
#      else -- 19 files where there should be ~2980 -- `smooth_ui_toolkit` was absent entirely, and
#      the script printed NOT ONE LINE about either. A device build does not notice, because that
#      uses the LVGL managed component from the registry; the desktop build fails at configure time
#      with `add_subdirectory given source "dependencies/lvgl" which is not an existing directory`,
#      which names the symptom and not the cause.
#
#      So this script does not use fetch_repos.py. It fetches each dependency at its pin and then
#      COUNTS THE FILES, because "the directory exists" is not the same as "the clone worked".
#
#   2. `git clone` OF A LARGE REPO FAILS ON A BUSY CONNECTION. `fatal: fetch-pack: invalid
#      index-pack output` on lvgl, which is exactly the failure the README documents for esp-idf.
#      Release tarballs have no pack negotiation, so they do not fail this way. lvgl comes from a
#      tarball for that reason.
#
#   3. The launcher subtraction is 14.8 MB of the 15 MB of assets, and it must happen before a
#      build or the image does not fit. Idempotent here, so re-running is safe.
#
# Usage:  tools/setup-demo-tree.sh [target-dir]
#         default target: ../../../M5Tab5-UserDemo relative to this repo (a sibling of the repo)
#
# Afterwards:  export OPENDUBS_DEMO=<target-dir>
#              ./build.sh desktop-build     # no board and no IDF needed
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$(cd "$HERE/../../.." && pwd)/M5Tab5-UserDemo}"

# PINNED, not "latest". The demo tree is upstream code we subtract from; an unpinned fetch means
# the tree under the build changes without a commit in this repo saying so.
DEMO_REPO="https://github.com/m5stack/M5Tab5-UserDemo.git"
DEMO_COMMIT="b4e356bc491ca070d54004718dad789c07d5fc93"   # 2026-09-15, the tree these deltas were built against

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# ---- 1. the demo tree ------------------------------------------------------------------------
if [ -d "$TARGET/.git" ]; then
    say "demo tree already at $TARGET -- leaving it alone"
else
    say "cloning the demo tree into $TARGET"
    git clone "$DEMO_REPO" "$TARGET"
    ( cd "$TARGET" && git checkout --quiet "$DEMO_COMMIT" )
fi
[ -f "$TARGET/app/hal/hal.h" ] || die "$TARGET does not look like the demo tree (no app/hal/hal.h)"

# ---- 2. dependencies, each one VERIFIED -----------------------------------------------------
# name | kind | ref | url | the least plausible file count for a working checkout
DEPS="
mooncake|git|v2.1.0|https://github.com/Forairaaaaa/mooncake.git|10
mooncake_log|git|v1.0.0|https://github.com/Forairaaaaa/mooncake_log.git|5
smooth_ui_toolkit|git|v2.0.0|https://github.com/Forairaaaaa/smooth_ui_toolkit.git|30
lvgl|tar|v9.2.2|https://github.com/lvgl/lvgl/archive/refs/tags/v9.2.2.tar.gz|1000
"

mkdir -p "$TARGET/dependencies"
echo "$DEPS" | while IFS='|' read -r name kind ref url minfiles; do
    [ -n "$name" ] || continue
    dir="$TARGET/dependencies/$name"
    have=0
    [ -d "$dir" ] && have="$(find "$dir" -path '*/.git' -prune -o -type f -print 2>/dev/null | wc -l | tr -d ' ')"
    if [ "$have" -ge "$minfiles" ]; then
        printf '   ok   %-18s %s files\n' "$name" "$have"
        continue
    fi
    say "fetching $name $ref (had $have files, need >= $minfiles)"
    rm -rf "$dir"
    if [ "$kind" = "tar" ]; then
        # No pack negotiation, so a busy connection cannot produce an invalid-index-pack failure.
        tmp="$(mktemp -d)/d.tar.gz"
        curl -fsSL -o "$tmp" "$url"
        mkdir -p "$dir"
        tar xzf "$tmp" -C "$dir" --strip-components=1
        rm -rf "$(dirname "$tmp")"
    else
        git clone --quiet --depth 1 -b "$ref" "$url" "$dir"
    fi
    got="$(find "$dir" -path '*/.git' -prune -o -type f -print 2>/dev/null | wc -l | tr -d ' ')"
    # THE WHOLE POINT. A fetch that "succeeded" into an empty directory is the documented failure.
    [ "$got" -ge "$minfiles" ] || die "$name fetched only $got files, expected >= $minfiles"
    printf '   ok   %-18s %s files (fetched)\n' "$name" "$got"
done

# ---- 3. our deltas, idempotent ---------------------------------------------------------------
say "subtracting the demo launcher (14.8 MB of the 15 MB of assets)"
rm -rf "$TARGET/app/apps/app_launcher"
rm -f  "$TARGET/app/assets/images/launcher_bg.c" \
       "$TARGET/app/assets/images/porta_i2c_dev_chart.c" \
       "$TARGET/app/assets/images/internal_i2c_dev_chart.c"

python3 - "$TARGET" <<'PY'
import io, sys, os
t = sys.argv[1]

# assets.h: drop the three declarations whose .c files are gone. Idempotent.
p = os.path.join(t, 'app/assets/assets.h')
s = io.open(p).read(); o = s
for d in ('launcher_bg', 'internal_i2c_dev_chart', 'porta_i2c_dev_chart'):
    s = s.replace('LV_IMG_DECLARE(%s);\n' % d, '')
if s != o:
    io.open(p, 'w').write(s)
    print('   ok   assets.h: dropped the launcher declarations')
else:
    print('   ok   assets.h: already clean')

# app_installer.h: install AppFace instead of AppLauncher. Idempotent, and it ASSERTS rather than
# silently doing nothing -- a no-op replace here yields a tree that builds the wrong app.
p = os.path.join(t, 'app/apps/app_installer.h')
s = io.open(p).read(); o = s
s = s.replace('#include "app_launcher/app_launcher.h"', '#include "app_face/app_face.h"')
s = s.replace('std::make_unique<AppLauncher>()', 'std::make_unique<AppFace>()')
if s != o:
    io.open(p, 'w').write(s)
    print('   ok   app_installer.h: installs AppFace')
else:
    print('   ok   app_installer.h: already installs AppFace')
assert 'AppLauncher' not in s, 'app_installer.h still references AppLauncher'
assert 'AppFace' in s, 'app_installer.h does not install AppFace'
PY

# ---- 4. the IDF, which this script does NOT download ----------------------------------------
IDF="$(dirname "$TARGET")/esp-idf"
if [ -f "$IDF/export.sh" ]; then
    say "esp-idf present at $IDF"
else
    say "esp-idf NOT present -- the DESKTOP build works without it; the DEVICE build does not"
    cat <<EOF
   To get it (2.0 GB, and ./install.sh pulls ~2 GB more of toolchain):
     curl -L -C - -o esp-idf-v5.5.5.zip \\
       https://github.com/espressif/esp-idf/releases/download/v5.5.5/esp-idf-v5.5.5.zip
     unzip -q esp-idf-v5.5.5.zip -d "$(dirname "$TARGET")" && (cd "$IDF" && ./install.sh esp32p4)
   The release ARCHIVE, not a --recursive clone: the clone dies mid-fetch on a busy connection
   (README.md documents it, and lvgl did exactly that during this script's own development).
EOF
fi

say "done. Next:"
echo "   export OPENDUBS_DEMO=$TARGET"
echo "   cd $(cd "$HERE/.." && pwd) && ./build.sh desktop-build"
