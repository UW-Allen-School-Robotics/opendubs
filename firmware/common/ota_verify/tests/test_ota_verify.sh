#!/usr/bin/env bash
# Host test of the shared signed-update check (src/ota_verify.cpp), on a laptop: no board, no flash.
# Builds mbedTLS from source, signs with the Thor's real signing script, and checks that only a
# correctly signed, untouched image for THIS board passes. A deliberately bad signature must be refused.
#
#   firmware/common/ota_verify/tests/test_ota_verify.sh
#
# MBEDTLS_SRC defaults to the copy inside PlatformIO's ESP-IDF package. The StamPLC's Arduino core has
# mbedTLS 2.28 and the Dial's 3.x; the md and pk calls used are the same in both.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$HERE/../src"
SIGN="$HERE/../../../../software/stack/firmware-signing/sign-firmware.sh"
MBEDTLS_SRC="${MBEDTLS_SRC:-$HOME/.platformio/packages/framework-espidf/components/mbedtls/mbedtls}"
[ -f "$MBEDTLS_SRC/include/mbedtls/pk.h" ] || { echo "no mbedTLS source at $MBEDTLS_SRC (set MBEDTLS_SRC)"; exit 2; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

echo "-- building mbedTLS and the check"
mkdir -p "$W/obj"
for f in "$MBEDTLS_SRC"/library/*.c; do
  "${CC:-cc}" -O1 -w -I"$MBEDTLS_SRC/include" -I"$MBEDTLS_SRC/library" -c "$f" -o "$W/obj/$(basename "$f" .c).o" &
done; wait
ar rcs "$W/libmbedcrypto.a" "$W"/obj/*.o
"${CXX:-c++}" -std=c++17 -O1 -Wall -Wextra -I"$SRC" -I"$MBEDTLS_SRC/include" \
  "$SRC/ota_verify.cpp" "$HERE/ota_verify_cli.cpp" "$W/libmbedcrypto.a" -o "$W/verify"

keys() {  # $1 = a key folder laid out as the Thor's: ota.key, ota.pub.der
  mkdir -p "$W/$1"
  openssl ecparam -name prime256v1 -genkey -noout -out "$W/$1/ota.key" 2>/dev/null
  openssl pkey -in "$W/$1/ota.key" -pubout -outform DER -out "$W/$1/ota.pub.der"
}
keys cur; keys next; keys other
sign() {  # board key-folder image out : the Thor's own signing script, against a scratch key folder
  OPENDUBS_FW_KS_DIR="$W/$2" "$SIGN" "$1" "$3" >/dev/null && mv "$3.sig" "$4"
}
head -c 700000 /dev/urandom > "$W/fw.bin"
sign stamplc cur   "$W/fw.bin" "$W/stamplc.sig"
sign neck    cur   "$W/fw.bin" "$W/neck.sig"
sign stamplc next  "$W/fw.bin" "$W/next.sig"
sign stamplc other "$W/fw.bin" "$W/other.sig"
cp "$W/fw.bin" "$W/fw.tampered"; printf '\x00' | dd of="$W/fw.tampered" bs=1 seek=4096 conv=notrunc 2>/dev/null
cp "$W/stamplc.sig" "$W/bad.sig"; printf '\xff' | dd of="$W/bad.sig" bs=1 seek=40 conv=notrunc 2>/dev/null
head -c 20 "$W/stamplc.sig" > "$W/short.sig"; head -c 64 /dev/urandom > "$W/junk.sig"; : > "$W/empty.sig"

C="$W/cur/ota.pub.der"; N="$W/next/ota.pub.der"
fail=0
check() {  # name expected board image sig keys...
  local name="$1" want="$2"; shift 2
  local got; got="$("$W/verify" "$@")"
  if [ "$got" = "$want" ]; then echo "  ok   $name -> $got"; else echo "  FAIL $name -> $got (expected $want)"; fail=1; fi
}
echo "-- cases"
check "the boot self-test"                       PASS     --selftest
check "signed for this board"                    "OK key0" stamplc "$W/fw.bin" "$W/stamplc.sig" "$C"
check "signed for the OTHER board"               INVALID  stamplc "$W/fw.bin" "$W/neck.sig"    "$C"
check "the neck's own image on the neck"         "OK key0" neck    "$W/fw.bin" "$W/neck.sig"    "$C"
check "one image byte changed"                   INVALID  stamplc "$W/fw.tampered" "$W/stamplc.sig" "$C"
check "one signature byte changed"               INVALID  stamplc "$W/fw.bin" "$W/bad.sig"     "$C"
check "signed by an untrusted key"               INVALID  stamplc "$W/fw.bin" "$W/other.sig"   "$C"
check "a truncated signature"                    INVALID  stamplc "$W/fw.bin" "$W/short.sig"   "$C"
check "random bytes as the signature"            INVALID  stamplc "$W/fw.bin" "$W/junk.sig"    "$C"
check "no signature"                             MISSING  stamplc "$W/fw.bin" "$W/empty.sig"   "$C"
check "a build that trusts no key"               NO_KEY   stamplc "$W/fw.bin" "$W/stamplc.sig"
check "rotation: old key, board trusts both"     "OK key0" stamplc "$W/fw.bin" "$W/stamplc.sig" "$C" "$N"
check "rotation: new key, board trusts both"     "OK key1" stamplc "$W/fw.bin" "$W/next.sig"   "$C" "$N"
check "after rotation: the retired key"          INVALID  stamplc "$W/fw.bin" "$W/stamplc.sig" "$N"
[ "$fail" -eq 0 ] && echo "OK: only a correctly signed, untouched image for this board passes" || { echo "FAILED"; exit 1; }
