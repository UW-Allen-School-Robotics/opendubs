#!/usr/bin/env bash
# ONCE per project: prove that the release key succeeds the debug key, so the tablet accepts a
# release-signed kiosk as an UPDATE to the debug-signed one it has (APK Signature Scheme v3).
# Needs the debug keystore, placed by the user (README.md); refuses any other debug key.
#
#   sudo rotate-kiosk-key.sh
#
# Afterwards the debug key is no longer needed here: every later APK is signed with the release
# key and this lineage alone. Shred it once the first rotated kiosk has installed (README.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"
[ "$(id -u)" = 0 ] || [ -n "${OPENDUBS_KIOSK_KS_DIR:-}" ] || die "run as root"

[ -e "$RELEASE_KS" ] || die "no release key; run provision-kiosk-signing.sh first"
[ -e "$DEBUG_KS" ] || die "no $DEBUG_KS; the user places it (README.md)"
[ -e "$LINEAGE" ] && die "a lineage already exists; rotation is done once"

# The debug keystore is Android's default: store and key password "android", alias androiddebugkey.
# That password is public and identical on every machine, so it is passed literally.
got=$(keytool -exportcert -rfc -keystore "$DEBUG_KS" -alias androiddebugkey -storepass android 2>/dev/null \
        | openssl x509 -outform DER 2>/dev/null | openssl dgst -sha256 -r | cut -d' ' -f1)
[ "$got" = "$DEBUG_CERT_SHA256" ] || die "this is not the debug key that signed the tablet's kiosk (got ${got:-nothing})"

( umask 077; $APKSIGNER rotate --out "$LINEAGE" \
    --old-signer --ks "$DEBUG_KS" --ks-pass pass:android --ks-key-alias androiddebugkey \
    --new-signer --ks "$RELEASE_KS" --ks-pass "file:$RELEASE_PASS" --ks-key-alias "$ALIAS" )
chmod 0400 "$LINEAGE"
echo "== lineage written: $DEBUG_CERT_SHA256 -> $(cat "$RELEASE_SHA")"
echo "   Next: sign and install the first rotated kiosk; once it is verified on the tablet, shred the"
echo "   debug key here: sudo shred -u $DEBUG_KS   (it stays only in the password-manager backup)"
