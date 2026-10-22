// Host harness for src/ota_verify.cpp.
//   ota_verify_cli --selftest                         run the boot self-test, print PASS or FAIL
//   ota_verify_cli BOARD IMAGE SIG [KEY.der ...]      verify, feeding the image in 1 KiB chunks as
//                                                     the boards do; print the result and the key index
#include <stdio.h>
#include <string.h>
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
    if (argc == 2 && !strcmp(argv[1], "--selftest")) { puts(otaVerifySelfTest() ? "PASS" : "FAIL"); return 0; }
    if (argc < 4) { fprintf(stderr, "usage: %s BOARD IMAGE SIG [KEY.der ...]\n", argv[0]); return 2; }
    std::vector<uint8_t> img = slurp(argv[2]), sig = slurp(argv[3]);
    std::vector<std::vector<uint8_t>> ders;
    for (int i = 4; i < argc; i++) ders.push_back(slurp(argv[i]));
    std::vector<OtaKey> keys;
    for (auto& d : ders) keys.push_back({d.data(), d.size()});
    static const char* names[] = {"OK", "NO_KEY", "BAD_KEY", "MISSING", "HASH_FAILED", "INVALID"};
    OtaVerify v;
    if (!otaVerifyBegin(v, argv[1])) { puts("HASH_FAILED"); return 0; }
    for (size_t i = 0; i < img.size(); i += 1024)
        otaVerifyUpdate(v, img.data() + i, img.size() - i < 1024 ? img.size() - i : 1024);
    int which = -1;
    OtaVerifyResult r = otaVerifyFinish(v, sig.empty() ? nullptr : sig.data(), sig.size(),
                                        keys.empty() ? nullptr : keys.data(), keys.size(), &which);
    if (r == OTA_SIG_OK) printf("OK key%d\n", which); else puts(names[r]);
    return 0;
}
