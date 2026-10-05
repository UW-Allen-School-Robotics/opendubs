#include "app_face.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <smooth_ui_toolkit.h>
#include <lvgl.h>
#include <cmath>
#include <cstdio>

using namespace mooncake;
using namespace smooth_ui_toolkit;

namespace {

// ---- geometry ------------------------------------------------------------------------------
// The panel is FIVE INCHES: 1280x720 over 110.7 x 62.3 mm, 0.0865 mm per pixel. The whole display
// is physically about the size of a human EYE REGION, not a face, which is why proportions
// borrowed from whole-face designs mislead. Numbers carry their physical size, because that is the
// only frame in which "too small" means anything.
constexpr int kScreenW = 1280, kScreenH = 720;

// SPACING IS A FRACTION OF EYE WIDTH, not millimetres, because that is the dial that controls how
// friendly the face reads. Infants have larger eyes AND proportionally less gap, so "bigger and
// closer" is one coherent move toward the baby schema rather than two opposing ones.
//     adult human (63 mm IPD, 30 mm eye) .... gap ~1.00 eye-widths
//     this face ............................. gap  0.34  -- tried human scale, read less friendly
constexpr int   kBodyW      = 480;      // 41.5 mm, past a human aperture's 30 mm per baby schema
constexpr int   kBodyH      = 480;
constexpr float kGapFrac    = 0.34f;
constexpr float kRadiusFrac = 0.205f;

constexpr int kIpd   = (int)(kBodyW * (1.0f + kGapFrac));
constexpr int kSlotL = kScreenW / 2 - kIpd / 2;
constexpr int kSlotR = kScreenW / 2 + kIpd / 2;
constexpr int kEyeY  = kScreenH / 2;

constexpr float kPupilFrac = 0.34f;
constexpr int   kRimPad    = 14;

// Brows: an lv_line, never a rotated object -- a rotated object is rendered through a LAYER, which
// allocates a buffer the size of its bounding box on every draw and starved the device.
constexpr float kBrowWFrac = 0.86f, kBrowHFrac = 0.085f, kBrowGap = 0.10f, kBrowRange = 0.14f;

// ---- the REAL state machine ----------------------------------------------------------------
// Ported verbatim from ui_shared.cpp so the two faces cannot drift. Expr is {h, w, tilt, bow,
// squint, lookY, brow}, and each parameter gets its OWN AnimateValue rather than one lerp toward
// the whole tuple -- which is what makes a state change read as a change of posture instead of a
// crossfade, and was the point of moving to this toolkit.
struct Expr { float h, w, tilt, bow, squint, lookY, brow; };
struct St   { const char* name; const char* label; uint8_t r, g, b; Expr e; };

const St kStates[] = {
    {"INIT",       "INIT",       223, 231, 240, {0.60f, 1.00f,  0.00f, 0.00f, 0.10f, -0.05f,  0.00f}},
    {"SAFE_IDLE",  "SAFE IDLE",   46, 204,  88, {0.80f, 1.00f,  0.00f, 0.02f, 0.34f,  0.05f, -0.18f}},
    {"MANUAL",     "MANUAL",     242, 246, 251, {1.02f, 1.02f,  0.00f, 0.00f, 0.02f, -0.06f,  0.12f}},
    {"AUTONOMOUS", "AUTONOMOUS",   0, 176, 235, {0.94f, 1.02f,  0.00f, 0.03f, 0.06f, -0.02f, -0.04f}},
    {"DEGRADED",   "DEGRADED",   243, 182,  74, {0.82f, 0.96f, -0.34f, 0.00f, 0.06f,  0.12f,  0.10f}},
    {"E_STOP",     "E-STOP",     255,  77,  77, {1.06f, 1.08f,  0.02f, 0.00f, 0.00f, -0.02f,  0.55f}},
    {"CHARGING",   "CHARGING",     0, 196, 168, {0.92f, 1.00f, -0.02f, 0.05f, 0.06f,  0.00f, -0.08f}},
};
constexpr int kNStates = sizeof(kStates) / sizeof(kStates[0]);

// Which state the face shows. On the robot the StamPLC's "STATE <NAME>" drives this; with no link
// on the bench it cycles, so every state gets looked at.
int      g_state     = 1;          // SAFE_IDLE, as ui_shared.cpp defaults
uint32_t g_nextState = 0;

struct Eye {
    lv_obj_t* body  = nullptr;
    lv_obj_t* pupil = nullptr;
    lv_obj_t* glint = nullptr;
    lv_obj_t* lid   = nullptr;
    lv_obj_t* brow  = nullptr;
    lv_obj_t* cut   = nullptr;     // tilt: an angled wedge off the upper corner
    lv_obj_t* bow   = nullptr;     // bow: a curve carved out of the lower lid
};
Eye g_eye[2];
lv_obj_t* g_label = nullptr;

// WRITE ONLY WHAT CHANGED. Every lv_obj_set_* invalidates the object and schedules a redraw, so
// writing everything unconditionally makes render cost independent of whether anything moved.
struct Last {
    int w = -1, h = -1, y = -1, radius = -1;
    int pupilW = -1, pupilX = -1, pupilY = -1, glintW = -1, glintX = -1, glintY = -1;
    int lidW = -1, lidH = -1;
    int browW = -1, browH = -1, browX = -1, browY = -1, browSpan = 9999;
    int cutShown = -1, cutW = -1, cutH = -1, cutX = -1, cutY = -1;
    int bowShown = -1, bowW = -1, bowH = -1, bowX = -1, bowY = -1;
    uint32_t colour = 0xffffffff;
};
Last g_last[2];

// ---- animated state ------------------------------------------------------------------------
// Gaze is EASED: a saccade is ballistic and must not overshoot. Everything else is SPRUNG, so a
// state change settles like something with mass.
AnimateValue g_gazeX(0.0f), g_gazeY(0.0f);
AnimateValue g_h(1.0f), g_w(1.0f), g_tilt(0.0f), g_bow(0.0f),
             g_squint(0.0f), g_lookY(0.0f), g_brow(0.0f);
AnimateValue g_lidDrop(0.0f);
AnimateValue g_asym(0.0f);
AnimateValue g_colR(46.0f), g_colG(204.0f), g_colB(88.0f);   // colour transitions too

uint32_t g_nextSaccade = 0, g_nextBlink = 0, g_blinkEnd = 0;
bool g_blinking = false;

lv_obj_t* plainBox(lv_obj_t* parent, lv_color_t colour, int radius)
{
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(o, colour, LV_PART_MAIN);
    lv_obj_set_style_radius(o, radius, LV_PART_MAIN);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    return o;
}

void buildScene()
{
    lv_obj_t* scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);

    for (int i = 0; i < 2; ++i) {
        g_eye[i].body = plainBox(scr, lv_color_hex(0x2ecc58), (int)(kBodyW * kRadiusFrac));
        // Radial shading, which is what the bespoke renderer's loop of fourteen concentric
        // ellipses was approximating by hand. Makes the eye read as a sphere, not a disc.
        lv_obj_set_style_bg_grad_dir(g_eye[i].body, LV_GRAD_DIR_RADIAL, LV_PART_MAIN);
        lv_obj_set_style_bg_main_stop(g_eye[i].body, 40, LV_PART_MAIN);
        lv_obj_set_style_bg_grad_stop(g_eye[i].body, 255, LV_PART_MAIN);

        g_eye[i].pupil = plainBox(g_eye[i].body, lv_color_hex(0x04201e), 999);

        // Child of the PUPIL: a catchlight is a corneal reflection over the pupil, so it travels
        // with the gaze for free and can never escape the eye's rounded silhouette.
        g_eye[i].glint = plainBox(g_eye[i].pupil, lv_color_hex(0xffffff), 999);
        lv_obj_set_style_bg_grad_color(g_eye[i].glint, lv_color_hex(0xffffff), LV_PART_MAIN);
        lv_obj_set_style_bg_grad_dir(g_eye[i].glint, LV_GRAD_DIR_RADIAL, LV_PART_MAIN);
        lv_obj_set_style_bg_main_opa(g_eye[i].glint, 235, LV_PART_MAIN);
        lv_obj_set_style_bg_grad_opa(g_eye[i].glint, LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_bg_main_stop(g_eye[i].glint, 20, LV_PART_MAIN);

        // tilt and bow are carved in BACKGROUND colour, exactly as the old renderer carved them
        // with fillTriangle and fillEllipse. Hidden entirely when their parameter is ~0, so they
        // cost nothing in the states that do not use them.
        g_eye[i].cut = plainBox(g_eye[i].body, lv_color_hex(0x000000), 0);
        g_eye[i].bow = plainBox(g_eye[i].body, lv_color_hex(0x000000), 999);
        lv_obj_add_flag(g_eye[i].cut, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(g_eye[i].bow, LV_OBJ_FLAG_HIDDEN);

        g_eye[i].lid = plainBox(g_eye[i].body, lv_color_hex(0x000000), 0);
        lv_obj_move_foreground(g_eye[i].lid);

        // Child of the SCREEN, not the body: as a child it would be clipped by what it sits above.
        g_eye[i].brow = lv_line_create(scr);
        lv_obj_remove_style_all(g_eye[i].brow);
        lv_obj_set_style_line_rounded(g_eye[i].brow, true, LV_PART_MAIN);
    }

    g_label = lv_label_create(scr);
    lv_obj_set_style_text_color(g_label, lv_color_hex(0x777777), LV_PART_MAIN);
    lv_obj_align(g_label, LV_ALIGN_TOP_MID, 0, 22);
    lv_label_set_text(g_label, "");
}

void applyState(int idx)
{
    const St& s = kStates[idx];
    g_h = s.e.h;  g_w = s.e.w;  g_tilt = s.e.tilt;  g_bow = s.e.bow;
    g_squint = s.e.squint;  g_lookY = s.e.lookY;  g_brow = s.e.brow;
    g_colR = (float)s.r;  g_colG = (float)s.g;  g_colB = (float)s.b;
    lv_label_set_text(g_label, s.label);
}

// Main-sequence saccade timing: ~20 ms fixed plus 2.2 ms per degree over 30 degrees of full
// deflection, capped at the physiological 200 ms. In seconds, as the library expects.
float saccadeSeconds(float dx, float dy)
{
    const float deg = std::sqrt(dx * dx + dy * dy) * 30.0f;
    const float ms  = 20.0f + deg * 2.2f;
    return (ms > 200.0f ? 200.0f : ms) / 1000.0f;
}

void newSaccade(uint32_t now)
{
    const long roll = rand() % 100;
    const float amp = (roll < 60) ? (rand() % 25) / 100.0f
                    : (roll < 90) ? (rand() % 35 + 25) / 100.0f
                                  : (rand() % 40 + 60) / 100.0f;
    const float ang = (rand() % 628) / 100.0f;
    const float tx = std::cos(ang) * amp, ty = std::sin(ang) * amp * 0.55f;

    const float dur = saccadeSeconds(tx - (float)g_gazeX, ty - (float)g_gazeY);
    g_gazeX.easingOptions().duration = dur;
    g_gazeY.easingOptions().duration = dur;
    g_gazeX = tx;
    g_gazeY = ty;
    g_asym = -tx * 0.13f;              // the leading eye foreshortens; sprung, so it settles

    // Arousal sets the rate, per Eyes Alive: a busy state looks about more often and holds briefly.
    const bool busy = (g_state == 2 || g_state == 3);   // MANUAL or AUTONOMOUS
    g_nextSaccade = now + (busy ? 200 + (rand() % 500) : 340 + (rand() % 1300));
    if (std::fabs(tx) > 0.55f) g_nextBlink = now;
}

} // namespace

AppFace::AppFace() { setAppInfo().name = "AppFace"; }

void AppFace::onCreate()
{
    mclog::tagInfo(getAppInfo().name, "on create");
    open();
}

void AppFace::onOpen()
{
    mclog::tagInfo(getAppInfo().name, "on open");
    GetHAL()->lvglLock();
    buildScene();
    GetHAL()->lvglUnlock();

    for (AnimateValue* v : {&g_gazeX, &g_gazeY, &g_h, &g_w, &g_tilt, &g_bow, &g_squint,
                            &g_lookY, &g_brow, &g_lidDrop, &g_asym, &g_colR, &g_colG, &g_colB})
        v->begin();
    g_gazeX.animationType = animation_type::easing;
    g_gazeY.animationType = animation_type::easing;

    GetHAL()->lvglLock();
    applyState(g_state);
    GetHAL()->lvglUnlock();
}

void AppFace::onRunning()
{
    const uint32_t now = GetHAL()->millis();

    if (now > g_nextSaccade) newSaccade(now);

    // No StamPLC link on the bench, so walk the states. Phase 2 replaces this with "STATE <NAME>".
    if (now > g_nextState) {
        g_nextState = now + 4000;
        GetHAL()->lvglLock();
        g_state = (g_state + 1) % kNStates;
        applyState(g_state);
        GetHAL()->lvglUnlock();
    }

    if (!g_blinking && now > g_nextBlink) {
        g_blinking = true;
        g_lidDrop.easingOptions().duration = 0.07f;
        g_lidDrop = 1.0f;
        g_blinkEnd = now + 90;
    } else if (g_blinking && now > g_blinkEnd) {
        g_blinking = false;
        g_lidDrop = 0.0f;
        g_nextBlink = now + 2200 + (rand() % 3000);
    }

    GetHAL()->lvglLock();
    const float gx = g_gazeX, gy = g_gazeY, asym = g_asym;
    const float tilt = g_tilt, bow = g_bow, squint = g_squint;
    const uint32_t colour = ((uint32_t)(float)g_colR << 16) | ((uint32_t)(float)g_colG << 8)
                          | (uint32_t)(float)g_colB;

    for (int i = 0; i < 2; ++i) {
        Last& L = g_last[i];
        const int cx = (i == 0 ? kSlotL : kSlotR);

        // squint eats height from the top and shifts the eye centre down by half of what it took,
        // and lookY biases the whole eye vertically -- both exactly as the old renderer did.
        const float baseH = kBodyH * (float)g_h;
        const float squintCut = baseH * squint;
        const int h = (int)(baseH - squintCut);
        const int w = (int)(kBodyW * (float)g_w * (1.0f + (i == 0 ? -asym : asym)));
        const int ey = kEyeY + (int)(squintCut * 0.5f) + (int)((float)g_lookY * 66.0f);

        if (w != L.w || h != L.h || ey != L.y) {
            lv_obj_set_size(g_eye[i].body, w, h);
            lv_obj_set_pos(g_eye[i].body, cx - w / 2, ey - h / 2);
            L.w = w; L.h = h; L.y = ey;
        }
        const int radius = (int)((w < h ? w : h) * kRadiusFrac);
        if (radius != L.radius) {
            lv_obj_set_style_radius(g_eye[i].body, radius, LV_PART_MAIN);
            L.radius = radius;
        }
        if (colour != L.colour) {
            lv_obj_set_style_bg_color(g_eye[i].body, lv_color_hex(colour), LV_PART_MAIN);
            // Rim is the same hue at 35%, which is what gives the sphere its falloff.
            const uint32_t dim = (((colour >> 16 & 0xff) * 35 / 100) << 16)
                               | (((colour >> 8 & 0xff) * 35 / 100) << 8)
                               | ((colour & 0xff) * 35 / 100);
            lv_obj_set_style_bg_grad_color(g_eye[i].body, lv_color_hex(dim), LV_PART_MAIN);
            lv_obj_set_style_line_color(g_eye[i].brow, lv_color_hex(colour), LV_PART_MAIN);
            L.colour = colour;
        }

        const int pw = (int)(w * kPupilFrac);
        const int travelX = (w - pw) / 2 - kRimPad, travelY = (h - pw) / 2 - kRimPad;
        const int px = (int)(gx * (travelX > 0 ? travelX : 0));
        const int py = (int)(gy * (travelY > 0 ? travelY : 0));
        if (pw != L.pupilW) { lv_obj_set_size(g_eye[i].pupil, pw, pw); L.pupilW = pw; }
        if (px != L.pupilX || py != L.pupilY) {
            lv_obj_align(g_eye[i].pupil, LV_ALIGN_CENTER, px, py);
            L.pupilX = px; L.pupilY = py;
        }

        const int gw = pw / 3, gxo = -pw / 6, gyo = -pw / 6;
        if (gw != L.glintW) { lv_obj_set_size(g_eye[i].glint, gw, gw); L.glintW = gw; }
        if (gxo != L.glintX || gyo != L.glintY) {
            lv_obj_align(g_eye[i].glint, LV_ALIGN_CENTER, gxo, gyo);
            L.glintX = gxo; L.glintY = gyo;
        }

        // TILT drives the brow angle, as it did in the old renderer (innerY = +tilt*90,
        // outerY = -tilt*34), and also carves the upper corner below.
        const int bw = (int)(w * kBrowWFrac);
        const int bh0 = (int)(h * kBrowHFrac), bh = bh0 < 6 ? 6 : bh0;
        const int rise = (int)(tilt * 62.0f);          // combined inner/outer swing
        const int span = rise < 0 ? -rise : rise;
        const int objH = bh + 2 * span;
        const int bx = cx - bw / 2;
        const int by = ey - h / 2 - (int)(h * kBrowGap) - objH
                     - (int)((float)g_brow * h * kBrowRange);
        static lv_point_precise_t pts[2][2];
        const int mid = objH / 2;
        pts[i][0].x = (i == 0) ? 0 : bw;   pts[i][0].y = mid - rise;   // outer
        pts[i][1].x = (i == 0) ? bw : 0;   pts[i][1].y = mid + rise;   // inner
        if (bw != L.browW || bh != L.browH || objH != L.browSpan) {
            lv_obj_set_style_line_width(g_eye[i].brow, bh, LV_PART_MAIN);
            lv_obj_set_size(g_eye[i].brow, bw + bh, objH + bh);
            L.browW = bw; L.browH = bh; L.browSpan = objH;
        }
        if (bx != L.browX || by != L.browY) {
            lv_obj_set_pos(g_eye[i].brow, bx, by);
            L.browX = bx; L.browY = by;
        }
        lv_line_set_points(g_eye[i].brow, pts[i], 2);

        // TILT's corner cut. The old renderer chose which corner from the sign of tilt and which
        // eye, so a negative tilt cuts the outer corners and a positive one the inner.
        const int cutMag = (int)(std::fabs(tilt) * 235.0f);
        const int cutShown = (cutMag > 3 && h > 40) ? 1 : 0;
        if (cutShown != L.cutShown) {
            if (cutShown) lv_obj_clear_flag(g_eye[i].cut, LV_OBJ_FLAG_HIDDEN);
            else          lv_obj_add_flag(g_eye[i].cut, LV_OBJ_FLAG_HIDDEN);
            L.cutShown = cutShown;
        }
        if (cutShown) {
            const int cwid = (int)(cutMag * 0.95f), chei = cutMag;
            const bool cutLeft = (tilt < 0) ? (i == 0) : (i != 0);
            const int cxp = cutLeft ? 0 : w - cwid;
            if (cwid != L.cutW || chei != L.cutH || cxp != L.cutX) {
                lv_obj_set_size(g_eye[i].cut, cwid, chei);
                lv_obj_align(g_eye[i].cut, LV_ALIGN_TOP_LEFT, cxp, 0);
                L.cutW = cwid; L.cutH = chei; L.cutX = cxp;
            }
        }

        // BOW: a curve carved out of the lower lid, as fillEllipse did.
        const int bowMag = (int)(bow * 230.0f);
        const int bowShown = (bowMag > 3 && h > 40) ? 1 : 0;
        if (bowShown != L.bowShown) {
            if (bowShown) lv_obj_clear_flag(g_eye[i].bow, LV_OBJ_FLAG_HIDDEN);
            else          lv_obj_add_flag(g_eye[i].bow, LV_OBJ_FLAG_HIDDEN);
            L.bowShown = bowShown;
        }
        if (bowShown) {
            const int bwid = (int)(w * 1.20f), bhei = (int)(h * 0.90f);
            const int byp = h - bowMag;
            if (bwid != L.bowW || bhei != L.bowH || byp != L.bowY) {
                lv_obj_set_size(g_eye[i].bow, bwid, bhei);
                lv_obj_align(g_eye[i].bow, LV_ALIGN_TOP_MID, 0, byp);
                L.bowW = bwid; L.bowH = bhei; L.bowY = byp;
            }
        }

        const int lidH = (int)((float)g_lidDrop * h), lh = lidH < 0 ? 0 : lidH;
        if (w != L.lidW || lh != L.lidH) {
            lv_obj_set_size(g_eye[i].lid, w, lh);
            lv_obj_align(g_eye[i].lid, LV_ALIGN_TOP_MID, 0, 0);
            L.lidW = w; L.lidH = lh;
        }
    }
    GetHAL()->lvglUnlock();
}

void AppFace::onClose() { mclog::tagInfo(getAppInfo().name, "on close"); }
