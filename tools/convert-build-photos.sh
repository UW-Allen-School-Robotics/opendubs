#!/usr/bin/env bash
# Convert + resize the DESIGNATED build photos into a module's web-ready assets
# folder. Uses macOS `sips` (built in — no install). ONLY files listed in the
# manifest are processed — that manifest is how you designate which of the many
# shots on Drive actually make the guide, and what each becomes.
#
# Workflow:
#   1. Download the module's raw photos (e.g. "1 - Base Frame Build") to a staging dir.
#   2. Edit the manifest: one "SOURCE.HEIC  target.jpg" line per KEPT photo (see below).
#   3. Run this — converted/resized JPGs land in docs/assets/build/<module>/.
#
# Usage:
#   tools/convert-build-photos.sh <staging-dir> <manifest> <out-dir>
# Example:
#   tools/convert-build-photos.sh ~/Downloads/base-frame-raw \
#       docs/assets/build/base-frame/photos.manifest \
#       docs/assets/build/base-frame
#
# Manifest format (whitespace-separated, '#' comments and blank lines ignored):
#   IMG_1480.HEIC   completed-01.jpg
#   IMG_1490.HEIC   01-frame-rectangle.jpg

set -euo pipefail
MAXPX="${MAXPX:-1600}"        # longest edge, px (override: MAXPX=2000 ...)

if [[ $# -ne 3 ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'   # print the header as help
  exit 2
fi
staging="$1"; manifest="$2"; out="$3"

command -v sips >/dev/null || { echo "sips not found (macOS only). Use ImageMagick otherwise." >&2; exit 1; }
[[ -d "$staging" ]] || { echo "staging dir not found: $staging" >&2; exit 1; }
[[ -f "$manifest" ]] || { echo "manifest not found: $manifest" >&2; exit 1; }
mkdir -p "$out"

n=0; miss=0
while read -r src dst _rest; do
  [[ -z "${src:-}" || "${src:0:1}" == "#" ]] && continue
  in="$staging/$src"
  if [[ ! -f "$in" ]]; then echo "  MISSING  $src" >&2; miss=$((miss+1)); continue; fi
  sips -s format jpeg -Z "$MAXPX" "$in" --out "$out/$dst" >/dev/null
  echo "  ok       $src  ->  $dst"
  n=$((n+1))
done < "$manifest"

echo "converted $n file(s) into $out${miss:+  ($miss missing)}"
echo "note: sips re-encodes and drops most camera metadata; if you need a hard EXIF/GPS"
echo "      scrub, run: exiftool -all= \"$out\"/*.jpg   (if exiftool is installed)"
