#include "rc_frame.h"
#include <cstring>

namespace neck {

bool parseRcFrame(const uint8_t* b, size_t n, float& panDps, float& tiltDps)
{
    if (!b || n != kRcFrameLen) return false;
    const uint32_t id = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    if (id != kRcFrameId || b[4] != 4) return false;
    int16_t p = 0, t = 0;
    std::memcpy(&p, b + 5, 2);
    std::memcpy(&t, b + 7, 2);
    panDps = (float)p;
    tiltDps = (float)t;
    return true;
}

}  // namespace neck
