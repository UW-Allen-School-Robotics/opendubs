#!/usr/bin/env bash
# On the Thor, from the deploy: sign a copy of software/stage-pi with the robot's project key (the one that
# signs the tablet's app; its settings and paths are the kiosk-signing tooling's own common.sh) and publish
# it for the stage box, which installs it itself between shows (bin/stage_update.py):
#
#   sudo publish-bundle.sh <the stage-pi folder> <version, e.g. the deploy's git sha>
#
# Output, in $OPENDUBS_STAGE_BUNDLE_DIR (default /var/lib/opendubs/stage), each replaced by a rename:
#   stage-bundle.tar.gz   the folder
#   key.pem               the project key's PUBLIC half (setup.sh pins it on the box)
#   manifest.sig          RSA-SHA256 of manifest.json
#   manifest.json         {"version", "sha256" of the archive, "bytes", "built"}, written last
# The private key is read from the keystore through a pipe and never written anywhere, unlocked.
set -euo pipefail
SIGNING=${OPENDUBS_SIGNING_COMMON:-/opt/opendubs/stack/kiosk-signing/common.sh}
# shellcheck source=/dev/null
. "$SIGNING"                                       # RELEASE_KS, RELEASE_PASS, die
SRC="${1:?usage: publish-bundle.sh <stage-pi folder> <version>}"
VERSION="${2:?usage: publish-bundle.sh <stage-pi folder> <version>}"
OUT="${OPENDUBS_STAGE_BUNDLE_DIR:-/var/lib/opendubs/stage}"
[ "$(id -u)" = 0 ] || [ -n "${OPENDUBS_KIOSK_KS_DIR:-}" ] || die "run as root"
[ -d "$SRC/bin" ] && [ -f "$SRC/packages.txt" ] || die "$SRC is not the stage-pi folder"
[ -e "$RELEASE_KS" ] && [ -e "$RELEASE_PASS" ] || die "signing not provisioned (provision-kiosk-signing.sh)"
[[ "$VERSION" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "version: letters, digits, . _ - only"

umask 077
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
openssl pkcs12 -in "$RELEASE_KS" -passin "file:$RELEASE_PASS" -nocerts -nodes 2>/dev/null \
  | openssl dgst -sha256 -sign /dev/stdin -out "$WORK/manifest.sig" "$WORK/manifest.json" \
  || die "could not sign with $RELEASE_KS"
openssl pkcs12 -in "$RELEASE_KS" -passin "file:$RELEASE_PASS" -nokeys -clcerts 2>/dev/null \
  | openssl x509 -pubkey -noout > "$WORK/key.pem" || die "could not read the public key"
openssl dgst -sha256 -verify "$WORK/key.pem" -signature "$WORK/manifest.sig" "$WORK/manifest.json" >/dev/null \
  || die "the signature does not verify"

install -d -m 0755 "$OUT"
for f in stage-bundle.tar.gz key.pem manifest.sig manifest.json; do   # the manifest last: a reader never
  install -m 0644 "$WORK/$f" "$OUT/.$f.new"                           # sees one for an archive not there yet
  mv -f "$OUT/.$f.new" "$OUT/$f"
done
echo "== published the stage box's software $VERSION (sha256 $SHA)"
