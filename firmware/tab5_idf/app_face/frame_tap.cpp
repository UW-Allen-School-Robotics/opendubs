#include "frame_tap.h"

#ifndef ESP_PLATFORM

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <lvgl.h>
#include <sys/mman.h>
#include <unistd.h>

namespace {

constexpr uint32_t kOutW = 320, kOutH = 180;      // a quarter of the 1280x720 panel each way
constexpr uint32_t kMinPeriodMs = 66;             // ~15 fps: a preview, not a second display
constexpr uint32_t kHeaderBytes = 32;

struct Header {
    char     magic[4];
    uint32_t version, width, height, stride;
    volatile uint32_t seq;
    uint32_t reserved[2];
};
static_assert(sizeof(Header) == kHeaderBytes, "the frame header is a published layout");

Header*  g_hdr  = nullptr;
uint8_t* g_px   = nullptr;
uint32_t g_last = 0;

void onRefrReady(lv_event_t*)
{
    const uint32_t now = lv_tick_get();
    if (now - g_last < kMinPeriodMs) return;
    lv_display_t* d = lv_display_get_default();
    lv_draw_buf_t* b = d ? lv_display_get_buf_active(d) : nullptr;
    if (!b || !b->data || b->header.cf != LV_COLOR_FORMAT_RGB565) return;
    g_last = now;

    const uint32_t W = b->header.w, H = b->header.h, S = b->header.stride;
    g_hdr->seq = g_hdr->seq + 1;                   // odd: a frame is being written
    __sync_synchronize();
    for (uint32_t y = 0; y < kOutH; ++y) {
        const uint16_t* row = (const uint16_t*)(b->data + (size_t)(y * H / kOutH) * S);
        uint8_t* out = g_px + (size_t)y * kOutW * 3;
        for (uint32_t x = 0; x < kOutW; ++x) {
            const uint16_t p = row[x * W / kOutW];
            const uint8_t r = (p >> 11) & 0x1f, g = (p >> 5) & 0x3f, bl = p & 0x1f;
            out[3 * x]     = (uint8_t)((r << 3) | (r >> 2));
            out[3 * x + 1] = (uint8_t)((g << 2) | (g >> 4));
            out[3 * x + 2] = (uint8_t)((bl << 3) | (bl >> 2));
        }
    }
    __sync_synchronize();
    g_hdr->seq = g_hdr->seq + 1;                   // even: whole
}

} // namespace

void frameTapBegin()
{
    const char* path = getenv("OPENDUBS_FACE_FRAMES");
    if (!path || !*path) return;
    lv_display_t* d = lv_display_get_default();
    if (!d) { printf("[frames] no display; not sharing frames\n"); return; }

    const size_t size = kHeaderBytes + (size_t)kOutW * kOutH * 3;
    const int fd = open(path, O_RDWR | O_CREAT, 0644);
    if (fd < 0 || ftruncate(fd, (off_t)size) != 0) {
        printf("[frames] cannot create %s; not sharing frames\n", path);
        if (fd >= 0) close(fd);
        return;
    }
    void* m = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) { printf("[frames] cannot map %s; not sharing frames\n", path); return; }

    g_hdr = (Header*)m;
    g_px  = (uint8_t*)m + kHeaderBytes;
    memcpy(g_hdr->magic, "ODFF", 4);
    g_hdr->version = 1;
    g_hdr->width   = kOutW;
    g_hdr->height  = kOutH;
    g_hdr->stride  = kOutW * 3;
    g_hdr->seq     = 0;
    g_hdr->reserved[0] = g_hdr->reserved[1] = 0;
    lv_display_add_event_cb(d, onRefrReady, LV_EVENT_REFR_READY, nullptr);
    printf("[frames] sharing %ux%u frames in %s (~%u fps)\n", kOutW, kOutH, path, 1000 / kMinPeriodMs);
    fflush(stdout);
}

#endif // !ESP_PLATFORM
