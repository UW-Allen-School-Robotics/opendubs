#include "walkin.h"

#include "percept/percept_config.h"

#ifndef OPENDUBS_WALKIN

void walkinBuild() {}
bool walkinTick(unsigned) { return false; }

#else

#include <lvgl.h>
#include <cstdio>
#include <cstring>
#include "percept/percept.h"

namespace {

lv_obj_t* g_bg    = nullptr;   // full-screen colour: the signal visible from across a room
lv_obj_t* g_kind  = nullptr;   // 44 pt: NOTHING / MOTION / FACE
lv_obj_t* g_num   = nullptr;   // 44 pt: the numbers that matter
lv_obj_t* g_imu   = nullptr;   // 44 pt: the IMU answer, which had no readout at all before

char g_lastKind[24] = "";
char g_lastNum[72]  = "";
char g_lastImu[48]  = "";

lv_obj_t* bigLabel(lv_obj_t* parent, int yOffset, uint32_t rgb)
{
    lv_obj_t* l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_44, LV_PART_MAIN);
    lv_obj_set_style_text_color(l, lv_color_hex(rgb), LV_PART_MAIN);
    lv_obj_align(l, LV_ALIGN_CENTER, 0, yOffset);
    lv_label_set_text_static(l, "");
    return l;
}

}  // namespace

void walkinBuild()
{
    lv_obj_t* scr = lv_screen_active();

    // Full-screen, behind everything. A plain styled object, never an image or a gradient: this
    // repaints on every state change and must stay a single flat fill.
    g_bg = lv_obj_create(scr);
    lv_obj_remove_style_all(g_bg);
    lv_obj_set_size(g_bg, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_opa(g_bg, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(g_bg, lv_color_hex(0x101010), 0);
    lv_obj_move_background(g_bg);

    g_kind = bigLabel(scr, -120, 0xffffff);
    g_num  = bigLabel(scr,  -20, 0xffffff);
    g_imu  = bigLabel(scr,   90, 0xffd24a);
}

bool walkinTick(unsigned)
{
    if (!g_bg) return true;

    GazeIntent g = {};
    const bool have = perceptGaze(&g);

    // THE COLOUR IS THE MEASUREMENT. Black means nothing is attended to; amber means motion has
    // cleared the floor; cyan means a confirmed face. Walking in until the panel changes colour is
    // the entire test, and it works at whatever distance the panel is visible at.
    uint32_t    bg   = 0x101010;
    const char* kind = "NOTHING";
    if (have) {
        switch (g.kind) {
            case GAZE_FACE:   bg = 0x0b5f73; kind = "FACE";    break;
            case GAZE_MOTION: bg = 0x7a4a00; kind = "MOTION";  break;
            default:          bg = 0x101010; kind = "NOTHING"; break;
        }
    }

    if (strcmp(kind, g_lastKind) != 0) {
        snprintf(g_lastKind, sizeof(g_lastKind), "%s", kind);
        lv_obj_set_style_bg_color(g_bg, lv_color_hex(bg), 0);
        lv_label_set_text(g_kind, kind);
    }

    // perceptStatus() already carries everything; the problem was never the data, it was that it
    // rendered at a size nobody could read. Pull the two numbers that matter out of it and show
    // them large: how confident, and where across the frame.
    char num[72];
    if (have) snprintf(num, sizeof(num), "conf %.2f   x %+.2f", g.confidence, g.nx);
    else      snprintf(num, sizeof(num), "-");
    if (strcmp(num, g_lastNum) != 0) {
        snprintf(g_lastNum, sizeof(g_lastNum), "%s", num);
        lv_label_set_text(g_num, num);
    }

    // The IMU, which I asked to be verified without ever providing a way to see it. The status
    // line reports "gate:noimu" when four consecutive reads come back zero; surfacing that here
    // makes it a glance. If this says NOIMU the gate is disabled and self-motion is NOT suppressed.
    const char* st = perceptStatus();
    char imu[48];
    if (strstr(st, "gate:noimu")) {
        snprintf(imu, sizeof(imu), "IMU: NOT READING");
    } else {
        const char* d = strstr(st, "dps");
        // walk back over the number that precedes "dps" in the status line
        if (d) {
            const char* b = d;
            while (b > st && (*(b - 1) == '.' || *(b - 1) == '-' || (*(b - 1) >= '0' && *(b - 1) <= '9'))) --b;
            int n = (int)(d - b);
            if (n > 0 && n < 12) snprintf(imu, sizeof(imu), "IMU: %.*s dps", n, b);
            else                 snprintf(imu, sizeof(imu), "IMU: ok");
        } else {
            snprintf(imu, sizeof(imu), "IMU: ?");
        }
    }
    if (strcmp(imu, g_lastImu) != 0) {
        snprintf(g_lastImu, sizeof(g_lastImu), "%s", imu);
        lv_label_set_text(g_imu, imu);
    }
    return true;
}

#endif  // OPENDUBS_WALKIN
