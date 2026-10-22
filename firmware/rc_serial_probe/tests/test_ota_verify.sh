#!/usr/bin/env bash
# Host test of the StamPLC's signed-OTA check (src/ota_verify.cpp), on a laptop: no board, no flash.
# Builds mbedTLS from source, makes throwaway keys with openssl exactly as the Thor signs, and checks
# that only a correctly signed, untouched image passes. A deliberately bad signature must be refused.
#
#   firmware/rc_serial_probe/tests/test_ota_verify.sh
#   MBEDTLS_SRC=/path/to/mbedtls firmware/rc_serial_probe/tests/test_ota_verify.sh
#
# MBEDTLS_SRC defaults to the copy inside PlatformIO's ESP-IDF package. The board's Arduino core
# carries mbedTLS 2.28; the md and pk calls used are the same in 2.28 and 3.x.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$HERE/../src"
MBEDTLS_SRC="${MBEDTLS_SRC:-$HOME/.platformio/packages/framework-espidf/components/mbedtls/mbedtls}"
[ -f "$MBEDTLS_SRC/include/mbedtls/pk.h" ] || { echo "no mbedTLS source at $MBEDTLS_SRC (set MBEDTLS_SRC)"; exit 2; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
CC="${CC:-cc}"; CXX="${CXX:-c++}"

echo "-- building mbedTLS (once per run)"
mkdir -p "$W/obj"
for f in "$MBEDTLS_SRC"/library/*.c; do
  "$CC" -O1 -w -I"$MBEDTLS_SRC/include" -I"$MBEDTLS_SRC/library" -c "$f" -o "$W/obj/$(basename "$f" .c).o" &
done; wait
ar rcs "$W/libmbedcrypto.a" "$W"/obj/*.o

keypair() {  # $1 = name: an ECDSA P-256 key and its DER public key as a C header, as the Thor makes them
  openssl ecparam -name prime256v1 -genkey -noout -out "$W/$1.key" 2>/dev/null
  openssl pkey -in "$W/$1.key" -pubout -outform DER -out "$W/$1.der"
  mkdir -p "$W/$1.inc"
  python3 "$HERE/../tools/pubkey_header.py" "$W/$1.der" > "$W/$1.inc/ota_pubkey.h"
}
build() {    # $1 = output, $2.. = extra include dirs
  local out="$1"; shift
  "$CXX" -std=c++17 -O1 -I"$SRC" "$@" -I"$MBEDTLS_SRC/include" "$SRC/ota_verify.cpp" "$HERE/ota_verify_cli.cpp" \
    "$W/libmbedcrypto.a" -o "$out"
}
keypair good; keypair other
build "$W/verify" -I"$W/good.inc"
build "$W/verify_nokey"                          # no ota_pubkey.h on the include path

head -c 700000 /dev/urandom > "$W/fw.bin"         # about a firmware's size
sign() { openssl dgst -sha256 -sign "$W/$1.key" -out "$2" "$3"; }
sign good  "$W/fw.sig"       "$W/fw.bin"
sign other "$W/fw.other.sig" "$W/fw.bin"
cp "$W/fw.bin" "$W/fw.tampered"; printf '\x00' | dd of="$W/fw.tampered" bs=1 seek=4096 conv=notrunc 2>/dev/null
cp "$W/fw.sig" "$W/fw.badsig"; printf '\xff' | dd of="$W/fw.badsig" bs=1 seek=40 conv=notrunc 2>/dev/null
head -c 20 "$W/fw.sig" > "$W/fw.shortsig"; : > "$W/fw.empty"
head -c 64 /dev/urandom > "$W/fw.junksig"

fail=0
check() {  # name, binary, image, sig, expected
  local got; got="$("$2" "$3" "$4")"
  if [ "$got" = "$5" ]; then echo "  ok   $1 -> $got"; else echo "  FAIL $1 -> $got (expected $5)"; fail=1; fi
}
echo "-- cases"
check "the signed image"                 "$W/verify"       "$W/fw.bin"      "$W/fw.sig"       OK
check "one byte of the image changed"    "$W/verify"       "$W/fw.tampered" "$W/fw.sig"       INVALID
check "one byte of the signature changed" "$W/verify"      "$W/fw.bin"      "$W/fw.badsig"    INVALID
check "signed by another key"            "$W/verify"       "$W/fw.bin"      "$W/fw.other.sig" INVALID
check "a truncated signature"            "$W/verify"       "$W/fw.bin"      "$W/fw.shortsig"  INVALID
check "random bytes as the signature"    "$W/verify"       "$W/fw.bin"      "$W/fw.junksig"   INVALID
check "no signature"                     "$W/verify"       "$W/fw.bin"      "$W/fw.empty"     MISSING
check "a build with no key compiled in"  "$W/verify_nokey" "$W/fw.bin"      "$W/fw.sig"       NO_KEY
[ "$fail" -eq 0 ] && echo "OK: only the correctly signed, untouched image is accepted" || { echo "FAILED"; exit 1; }
