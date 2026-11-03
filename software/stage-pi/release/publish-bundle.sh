#!/usr/bin/env bash
# On the Thor, from the deploy: publish a signed copy of software/stage-pi for the stage box, which installs
# it itself between shows (bin/stage_update.py):
#
#   sudo publish-bundle.sh <the stage-pi folder> <version: the folder's git tree hash>
#
# The version is the folder's CONTENT (git rev-parse HEAD:software/stage-pi), so a deploy that does not
# change it publishes the same version, and the box neither reinstalls nor reboots.
#
# Signed by the project's firmware-signing step, the one every board's update uses (the Thor lane's
# software/stack/firmware-signing/sign-firmware.sh, target "stage": ECDSA P-256 over
# "OPENDUBS-FW-SIG-1" \0 stage \0 <manifest.json>). This script never touches a key.
#
# Output, in $OPENDUBS_STAGE_BUNDLE_DIR (default /var/lib/opendubs/stage), each replaced by a rename:
#   stage-bundle.tar.gz   the folder
#   manifest.json.sig     the signature of manifest.json
#   manifest.json         {"version", "sha256" of the archive, "bytes", "built"}, written last
set -euo pipefail
die() { echo "!! $*" >&2; exit 2; }
SIGN=${OPENDUBS_SIGN_FIRMWARE:-/opt/opendubs/stack/firmware-signing/sign-firmware.sh}
SRC="${1:?usage: publish-bundle.sh <stage-pi folder> <version>}"
VERSION="${2:?usage: publish-bundle.sh <stage-pi folder> <version>}"
OUT="${OPENDUBS_STAGE_BUNDLE_DIR:-/var/lib/opendubs/stage}"
[ -d "$SRC/bin" ] && [ -f "$SRC/packages.txt" ] || die "$SRC is not the stage-pi folder"
[[ "$VERSION" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "version: letters, digits, . _ - only"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
same=()                                            # GNU tar: the same folder gives the same archive
tar --version 2>/dev/null | grep -q GNU && same=(--sort=name --owner=0 --group=0 --numeric-owner --mtime=@0)
tar ${same[@]+"${same[@]}"} --exclude=__pycache__ --exclude=tests -C "$SRC" -czf "$WORK/stage-bundle.tar.gz" .
SHA=$(openssl dgst -sha256 -r "$WORK/stage-bundle.tar.gz" | cut -d' ' -f1)
python3 - "$WORK/manifest.json" "$VERSION" "$SHA" "$(wc -c < "$WORK/stage-bundle.tar.gz")" <<'PY'
import datetime, json, sys
p, version, sha, n = sys.argv[1:]
json.dump({"version": version, "sha256": sha, "bytes": int(n),
           "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, open(p, "w"))
PY
"$SIGN" stage "$WORK/manifest.json" >/dev/null || die "the firmware-signing step refused to sign it"
[ -s "$WORK/manifest.json.sig" ] || die "no signature written"

install -d -m 0755 "$OUT"
for f in stage-bundle.tar.gz manifest.json.sig manifest.json; do   # the manifest last: a reader never
  install -m 0644 "$WORK/$f" "$OUT/.$f.new"                        # sees one for an archive not there yet
  mv -f "$OUT/.$f.new" "$OUT/$f"
done
rm -f "$OUT/manifest.sig" "$OUT/key.pem"
echo "== published the stage box's software $VERSION (sha256 $SHA)"
