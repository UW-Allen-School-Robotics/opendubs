// LARGE BUFFERS GO TO PSRAM, EXPLICITLY, THROUGH ONE FUNCTION.
//
// CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 (read from sdkconfig.vendor) means every allocation
// under 16 KB lands in INTERNAL DRAM, of which there is ~250-310 KB against 32 MB of PSRAM.
// Internal DRAM is the binding resource on this chip, and a plain `malloc` or a std::vector is a
// silent policy decision about it: cues.cpp's 13.4 KB `Heard` buffer is internal and its 65 KB
// `Mode` buffer is PSRAM, decided by a threshold nobody wrote down.
//
// So nothing in the perception layer calls malloc. It calls bigAlloc(), which names the capability,
// and AUDIT.md's `grep -rnE 'malloc\(|calloc\('` has exactly one hit per platform to look at.
//
// bigAlloc is NOT for small objects. Everything in percept/ is allocated once at startup and never
// freed; there is no steady-state allocation anywhere in this layer (AUDIT.md invariant 6).
#pragma once
#include <stddef.h>

#ifdef ESP_PLATFORM
#include <esp_heap_caps.h>
inline void* bigAlloc(size_t bytes)
{
    // MALLOC_CAP_SPIRAM, not MALLOC_CAP_DEFAULT: the allocator would otherwise be free to satisfy
    // this from internal DRAM, which is the one thing we are trying to protect.
    return heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
}
inline void bigFree(void* p)
{
    heap_caps_free(p);
}
#else
#include <stdlib.h>
inline void* bigAlloc(size_t bytes)
{
    return malloc(bytes);
}
inline void bigFree(void* p)
{
    free(p);
}
#endif
