// See ota_verify.h.
#include "ota_verify.h"

#include <stdio.h>
#include <string.h>
#include "mbedtls/pk.h"

#if defined(__has_include)
#  if __has_include("ota_pubkey.h")
#    include "ota_pubkey.h"     // generated on the Thor: static const uint8_t OTA_PUBKEY_DER[]
#    define OTA_HAVE_KEY 1
#  endif
#endif

void otaVerifyKey(const uint8_t** der, size_t* len)
{
#ifdef OTA_HAVE_KEY
    *der = OTA_PUBKEY_DER; *len = sizeof(OTA_PUBKEY_DER);
#else
    *der = nullptr; *len = 0;
#endif
}

void otaVerifyKeyId(char* buf, size_t buflen)
{
    if (buflen == 0) return;
    buf[0] = '\0';
    const uint8_t* der; size_t len;
    otaVerifyKey(&der, &len);
    if (!der || len == 0 || buflen < 17) return;
    uint8_t h[32];
    if (mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), der, len, h) != 0) return;
    for (int i = 0; i < 8; i++) snprintf(buf + 2 * i, 3, "%02x", h[i]);
}

bool otaVerifyBegin(OtaVerify& v)
{
    mbedtls_md_init(&v.md);
    v.started = mbedtls_md_setup(&v.md, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0) == 0
             && mbedtls_md_starts(&v.md) == 0;
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
                                const uint8_t* der, size_t der_len)
{
    uint8_t hash[32];
    bool hashed = v.started && mbedtls_md_finish(&v.md, hash) == 0;
    otaVerifyAbort(v);
    if (!der || der_len == 0) return OTA_SIG_NO_KEY;
    if (!sig || sig_len == 0) return OTA_SIG_MISSING;
    if (!hashed) return OTA_SIG_HASH_FAILED;

    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);
    if (mbedtls_pk_parse_public_key(&pk, der, der_len) != 0 || !mbedtls_pk_can_do(&pk, MBEDTLS_PK_ECDSA)) {
        mbedtls_pk_free(&pk);
        return OTA_SIG_BAD_KEY;
    }
    int rc = mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), sig, sig_len);
    mbedtls_pk_free(&pk);
    return rc == 0 ? OTA_SIG_OK : OTA_SIG_INVALID;
}

const char* otaVerifyReason(OtaVerifyResult r)
{
    switch (r) {
        case OTA_SIG_OK:          return "signature valid";
        case OTA_SIG_NO_KEY:      return "this firmware has no signing key compiled in, so it accepts no OTA";
        case OTA_SIG_BAD_KEY:     return "the compiled-in signing key does not parse";
        case OTA_SIG_MISSING:     return "no signature for the image";
        case OTA_SIG_HASH_FAILED: return "could not hash the image";
        case OTA_SIG_INVALID:     return "the signature does not match the image";
    }
    return "unknown";
}
