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
// The panel is FIVE INCHES: 1280x720 over 110.7 x 62.3 mm, so 0.0865 mm per pixel. The whole
// display is physically about the size of a human EYE REGION, not a face, which is why
// proportions borrowed from whole-face designs mislead here. Numbers below carry their physical
// size, because that is the only frame in which "too small" means anything.
//
// What the previous geometry got wrong was SPACING, not size. Eye width was 284 px = 24.6 mm,
// already 82% of a human eye's 30 mm visible aperture -- but the eyes sat 380 px = 32.9 mm apart
// against a human interpupillary distance of 63 mm. FIFTY-TWO PERCENT. Tall (3x a human
// aperture), narrow and close together is a small animal's face, not a person-scale robot at
// human eye level, and that -- not size -- is why it did not read right.
//
// Research direction: the baby schema, tested on robot faces specifically, finds large eyes, a
// round face and medium eye positions give the highest perceived trustworthiness. So LARGER than
// human, and rounder -- and roundness happens to be pushed the same way by both the
// human-proportion and baby-schema arguments, which is unusual and worth taking.
constexpr int kScreenW = 1280, kScreenH = 720;

// Human IPD is 63 mm = 729 px on this panel. This is the single biggest change from before.
constexpr int kIpd   = 729;                       // 63.0 mm -- human scale
constexpr int kSlotL = kScreenW / 2 - kIpd / 2;   // 276
constexpr int kSlotR = kScreenW / 2 + kIpd / 2;   // 1004
constexpr int kEyeY  = kScreenH / 2;              // 360, vertically centred

// 400 x 400 = 34.6 x 34.6 mm. Wider than a human aperture (30 mm) per the baby schema, and SQUARE
// rather than the old 0.76:1 portrait. Leaves 76 px of side margin and 160 px top and bottom --
// room for the perimeter band and for the quote strip.
constexpr int kBodyW = 400, kBodyH = 400;
constexpr float kRadiusFrac = 0.205f;   // of min(w,h); was 58/284 on the old body

constexpr float kPupilFrac = 0.34f;
constexpr int kRimPad = 14;             // the pupil must never touch the rim

struct Eye {
    lv_obj_t* body    = nullptr;
    lv_obj_t* pupil   = nullptr;
    lv_obj_t* glint   = nullptr;
    lv_obj_t* lid     = nullptr;
};
Eye g_eye[2];
lv_obj_t* g_label = nullptr;

// ---- animated state --------------------------------------------------------------------------
// Gaze is EASED, not sprung: a saccade is ballistic and must not overshoot or wobble.
AnimateValue g_gazeX(0.0f), g_gazeY(0.0f);
// Shape is SPRUNG: an emote change should settle like something with mass.
AnimateValue g_bodyW((float)kBodyW), g_bodyH((float)kBodyH),
             g_radius((float)kBodyW * kRadiusFrac);
AnimateValue g_lidDrop(0.0f);     // 0 = open, 1 = shut
AnimateValue g_asym(0.0f);        // per-eye width asymmetry, the pupil-less direction cue

// MULTIPLIERS of the neutral body, not absolute pixels, so the entire face rescales from
// kBodyW/kBodyH alone -- which is what made the geometry above a one-line experiment rather than
// a table rewrite. Radius is a fraction of min(w,h) for the same reason.
struct Emote { const char* name; float wMul, hMul, radiusFrac, lid; };
// Deliberately small, and these are STATES rather than emotions -- see notes/design/face-design.md.
const Emote kEmotes[] = {
    {"NEUTRAL",  1.00f, 1.00f, 0.205f, 0.00f},
    {"ALERT",    1.06f, 1.08f, 0.173f, 0.00f},
    {"SQUINT",   1.02f, 0.56f, 0.429f, 0.00f},
    {"SOFT",     0.95f, 0.88f, 0.481f, 0.12f},
    {"SLEEPY",   0.99f, 0.40f, 0.733f, 0.35f},
};
constexpr int kNEmotes = sizeof(kEmotes) / sizeof(kEmotes[0]);
int g_emote = 0;

uint32_t g_nextSaccade = 0, g_nextEmote = 0, g_nextBlink = 0;
bool g_blinking = false;
uint32_t g_blinkEnd = 0;

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
        g_eye[i].body = plainBox(scr, lv_color_hex(0x19d1c4), (int)(kBodyW * kRadiusFrac));
        lv_obj_set_size(g_eye[i].body, kBodyW, kBodyH);
        lv_obj_set_pos(g_eye[i].body, (i == 0 ? kSlotL : kSlotR) - kBodyW / 2, kEyeY - kBodyH / 2);

        // The pupil is a CHILD of the body: it inherits clipping, so it can be driven to the rim
        // without ever spilling outside the eye.
        g_eye[i].pupil = plainBox(g_eye[i].body, lv_color_hex(0x04201e), 999);
        g_eye[i].glint = plainBox(g_eye[i].body, lv_color_hex(0xffffff), 999);
        // The eyelid is a real object that descends, not a height squash of the body.
        g_eye[i].lid = plainBox(g_eye[i].body, lv_color_hex(0x000000), 0);
    }

    g_label = lv_label_create(scr);
    lv_obj_set_style_text_color(g_label, lv_color_hex(0x666666), LV_PART_MAIN);
    lv_obj_align(g_label, LV_ALIGN_TOP_MID, 0, 24);
    lv_label_set_text(g_label, "1.1 gate: eased saccades + sprung shape, independent layers");
}

void applyEmote(int idx)
{
    const Emote& e = kEmotes[idx];
    const float w = kBodyW * e.wMul, h = kBodyH * e.hMul;
    g_bodyW  = w;          // assignment animates -- spring by default
    g_bodyH  = h;
    g_radius = (w < h ? w : h) * e.radiusFrac;
    g_lidDrop = e.lid;
    char buf[96];
    snprintf(buf, sizeof(buf), "%s   gaze: eased saccade   shape: spring", e.name);
    lv_label_set_text(g_label, buf);
}

// Main-sequence saccade timing: ~20 ms fixed plus 2.2 ms per degree, 30 deg of full deflection.
// Expressed in seconds because the library works in seconds.
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
    // EASED, with a duration that scales with amplitude. A spring here would overshoot, and an
    // eye that overshoots its target does not read as an eye.
    g_gazeX.easingOptions().duration = dur;
    g_gazeY.easingOptions().duration = dur;
    g_gazeX = tx;
    g_gazeY = ty;

    g_asym = -tx * 0.13f;   // the leading eye foreshortens; sprung, so it settles
    g_nextSaccade = now + 320 + (rand() % 1300);
    if (std::fabs(tx) > 0.55f) g_nextBlink = now;   // large shifts take a blink with them
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

    for (AnimateValue* v : {&g_gazeX, &g_gazeY, &g_bodyW, &g_bodyH, &g_radius, &g_lidDrop, &g_asym})
        v->begin();
    g_gazeX.animationType = animation_type::easing;
    g_gazeY.animationType = animation_type::easing;
    applyEmote(0);
}

void AppFace::onRunning()
{
    const uint32_t now = GetHAL()->millis();

    // Distinguishes three causes of a static face in one line: whether onRunning is called at
    // all, whether the AnimateValues are advancing, and whether the toolkit's tick source is
    // moving. Without this the three are indistinguishable from the outside.
    static uint32_t dbg = 0;
    if (now - dbg > 1000) {
        dbg = now;
        printf("[AppFace] hal_ms=%u ui_tick=%u gaze=%.3f,%.3f bodyW=%.1f lid=%.2f\n",
               (unsigned)now, (unsigned)smooth_ui_toolkit::ui_hal::get_tick(),
               (float)g_gazeX, (float)g_gazeY, (float)g_bodyW, (float)g_lidDrop);
        fflush(stdout);
    }

    if (now > g_nextSaccade) newSaccade(now);
    if (now > g_nextEmote) {
        g_emote = (g_emote + 1) % kNEmotes;
        applyEmote(g_emote);
        g_nextEmote = now + 3200;
    }
    if (!g_blinking && now > g_nextBlink) {
        g_blinking = true;
        g_lidDrop.easingOptions().duration = 0.07f;
        g_lidDrop = 1.0f;
        g_blinkEnd = now + 90;
    } else if (g_blinking && now > g_blinkEnd) {
        g_blinking = false;
        g_lidDrop = kEmotes[g_emote].lid;
        g_nextBlink = now + 2200 + (rand() % 3000);
    }

    GetHAL()->lvglLock();
    const float gx = g_gazeX, gy = g_gazeY, asym = g_asym;
    for (int i = 0; i < 2; ++i) {
        // Per-eye width: the asymmetry between the two eyes is the gaze cue in a pupil-less face,
        // and it costs nothing here because each eye is its own object.
        const int w = (int)((float)g_bodyW * (1.0f + (i == 0 ? -asym : asym)));
        const int h = (int)(float)g_bodyH;
        lv_obj_set_size(g_eye[i].body, w, h);
        lv_obj_set_pos(g_eye[i].body, (i == 0 ? kSlotL : kSlotR) - w / 2, kEyeY - h / 2);
        lv_obj_set_style_radius(g_eye[i].body, (int)(float)g_radius, LV_PART_MAIN);

        // THE POINT: pupil travel is (body - pupil)/2 - rim, not 11 px.
        const int pw = (int)(w * kPupilFrac), ph = pw;
        const int travelX = (w - pw) / 2 - kRimPad;
        const int travelY = (h - ph) / 2 - kRimPad;
        lv_obj_set_size(g_eye[i].pupil, pw, ph);
        lv_obj_align(g_eye[i].pupil, LV_ALIGN_CENTER,
                     (int)(gx * (travelX > 0 ? travelX : 0)),
                     (int)(gy * (travelY > 0 ? travelY : 0)));

        // One highlight, FIXED to the body. A highlight that chases the gaze was tried on the
        // bespoke face and read as confusing.
        lv_obj_set_size(g_eye[i].glint, pw / 3, pw / 3);
        lv_obj_align(g_eye[i].glint, LV_ALIGN_CENTER, -w / 5, -h / 4);

        const int lidH = (int)((float)g_lidDrop * h);
        lv_obj_set_size(g_eye[i].lid, w, lidH < 0 ? 0 : lidH);
        lv_obj_align(g_eye[i].lid, LV_ALIGN_TOP_MID, 0, 0);
    }
    GetHAL()->lvglUnlock();
}

void AppFace::onClose() { mclog::tagInfo(getAppInfo().name, "on close"); }
