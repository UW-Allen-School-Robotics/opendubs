#include "app_face.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <smooth_ui_toolkit.h>
#include <lvgl.h>
#include <cmath>
#include <cstdio>
#include <string>

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

// ---- states and emotes are DIFFERENT THINGS ------------------------------------------------
// An earlier version mapped one state to one expression, which conflated them. A state is
// mostly a COLOUR plus a policy; an emote is an EXPRESSION, and in the calm states the face
// should be free to be emotive rather than locked to a posture.
//
// The split is also what the research supports. Categorical labels and continuous
// valence/arousal are better together than either alone (CAGE; Handrich et al.), so emotes keep
// NAMES -- which is what makes them designable and discussable -- and each also carries a point
// in valence/arousal space. Arousal is not decoration: per "Eyes Alive" it sets the SACCADE RATE,
// so the dimensional axis does real work rather than labelling.
struct Expr { float h, w, tilt, bow, squint, lookY, brow; };

// valence -1..+1 (unpleasant..pleasant), arousal 0..1 (calm..activated)
struct Emote { const char* name; Expr e; float valence, arousal; };

enum EmoteId {
    EM_NEUTRAL = 0, EM_ALERT, EM_SQUINT, EM_SOFT, EM_SLEEPY, EM_HAPPY,
    EM_INIT, EM_DEGRADED, EM_ESTOP, EM_CHARGING, EM_COUNT
};

// The first six are the FREE vocabulary -- these are the ones worth keeping from the earlier
// presets, now expressed in the same seven parameters as everything else. The last four are
// state-locked postures, carried over from ui_shared.cpp so nothing is lost in the move.
const Emote kEmotes[EM_COUNT] = {
    // name          h      w     tilt   bow   squint  lookY   brow    val    aro
    {"NEUTRAL",  {1.00f, 1.00f,  0.00f, 0.00f, 0.00f,  0.00f,  0.00f},  0.00f, 0.30f},
    {"ALERT",    {1.08f, 1.06f, -0.03f, 0.00f, 0.00f, -0.02f,  1.00f},  0.10f, 0.90f},
    {"SQUINT",   {0.95f, 1.02f,  0.12f, 0.00f, 0.40f,  0.00f, -0.45f}, -0.30f, 0.60f},
    {"SOFT",     {0.88f, 0.95f, -0.09f, 0.10f, 0.00f,  0.02f,  0.30f},  0.60f, 0.25f},
    {"SLEEPY",   {0.45f, 0.99f,  0.00f, 0.00f, 0.35f,  0.04f, -0.60f},  0.10f, 0.05f},
    {"HAPPY",    {0.78f, 1.00f, -0.04f, 0.30f, 0.00f,  0.00f,  0.35f},  0.90f, 0.60f},
    // state-locked
    {"INIT",     {0.60f, 1.00f,  0.00f, 0.00f, 0.10f, -0.05f,  0.00f},  0.00f, 0.20f},
    {"DEGRADED", {0.82f, 0.96f, -0.34f, 0.00f, 0.06f,  0.12f,  0.10f}, -0.60f, 0.50f},
    {"E_STOP",   {1.06f, 1.08f,  0.02f, 0.00f, 0.00f, -0.02f,  0.55f}, -0.50f, 1.00f},
    {"CHARGING", {0.92f, 1.00f, -0.02f, 0.05f, 0.06f,  0.00f, -0.08f},  0.30f, 0.10f},
};

// A state owns the COLOUR and decides whether the face may emote freely. E_STOP and DEGRADED are
// locked deliberately: those postures are communicating a condition, and a robot that looks
// cheerful while degraded is lying.
struct St { const char* name; const char* label; uint8_t r, g, b; bool freeEmotes; EmoteId locked; };

const St kStates[] = {
    {"INIT",       "INIT",       223, 231, 240, false, EM_INIT},
    {"SAFE_IDLE",  "SAFE IDLE",   46, 204,  88, true,  EM_NEUTRAL},
    {"MANUAL",     "MANUAL",     242, 246, 251, true,  EM_NEUTRAL},
    {"AUTONOMOUS", "AUTONOMOUS",   0, 176, 235, true,  EM_NEUTRAL},
    {"DEGRADED",   "DEGRADED",   243, 182,  74, false, EM_DEGRADED},
    {"E_STOP",     "E-STOP",     255,  77,  77, false, EM_ESTOP},
    {"CHARGING",   "CHARGING",     0, 196, 168, false, EM_CHARGING},
};
constexpr int kNStates = sizeof(kStates) / sizeof(kStates[0]);

// The free vocabulary is the first six entries; locked postures are not chosen at random.
constexpr int kNFreeEmotes = EM_HAPPY + 1;

int      g_state     = 1;          // SAFE_IDLE, as ui_shared.cpp defaults
int      g_emote     = EM_NEUTRAL;
uint32_t g_nextState = 0, g_nextEmote = 0;
float    g_arousal   = 0.30f;      // drives saccade rate

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

void applyEmote(EmoteId id)
{
    const Expr& e = kEmotes[id].e;
    g_h = e.h;  g_w = e.w;  g_tilt = e.tilt;  g_bow = e.bow;
    g_squint = e.squint;  g_lookY = e.lookY;  g_brow = e.brow;
    g_arousal = kEmotes[id].arousal;
    g_emote = id;
}

void applyState(int idx)
{
    const St& s = kStates[idx];
    // COLOUR IS EASED, NOT SPRUNG. R, G and B were three independent springs, and a spring
    // overshoots -- an overshoot in RGB lands on a genuinely different hue, which is why
    // transitions flashed through other states' colours on the way. Easing cannot overshoot.
    g_colR = (float)s.r;  g_colG = (float)s.g;  g_colB = (float)s.b;
    if (!s.freeEmotes) applyEmote(s.locked);
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

    // AROUSAL SETS THE RATE, per Eyes Alive: activated looks about often and holds briefly, calm
    // holds for much longer. This is what makes the dimensional axis functional rather than a
    // label -- an ALERT face and a SLEEPY one differ in timing, not only in shape.
    const float hold = 1500.0f - g_arousal * 1250.0f;         // ~250 ms at 1.0, ~1500 ms at 0.0
    g_nextSaccade = now + (uint32_t)(hold * 0.5f) + (rand() % (uint32_t)(hold + 1));
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
    for (AnimateValue* v : {&g_colR, &g_colG, &g_colB}) {
        v->animationType = animation_type::easing;
        v->easingOptions().duration = 0.45f;
    }

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
        g_nextState = now + 7000;
        GetHAL()->lvglLock();
        g_state = (g_state + 1) % kNStates;
        applyState(g_state);
        GetHAL()->lvglUnlock();
        g_nextEmote = now + 2200;
    }
    // In a free state the face is allowed to be emotive. Later this is driven by what the robot
    // notices -- a person, a sound, a task event -- which is the whole point of the attention
    // model; cycling is a stand-in so the vocabulary can be judged.
    if (kStates[g_state].freeEmotes && now > g_nextEmote) {
        g_nextEmote = now + 2400;
        GetHAL()->lvglLock();
        applyEmote((EmoteId)((g_emote + 1) % kNFreeEmotes));
        lv_label_set_text(g_label, (std::string(kStates[g_state].label) + "  ·  "
                                    + kEmotes[g_emote].name).c_str());
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
