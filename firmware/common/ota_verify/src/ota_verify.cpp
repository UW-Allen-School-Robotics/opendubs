// See ota_verify.h.
#include "ota_verify.h"

#include <stdio.h>
#include <string.h>
#include "mbedtls/pk.h"
#include "ota_selftest.h"

bool otaVerifyBegin(OtaVerify& v, const char* board)
{
    mbedtls_md_init(&v.md);
    v.started = mbedtls_md_setup(&v.md, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0) == 0
             && mbedtls_md_starts(&v.md) == 0
             && board && *board
             // the domain and the board name, each with its terminating NUL
             && mbedtls_md_update(&v.md, (const uint8_t*)OTA_SIG_DOMAIN, sizeof(OTA_SIG_DOMAIN)) == 0
             && mbedtls_md_update(&v.md, (const uint8_t*)board, strlen(board) + 1) == 0;
    if (!v.started) mbedtls_md_free(&v.md);
    return v.started;
}

void otaVerifyUpdate(OtaVerify& v, const uint8_t* data, size_t len)
{
    if (v.started && mbedtls_md_update(&v.md, data, len) != 0) {
        mbedtls_md_free(&v.md);
        v.started = false;              // Finish then reports HASH_FAILED
    }
}

void otaVerifyAbort(OtaVerify& v)
{
    if (v.started) mbedtls_md_free(&v.md);
    v.started = false;
}

OtaVerifyResult otaVerifyFinish(OtaVerify& v, const uint8_t* sig, size_t sig_len,
                                const OtaKey* keys, size_t nkeys, int* which)
{
    if (which) *which = -1;
    uint8_t hash[32];
    bool hashed = v.started && mbedtls_md_finish(&v.md, hash) == 0;
    otaVerifyAbort(v);
    if (!keys || nkeys == 0) return OTA_SIG_NO_KEY;
    if (!sig || sig_len == 0) return OTA_SIG_MISSING;
    if (!hashed) return OTA_SIG_HASH_FAILED;

    bool any_parsed = false;
    for (size_t i = 0; i < nkeys; i++) {
        mbedtls_pk_context pk;
        mbedtls_pk_init(&pk);
        if (keys[i].der && keys[i].len
            && mbedtls_pk_parse_public_key(&pk, keys[i].der, keys[i].len) == 0
            && mbedtls_pk_can_do(&pk, MBEDTLS_PK_ECDSA)) {
            any_parsed = true;
            int rc = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), sig, sig_len);
            mbedtls_pk_free(&pk);
            if (rc == 0) { if (which) *which = (int)i; return OTA_SIG_OK; }
        } else {
            mbedtls_pk_free(&pk);
        }
    }
    return any_parsed ? OTA_SIG_INVALID : OTA_SIG_BAD_KEY;
}

const char* otaVerifyReason(OtaVerifyResult r)
{
    switch (r) {
        case OTA_SIG_OK:          return "signature valid";
        case OTA_SIG_NO_KEY:      return "this firmware trusts no signing key, so it accepts no update";
        case OTA_SIG_BAD_KEY:     return "the compiled-in signing key does not parse";
        case OTA_SIG_MISSING:     return "no signature for the image";
        case OTA_SIG_HASH_FAILED: return "could not hash the image";
        case OTA_SIG_INVALID:     return "the signature does not match this board, this image and a trusted key";
    }
    return "unknown";
}

void otaKeyId(const OtaKey& key, char* buf, size_t buflen)
{
    if (buflen == 0) return;
    buf[0] = '\0';
    if (!key.der || key.len == 0 || buflen < 17) return;
    uint8_t h[32];
    if (mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key.der, key.len, h) != 0) return;
    for (int i = 0; i < 8; i++) snprintf(buf + 2 * i, 3, "%02x", h[i]);
}

bool otaVerifySelfTest()
{
    // A fixed throwaway key (its private half was discarded when the vector was made) and a
    // signature over OTA_SELFTEST_MSG for board OTA_SELFTEST_BOARD. A good signature must pass; the
    // same signature on a changed message, or for another board, must fail.
    const OtaKey key = {OTA_SELFTEST_PUB, sizeof(OTA_SELFTEST_PUB)};
    const uint8_t* msg = (const uint8_t*)OTA_SELFTEST_MSG;
    const size_t n = sizeof(OTA_SELFTEST_MSG) - 1;
    OtaVerify v;

    if (!otaVerifyBegin(v, OTA_SELFTEST_BOARD)) return false;
    otaVerifyUpdate(v, msg, n);
    if (otaVerifyFinish(v, OTA_SELFTEST_SIG, sizeof(OTA_SELFTEST_SIG), &key, 1) != OTA_SIG_OK) return false;

    if (!otaVerifyBegin(v, OTA_SELFTEST_BOARD)) return false;
    otaVerifyUpdate(v, msg, n - 1);                                     // one byte short
    if (otaVerifyFinish(v, OTA_SELFTEST_SIG, sizeof(OTA_SELFTEST_SIG), &key, 1) != OTA_SIG_INVALID) return false;

    if (!otaVerifyBegin(v, "not-" OTA_SELFTEST_BOARD)) return false;   // another board
    otaVerifyUpdate(v, msg, n);
    return otaVerifyFinish(v, OTA_SELFTEST_SIG, sizeof(OTA_SELFTEST_SIG), &key, 1) == OTA_SIG_INVALID;
}
