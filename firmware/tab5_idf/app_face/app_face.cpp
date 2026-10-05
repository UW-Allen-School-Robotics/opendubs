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

// Panel geometry. The eyes sit where the bespoke face put them, so the comparison is fair.
constexpr int kScreenW = 1280, kScreenH = 720;
constexpr int kEyeY    = 376;
constexpr int kSlotL   = 450, kSlotR = 830;

// Eye body and pupil. The pupil is deliberately a fraction of the body: the ratio IS the gaze
// budget, and this is the whole point of the exercise.
constexpr int kBodyW = 284, kBodyH = 376;
constexpr float kPupilFrac = 0.34f;
constexpr int kRimPad = 14;   // never let the pupil touch the rim; it destroys the illusion

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
AnimateValue g_bodyW((float)kBodyW), g_bodyH((float)kBodyH), g_radius(58.0f);
AnimateValue g_lidDrop(0.0f);     // 0 = open, 1 = shut
AnimateValue g_asym(0.0f);        // per-eye width asymmetry, the pupil-less direction cue

struct Emote { const char* name; float w, h, radius, lid; };
// A deliberately small set. These are STATES, not emotions -- see notes/design/face-design.md.
const Emote kEmotes[] = {
    {"NEUTRAL",   284, 376,  58, 0.00f},
    {"ALERT",     300, 406,  52, 0.00f},
    {"SQUINT",    290, 210,  90, 0.00f},
    {"SOFT",      270, 330, 130, 0.12f},
    {"SLEEPY",    280, 150, 110, 0.35f},
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
        g_eye[i].body = plainBox(scr, lv_color_hex(0x19d1c4), 58);
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
    g_bodyW  = e.w;        // assignment animates -- spring by default
    g_bodyH  = e.h;
    g_radius = e.radius;
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
