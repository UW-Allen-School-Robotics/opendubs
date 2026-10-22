#!/usr/bin/env bash
# The StamPLC's firmware-signing key, on the Thor. Run as root. See README.md beside this file and
# notes/design/firmware-signing.md.
#
#   sudo provision-firmware-signing.sh                  create the project key (refuses to replace one)
#   sudo provision-firmware-signing.sh --import KEY.tar  install the project key from its backup
#   sudo provision-firmware-signing.sh --next           create the NEXT key, for a rotation
#   sudo provision-firmware-signing.sh --promote        after a rotation has landed: next becomes current
#   sudo provision-firmware-signing.sh --show           print the key ids (public; safe to share)
#
# Files, in $OPENDUBS_FW_KS_DIR (default /etc/opendubs/firmware-signing, root 0700):
#   ota.key        ECDSA P-256 private key (0400). Never printed, never copied off the robot except
#                  by the backup the maintainers run by hand (README).
#   ota.pub.der    its public key (0444); ota-from-thor.sh compiles it into every firmware build
#   ota.next.key / ota.next.pub.der    only during a rotation
# The key id is the first 16 hex digits of the public key's SHA-256: the board reports the same
# number as otakey= in its [NET] telemetry line.
set -euo pipefail
die() { echo "!! $*" >&2; exit 2; }
KS="${OPENDUBS_FW_KS_DIR:-/etc/opendubs/firmware-signing}"
# A dry run on a laptop sets OPENDUBS_FW_KS_DIR to a scratch folder and needs no root; on a robot
# sudo drops it, so the real key always lands in the root-only default.
OWN=()
if [ -z "${OPENDUBS_FW_KS_DIR:-}" ]; then
  [ "$(id -u)" = 0 ] || die "run as root"
  OWN=(-o root -g root)
fi
umask 077
install -d -m 0700 ${OWN[@]+"${OWN[@]}"} "$KS"

keyid() { [ -f "$1" ] && sha256sum "$1" | cut -c1-16 || echo none; }
pub_of() {  # $1 key -> $2 public DER
  openssl pkey -in "$1" -pubout -outform DER -out "$2.tmp" && mv "$2.tmp" "$2" && chmod 0444 "$2"
}
newkey() {  # $1 path: a fresh P-256 key, written atomically
  openssl ecparam -name prime256v1 -genkey -noout -out "$1.tmp" && chmod 0400 "$1.tmp" && mv "$1.tmp" "$1"
}
is_p256() { openssl pkey -in "$1" -noout -text 2>/dev/null | grep -qE 'prime256v1|P-256'; }

case "${1:-}" in
  "")
    if [ -f "$KS/ota.key" ]; then
      echo "-- a signing key already exists (id $(keyid "$KS/ota.pub.der")); nothing changed"
      exit 0
    fi
    newkey "$KS/ota.key"; pub_of "$KS/ota.key" "$KS/ota.pub.der"
    echo "-- created the firmware-signing key, id $(keyid "$KS/ota.pub.der")"
    echo "   BACK IT UP NOW (README.md, 'Back up the key'). Without it, the StamPLC can only be"
    echo "   updated over USB."
    ;;
  --import)
    [ -f "${2:-}" ] || die "usage: --import KEY.tar"
    [ -f "$KS/ota.key" ] && die "a key is already installed (id $(keyid "$KS/ota.pub.der")); refusing to replace it"
    T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
    tar -xf "$2" -C "$T" ota.key || die "$2 has no ota.key"
    is_p256 "$T/ota.key" || die "ota.key in $2 is not an ECDSA P-256 key"
    install -m 0400 ${OWN[@]+"${OWN[@]}"} "$T/ota.key" "$KS/ota.key"; pub_of "$KS/ota.key" "$KS/ota.pub.der"
    echo "-- imported the firmware-signing key, id $(keyid "$KS/ota.pub.der")"
    ;;
  --next)
    [ -f "$KS/ota.key" ] || die "no current key: a rotation needs one (or recover over USB, README)"
    [ -f "$KS/ota.next.key" ] && die "a rotation is already under way (next id $(keyid "$KS/ota.next.pub.der"))"
    newkey "$KS/ota.next.key"; pub_of "$KS/ota.next.key" "$KS/ota.next.pub.der"
    echo "-- created the NEXT key, id $(keyid "$KS/ota.next.pub.der")."
    echo "   The next ota-from-thor.sh compiles it in and signs with the current key. Once the board"
    echo "   reports otakey=$(keyid "$KS/ota.next.pub.der"), run --promote."
    ;;
  --promote)
    [ -f "$KS/ota.next.key" ] || die "no rotation under way"
    stamp=$(date +%Y%m%d-%H%M%S)
    mv "$KS/ota.key" "$KS/ota.retired-$stamp.key"; mv "$KS/ota.pub.der" "$KS/ota.retired-$stamp.pub.der"
    mv "$KS/ota.next.key" "$KS/ota.key"; mv "$KS/ota.next.pub.der" "$KS/ota.pub.der"
    echo "-- the current key is now id $(keyid "$KS/ota.pub.der"); the old one is kept as ota.retired-$stamp.*"
    echo "   Back up the new key (README). Delete the retired one once every board reports the new id."
    ;;
  --show)
    echo "current: $(keyid "$KS/ota.pub.der")"
    [ -f "$KS/ota.next.pub.der" ] && echo "next:    $(keyid "$KS/ota.next.pub.der")"
    ;;
  *) die "unknown option ${1}; see the header of this script" ;;
esac
