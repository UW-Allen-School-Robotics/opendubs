#include "chest.h"

#include <cctype>
#include <cstdlib>
#include <cstring>

namespace neck {
namespace {

const char* skipSpace(const char* p) { while (*p == ' ' || *p == '\t') ++p; return p; }

bool onlySpace(const char* p) { p = skipSpace(p); return *p == '\0' || *p == '\r' || *p == '\n'; }

}  // namespace

bool parseChest(const char* args, uint16_t* us)
{
    if (!args || !us) return false;
    const char* p = skipSpace(args);
    if (*p == '\0') return false;

    if (!strncasecmp(p, "OFF", 3) && onlySpace(p + 3)) { *us = kChestOffUs; return true; }

    // strtol, not atoi or sscanf: it is the only one that says WHERE it stopped, which is how
    // "1500x" and "1500 2000" are refused rather than silently read as 1500. A light showing the
    // wrong colour because of a typo nobody was told about is exactly the failure this part is
    // meant to prevent -- it exists to report the robot's state.
    char* end = nullptr;
    const long v = strtol(p, &end, 10);
    if (end == p || !onlySpace(end)) return false;
    if (v < kChestMinUs || v > kChestMaxUs) return false;

    *us = (uint16_t)v;
    return true;
}

}  // namespace neck
