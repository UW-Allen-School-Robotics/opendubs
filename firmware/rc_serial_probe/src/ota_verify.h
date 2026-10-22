#pragma once
// Signed OTA: verify a firmware image against the project's public key BEFORE it is made bootable.
//
// The OTA command is unauthenticated UDP, so the only thing that makes an update ours is a
// signature: ECDSA P-256 over the SHA-256 of the image, made on the Thor by ota-from-thor.sh with a
// key that never leaves it (notes/design/firmware-signing.md). The public key is compiled in, from
// include/ota_pubkey.h, which ota-from-thor.sh and flash-from-thor.sh generate from the Thor's key.
// A build without that header has NO key and rejects every OTA: fail closed, never open.
//
// Plain mbedTLS (the md and pk APIs, the same in 2.28 on the board and 3.x on a laptop) and no
// Arduino, so the host test (tests/test_ota_verify.sh) runs this exact file.
#include <stddef.h>
#include <stdint.h>
#include "mbedtls/md.h"

enum OtaVerifyResult {
    OTA_SIG_OK = 0,
    OTA_SIG_NO_KEY,         // this build has no public key compiled in
    OTA_SIG_BAD_KEY,        // the compiled-in key does not parse
    OTA_SIG_MISSING,        // no signature (empty, or not fetched)
    OTA_SIG_HASH_FAILED,    // SHA-256 could not run
    OTA_SIG_INVALID,        // the signature does not match this image and key
};

struct OtaVerify {
    mbedtls_md_context_t md;
    bool started;
};

// The key this firmware trusts: DER SubjectPublicKeyInfo, or nullptr/0 when the build has none.
void otaVerifyKey(const uint8_t** der, size_t* len);
// SHA-256 of the key as 16 hex digits, for the boot banner ("" when there is none). buf >= 17.
void otaVerifyKeyId(char* buf, size_t buflen);

bool otaVerifyBegin(OtaVerify& v);
void otaVerifyUpdate(OtaVerify& v, const uint8_t* data, size_t len);
// Finish the hash and check `sig` (DER ECDSA) with the key `der`. Frees the context either way.
OtaVerifyResult otaVerifyFinish(OtaVerify& v, const uint8_t* sig, size_t sig_len,
                                const uint8_t* der, size_t der_len);
void otaVerifyAbort(OtaVerify& v);
const char* otaVerifyReason(OtaVerifyResult r);
