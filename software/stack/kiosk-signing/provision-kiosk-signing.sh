#!/usr/bin/env bash
# Make a robot able to sign the tablet's kiosk app. Idempotent; run as root on the Thor.
#
#   sudo provision-kiosk-signing.sh                  tools + folders; a NEW release key if none exists
#   sudo provision-kiosk-signing.sh --import B.tar   tools + folders; the PROJECT key from a backup
#
# One project key signs the kiosk on every robot, so a second robot IMPORTS the key that the first
# one generated (from the team password manager; see README.md), it never generates its own.
# Nothing here prints the password or copies the key off the robot.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"
[ "$(id -u)" = 0 ] || die "run as root"

echo "== OpenDubs kiosk signing =="
if ! command -v apksigner >/dev/null || ! command -v keytool >/dev/null; then
  echo "-- installing a headless JRE and apksigner (Ubuntu packages)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-upgrade openjdk-17-jre-headless apksigner >/dev/null
fi

install -d -m 0700 -o root -g root "$KS_DIR"
install -d -m 0755 -o root -g "$OUT_GROUP" "$OUT_DIR"

if [ "${1:-}" = "--import" ]; then
  B="${2:?--import needs the backup tar}"
  [ -e "$RELEASE_KS" ] && die "a release key is already installed; refusing to overwrite it"
  ( umask 077; tar -C "$KS_DIR" -xf "$B" --no-same-owner release.p12 release.pass lineage )
  echo "-- imported the project key; delete your copy of the tar: rm '$B'"
elif [ ! -e "$RELEASE_KS" ]; then
  echo "-- generating a NEW release key on this robot (EC P-256, ~27 years)"
  ( umask 077; openssl rand -base64 32 > "$RELEASE_PASS" )
  ( umask 077; keytool -genkeypair -keystore "$RELEASE_KS" -storetype PKCS12 \
      -storepass:file "$RELEASE_PASS" -alias "$ALIAS" -keyalg EC -groupname secp256r1 \
      -validity 10000 -dname "CN=OpenDubs kiosk" >/dev/null 2>&1 )
  echo "-- generated. Back it up now (README.md, 'Back up the release key')."
fi

cert_sha256 "$RELEASE_KS" "$ALIAS" "$RELEASE_PASS" > "$RELEASE_SHA"
[ -s "$RELEASE_SHA" ] || die "could not read the release certificate"
chown -R root:root "$KS_DIR"; chmod 0700 "$KS_DIR"; chmod 0400 "$KS_DIR"/*
echo "== done. release certificate SHA-256: $(cat "$RELEASE_SHA")"
[ -e "$LINEAGE" ] || echo "   no rotation lineage yet: run rotate-kiosk-key.sh once (README.md)"
