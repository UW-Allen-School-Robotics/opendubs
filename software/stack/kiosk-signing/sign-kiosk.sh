#!/usr/bin/env bash
# Sign an UNSIGNED kiosk APK with the project's release key and rotation lineage, check it, and publish
# it for the dashboard's /api/kiosk/latest:
#
#   sudo sign-kiosk.sh <app-release-unsigned.apk> <kiosk-version.json>
#
# kiosk-version.json is written by the kiosk build: {"versionCode": <int>, "versionName": "<x.y.z>"}.
# Output, replacing the previous build atomically:
#   $OUT_DIR/opendubs-kiosk.apk
#   $OUT_DIR/latest.json   {"versionCode", "versionName", "sha256" of the signed APK, "signer" cert SHA-256}
# It refuses to publish unless the signed APK verifies with v3, is signed by the release certificate,
# and carries the lineage back to the debug certificate the tablet trusts today.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"
[ "$(id -u)" = 0 ] || [ -n "${OPENDUBS_KIOSK_KS_DIR:-}" ] || die "run as root"

IN="${1:?usage: sign-kiosk.sh <unsigned.apk> <kiosk-version.json>}"
VER="${2:?usage: sign-kiosk.sh <unsigned.apk> <kiosk-version.json>}"
[ -f "$IN" ] && [ -f "$VER" ] || die "missing input"
[ -e "$RELEASE_KS" ] && [ -s "$RELEASE_SHA" ] || die "signing not provisioned (provision-kiosk-signing.sh)"
[ -e "$LINEAGE" ] || die "no rotation lineage (rotate-kiosk-key.sh, once)"

read -r VCODE VNAME < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]));
c=d["versionCode"]; n=d["versionName"]; assert isinstance(c,int) and c>0 and isinstance(n,str) and n
print(c, n)' "$VER") || die "kiosk-version.json must hold an integer versionCode and a versionName"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/opendubs-kiosk.apk"
# v3 only, from Android 9 (minSdk 28): the tablet verifies v3 and reads the lineage there. apksigner
# 33+ needs --rotation-min-sdk-version 28 to put the rotated signer in the v3.0 block; older ones
# (Ubuntu's 31) already do so and do not know the flag.
ROT=()
$APKSIGNER sign --help 2>&1 | grep -q -- '--rotation-min-sdk-version' && ROT=(--rotation-min-sdk-version 28)
$APKSIGNER sign --in "$IN" --out "$OUT" --min-sdk-version 28 ${ROT[@]+"${ROT[@]}"} \
  --v1-signing-enabled false --v2-signing-enabled false \
  --ks "$RELEASE_KS" --ks-pass "file:$RELEASE_PASS" --ks-key-alias "$ALIAS" --lineage "$LINEAGE"

V=$($APKSIGNER verify -v --print-certs --min-sdk-version 28 "$OUT" 2>&1) || { echo "$V" >&2; die "apksigner verify failed"; }
grep -qE "^Verified using v3 scheme \(APK Signature Scheme v3\): true" <<<"$V" || die "not verified with v3"
WANT=$(cat "$RELEASE_SHA")
# apksigner 33+ prints "V3.0 Signer: certificate SHA-256 digest:", older ones "Signer #1 certificate …".
grep -qiE "^(V3\.0 Signer:|Signer #1) certificate SHA-256 digest: $WANT\$" <<<"$V" \
  || die "not signed by the release certificate $WANT"
# Android 11+ refuses to install an APK whose uncompressed entries (resources.arsc) do not start on a
# 4-byte boundary. apksigner aligns them as it signs, which zipalign would otherwise do; this checks the
# result instead of trusting either tool, so no zipalign is needed on the robot.
python3 -I - "$OUT" <<'PY' || die "the signed APK is not 4-byte aligned"
import struct, sys, zipfile
f = open(sys.argv[1], "rb")
for i in zipfile.ZipFile(f).infolist():
    if i.compress_type != zipfile.ZIP_STORED:
        continue
    f.seek(i.header_offset)
    n, m = struct.unpack("<26xHH", f.read(30))
    if (i.header_offset + 30 + n + m) % 4:
        sys.exit("misaligned: " + i.filename)
PY
SIGNED_SHA=$(openssl dgst -sha256 -r "$OUT" | cut -d' ' -f1)
# The lineage's first signer must be the tablet's current certificate, or the update is refused.
$APKSIGNER lineage --in "$LINEAGE" --print-certs 2>&1 \
  | grep -qiE "^Signer #1 in lineage certificate SHA-256 digest: $DEBUG_CERT_SHA256\$" \
  || die "the lineage does not start from the tablet's certificate $DEBUG_CERT_SHA256"

python3 - "$WORK/latest.json" "$VCODE" "$VNAME" "$SIGNED_SHA" "$WANT" <<'PY'
import json, sys
p, code, name, sha, signer = sys.argv[1:]
json.dump({"versionCode": int(code), "versionName": name, "sha256": sha, "signer": signer},
          open(p, "w"), indent=1)
PY
install -d -m 0755 -o root -g "$OUT_GROUP" "$OUT_DIR" 2>/dev/null || install -d -m 0755 "$OUT_DIR"
# The APK first, then latest.json, each by rename: a reader never sees a half-written file, and
# never sees a latest.json describing an APK that isn't there yet.
for f in opendubs-kiosk.apk latest.json; do
  install -m 0644 "$WORK/$f" "$OUT_DIR/.$f.new"
  chgrp "$OUT_GROUP" "$OUT_DIR/.$f.new" 2>/dev/null || true
  mv -f "$OUT_DIR/.$f.new" "$OUT_DIR/$f"
done
echo "== published kiosk $VNAME ($VCODE), sha256 $SIGNED_SHA, signer $WANT"
