#!/usr/bin/env bash
# Sign a firmware image for ONE board, on the Thor: the single signing step for every OpenDubs board.
#
#   sudo sign-firmware.sh stamplc firmware.bin     -> firmware.bin.sig
#   sudo sign-firmware.sh neck    firmware.bin     -> firmware.bin.sig
#
# Signs "OPENDUBS-FW-SIG-1" \0 <board> \0 <image> (ECDSA P-256, SHA-256, DER), so the signature is
# good for that board only, then checks it against the key's public half before writing it. Serve
# the .sig beside the image: each board fetches <image URL>.sig. ota-from-thor.sh calls this for the
# StamPLC; the neck Dial's OTA recipe (firmware/neck_dial/README.md) calls it for the Dial.
# Key: $OPENDUBS_FW_KS_DIR (default /etc/opendubs/firmware-signing; see provision-firmware-signing.sh).
set -euo pipefail
die() { echo "!! $*" >&2; exit 2; }
BOARDS="stamplc neck"
[ $# -eq 2 ] || die "usage: sign-firmware.sh <$(echo $BOARDS | tr ' ' '|')> IMAGE"
BOARD="$1"; IMAGE="$2"
case " $BOARDS " in *" $BOARD "*) ;; *) die "unknown board '$BOARD' (one of: $BOARDS)";; esac
[ -s "$IMAGE" ] || die "no image at $IMAGE"
KS="${OPENDUBS_FW_KS_DIR:-/etc/opendubs/firmware-signing}"
[ -n "${OPENDUBS_FW_KS_DIR:-}" ] || [ "$(id -u)" = 0 ] || die "run with sudo: the signing key is root-only"
[ -r "$KS/ota.key" ] && [ -r "$KS/ota.pub.der" ] \
  || die "no signing key in $KS: create it once with provision-firmware-signing.sh (README.md)"

message() { printf 'OPENDUBS-FW-SIG-1\0%s\0' "$BOARD"; cat "$IMAGE"; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
message | openssl dgst -sha256 -sign "$KS/ota.key" -out "$T/sig"
openssl pkey -pubin -inform DER -in "$KS/ota.pub.der" -out "$T/pub.pem"
message | openssl dgst -sha256 -verify "$T/pub.pem" -signature "$T/sig" >/dev/null \
  || die "the signature does not verify against the key's own public half; not written"
cp "$T/sig" "$IMAGE.sig"
echo "signed $IMAGE for $BOARD with key $(sha256sum "$KS/ota.pub.der" | cut -c1-16): $IMAGE.sig"
