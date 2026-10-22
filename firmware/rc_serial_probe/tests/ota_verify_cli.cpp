// Host harness for src/ota_verify.cpp: verify IMAGE against SIG with the compiled-in key, feeding
// the image in 1 KiB chunks as the OTA handler does, and print the result's name.
#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include "ota_verify.h"

static std::vector<uint8_t> slurp(const char* p)
{
    std::vector<uint8_t> b;
    FILE* f = fopen(p, "rb");
    if (!f) return b;
    uint8_t buf[4096]; size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) b.insert(b.end(), buf, buf + n);
    fclose(f);
    return b;
}

int main(int argc, char** argv)
{
    if (argc != 3) { fprintf(stderr, "usage: %s IMAGE SIG\n", argv[0]); return 2; }
    std::vector<uint8_t> img = slurp(argv[1]), sig = slurp(argv[2]);
    static const char* names[] = {"OK", "NO_KEY", "BAD_KEY", "MISSING", "HASH_FAILED", "INVALID"};
    OtaVerify v;
    if (!otaVerifyBegin(v)) { puts("HASH_FAILED"); return 0; }
    for (size_t i = 0; i < img.size(); i += 1024)
        otaVerifyUpdate(v, img.data() + i, img.size() - i < 1024 ? img.size() - i : 1024);
    const uint8_t* der; size_t der_len;
    otaVerifyKey(&der, &der_len);
    OtaVerifyResult r = otaVerifyFinish(v, sig.empty() ? nullptr : sig.data(), sig.size(), der, der_len);
    puts(names[r]);
    return 0;
}
