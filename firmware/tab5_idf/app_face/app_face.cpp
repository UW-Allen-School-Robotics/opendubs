#include "app_face.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <smooth_ui_toolkit.h>
#include <lvgl.h>
#include <cmath>
#include <math.h>
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

// SPACING IS SET AS A FRACTION OF EYE WIDTH, not in millimetres, because that is the dial that
// controls how friendly the face reads. Infants have larger eyes AND proportionally less gap
// between them, so "bigger and closer together" is a single coherent move toward the baby schema
// rather than two opposing ones.
//
//     adult human (63 mm IPD, 30 mm eye) ..... gap ~1.0 eye-widths
//     the original OpenDubs face ............. gap  0.34  -- read as cute
//     human-scale spacing, tried and rejected  gap  0.82  -- read as human but less friendly
//
// Optimising for human accuracy cost friendliness, which is the wrong trade for this robot.
constexpr int   kBodyW    = 480;        // 41.5 mm -- well past a human aperture's 30 mm
constexpr int   kBodyH    = 480;        // square: rounder than the old 0.76:1 portrait
constexpr float kGapFrac  = 0.34f;      // inner gap as a fraction of eye width -- THE dial
constexpr float kRadiusFrac = 0.205f;   // corner radius as a fraction of min(w,h)

constexpr int kIpd   = (int)(kBodyW * (1.0f + kGapFrac));   // 643 px = 55.6 mm
constexpr int kSlotL = kScreenW / 2 - kIpd / 2;             // 318
constexpr int kSlotR = kScreenW / 2 + kIpd / 2;             // 962
constexpr int kEyeY  = kScreenH / 2;                        // 360

constexpr float kPupilFrac = 0.34f;
constexpr int kRimPad = 14;             // the pupil must never touch the rim

// ---- eyebrows --------------------------------------------------------------------------------
// Added because they were missing entirely from this app. drawBrow() exists in the OLD Arduino
// face.cpp; nothing here drew one, so "I still don't see eyebrows" was simply correct.
//
// Worth the effort on evidence rather than taste: minimal-face work names brows as the emotion
// INTENSIFIER ("strongly pulled-down eyebrows for anger"), and a study of animated head gestures
// found eyebrow-raising measurably increased SPEECH INTELLIGIBILITY versus the same gestures being
// absent or added randomly -- which gives them a functional payoff once the robot speaks.
constexpr float kBrowWFrac = 0.86f;     // of eye width
constexpr float kBrowHFrac = 0.085f;    // of eye height
constexpr float kBrowGap   = 0.10f;     // resting clearance above the eye, as a fraction of height
constexpr float kBrowRange = 0.14f;     // how far raise = +/-1 moves it, as a fraction of height

// WRITE ONLY WHAT CHANGED. Every lv_obj_set_* call invalidates the object and schedules a
// redraw, so writing all of these unconditionally every frame makes the render cost independent
// of whether anything actually moved. With a rotated brow in the mix that was enough to starve
// the idle task and trip the task watchdog on the device ("IDLE0 (CPU 0)", main hogging CPU 0) --
// while the desktop had the headroom to hide it. Caching the last written value per property is
// the fix, and it is the right shape regardless.
struct Last {
    int w = -1, h = -1, radius = -1;
    int pupilW = -1, pupilX = -1, pupilY = -1;
    int glintW = -1, glintX = -1, glintY = -1;
    int lidW = -1, lidH = -1;
    int browW = -1, browH = -1, browX = -1, browY = -1, browRot = 9999;
    uint32_t colour = 0xffffffff;
};
Last g_last[2];

struct Eye {
    lv_obj_t* body    = nullptr;
    lv_obj_t* brow    = nullptr;
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
AnimateValue g_browRaise(0.0f), g_browAngle(0.0f);   // sprung, like the rest of the shape
AnimateValue g_asym(0.0f);
// EASED, NOT SPRUNG. R, G and B as independent springs overshoot, and an overshoot in RGB lands
// on a genuinely different hue -- which made transitions flash through other states' colours.
AnimateValue g_colR(46.0f), g_colG(204.0f), g_colB(88.0f);        // per-eye width asymmetry, the pupil-less direction cue

// MULTIPLIERS of the neutral body, not absolute pixels, so the entire face rescales from
// kBodyW/kBodyH alone -- which is what made the geometry above a one-line experiment rather than
// a table rewrite. Radius is a fraction of min(w,h) for the same reason.
struct Emote { const char* name; float wMul, hMul, radiusFrac, lid, browRaise, browAngle; };
// Deliberately small, and these are STATES rather than emotions -- see notes/design/face-design.md.
// browRaise: -1 lowered .. +1 raised.  browAngle: degrees, positive = INNER end down (the
// scowl direction). Mirrored per eye inside the render, so one number describes the pair.
const Emote kEmotes[] = {
    {"NEUTRAL",  1.00f, 1.00f, 0.205f, 0.00f,  0.00f,   0.0f},
    {"ALERT",    1.06f, 1.08f, 0.173f, 0.00f, +1.00f,  -2.0f},
    {"SQUINT",   1.02f, 0.56f, 0.429f, 0.00f, -0.45f,  +9.0f},
    {"SOFT",     0.95f, 0.88f, 0.481f, 0.12f, +0.30f,  -7.0f},
    {"SLEEPY",   0.99f, 0.40f, 0.733f, 0.35f, -0.60f,  +2.0f},
};
// Indices into kEmotes, so the state table can name what it pins.
enum { EM_NEUTRAL = 0, EM_ALERT, EM_SQUINT, EM_SOFT, EM_SLEEPY };
constexpr int kNEmotes = sizeof(kEmotes) / sizeof(kEmotes[0]);
int g_emote = 0;

// ---- states: COLOUR ONLY ---------------------------------------------------------------------
// A state is mostly a colour. Two previous attempts also gave each state an expression, which
// made the face worse both times -- so this carries the colour and the label and nothing else.
// The emote table above is untouched and keeps driving every shape.
//
// Colours copied from ui_shared.cpp so the two faces cannot drift on them.
// A state carries a colour, a label, and WHETHER THE FACE MAY EMOTE FREELY.
//
// The distinction is whether the robot is ATTENDING TO THE WORLD or REPORTING A CONDITION.
// SAFE_IDLE, MANUAL and AUTONOMOUS are attending, so the face is free to be emotive. INIT,
// DEGRADED, E_STOP and CHARGING are reporting, and cycling through moods while reporting one is
// incoherent -- SLEEPY during an e-stop says nothing true about anything. Those states pin one
// appropriate emote.
//
// Pinning the EMOTE does not freeze the FACE: saccades and blinks continue in every state,
// because a robot whose eyes have stopped moving reads as broken rather than as stopped.
//
// The pinned emotes are chosen from the existing vocabulary rather than invented:
//   INIT      -> NEUTRAL, because there is nothing to express yet
//   DEGRADED  -> SQUINT,  the narrowed, troubled form
//   E_STOP    -> ALERT,   wide eyes and high brows, which is ALARM rather than anger
//   CHARGING  -> SLEEPY,  genuinely apt for a robot parked on a charger
struct St { const char* name; const char* label; uint8_t r, g, b; int locked; };  // locked < 0 = free
const St kStates[] = {
    {"INIT",       "INIT",       223, 231, 240, EM_NEUTRAL},
    {"SAFE_IDLE",  "SAFE IDLE",   46, 204,  88, -1},
    {"MANUAL",     "MANUAL",     242, 246, 251, -1},
    {"AUTONOMOUS", "AUTONOMOUS",   0, 176, 235, -1},
    {"DEGRADED",   "DEGRADED",   243, 182,  74, EM_SQUINT},
    {"E_STOP",     "E-STOP",     255,  77,  77, EM_ALERT},
    {"CHARGING",   "CHARGING",     0, 196, 168, EM_SLEEPY},
};
constexpr int kNStates = sizeof(kStates) / sizeof(kStates[0]);
int g_state = 1;                       // SAFE_IDLE, as ui_shared.cpp defaults
uint32_t g_nextState = 0;

uint32_t g_nextSaccade = 0, g_nextEmote = 0, g_nextBlink = 0;
bool g_blinking = false;
uint32_t g_blinkEnd = 0;


// ---- the perimeter band ---------------------------------------------------------------------
// A second channel, so the eyes stop carrying two messages at once. The eyes say WHERE I AM
// LOOKING; the band says WHAT I AM DOING. The band is also peripherally legible -- visible from
// beside the robot or at a glance, where no eye detail is -- which is the half of the audience
// the eyes cannot reach.
//
// COLOUR IS STATE, MOTION IS ACTIVITY. Two independent dimensions on one channel, so neither is
// lost when the other is absent: a state with no activity still shows its colour, and an activity
// with no state change still reads from its motion.
//
// NOT A SAFETY CHANNEL. It is on the Tab5, so it dies with the e-stop and with the link. PRISM is
// the safety annunciator and survives both. Nothing may depend on this band alone.
//
// Built from discrete SEGMENTS with animated opacity rather than a gradient ring or an lv_arc.
// A rotated or arc-filled object is rendered through a layer, which allocates a buffer per draw
// and starved this device once already. A travelling opacity wave over small rectangles gives the
// same sweep with no masks, no rotation and no allocation.
namespace band {

constexpr int kThick = 16;
// BACK TO 48. 120 segments froze the device, and 48 was running. The smoothness came from the
// opacity quantisation below (16 -> 64 levels), which costs nothing, rather than from the object
// count, which evidently does.
constexpr int kTop = 15, kSide = 9;                    // segments per edge, ~83 px each
constexpr int kSegs = 2 * kTop + 2 * kSide;            // 48

lv_obj_t* g_seg[kSegs];
float     g_at[kSegs];                                 // 0..1 position around the ring
uint8_t   g_lastOpa[kSegs];
uint32_t  g_lastColour = 0xffffffff;

enum Activity { IDLE = 0, LISTEN, THINK, SPEAK, FAULT };
Activity g_activity = IDLE;
float    g_phase = 0.0f;
uint32_t g_lastMs = 0;

const char* name(Activity a)
{
    switch (a) {
        case LISTEN: return "listening";
        case THINK:  return "thinking";
        case SPEAK:  return "speaking";
        case FAULT:  return "fault";
        default:     return "idle";
    }
}

void build(lv_obj_t* scr)
{
    int n = 0;
    auto place = [&](int x, int y, int w, int h, float at) {
        lv_obj_t* o = lv_obj_create(scr);
        lv_obj_remove_style_all(o);
        lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_bg_color(o, lv_color_hex(0x2ecc58), LV_PART_MAIN);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_size(o, w, h);
        lv_obj_set_pos(o, x, y);
        g_seg[n] = o; g_at[n] = at; g_lastOpa[n] = 0xff; ++n;
    };
    // Clockwise from the top-left, so a sweep travels the way a clock does.
    const float per = 1.0f / (float)kSegs;
    for (int i = 0; i < kTop;  ++i) place(i * kScreenW / kTop, 0, kScreenW / kTop + 1, kThick, n * per);
    for (int i = 0; i < kSide; ++i) place(kScreenW - kThick, i * kScreenH / kSide, kThick, kScreenH / kSide + 1, n * per);
    for (int i = 0; i < kTop;  ++i) place(kScreenW - (i + 1) * kScreenW / kTop, kScreenH - kThick, kScreenW / kTop + 1, kThick, n * per);
    for (int i = 0; i < kSide; ++i) place(0, kScreenH - (i + 1) * kScreenH / kSide, kThick, kScreenH / kSide + 1, n * per);
}

// Distance around a 0..1 ring, shortest way.
float ringDist(float a, float b)
{
    float d = a - b;
    while (d < -0.5f) d += 1.0f;
    while (d >  0.5f) d -= 1.0f;
    return d < 0 ? -d : d;
}

void update(uint32_t now, uint32_t colour)
{
    const float dt = (g_lastMs == 0) ? 0.0f : (now - g_lastMs) / 1000.0f;
    g_lastMs = now;

    // Motion IS the vocabulary, the same argument as the earcons being rhythm-distinct: a cue
    // separated only by colour is lost on anyone glancing, colour-blind, or at an angle.
    float speed = 0.0f, width = 0.12f, base = 26.0f, peak = 170.0f;
    switch (g_activity) {
        case IDLE:   speed = 0.0f;  base = 22.0f; peak = 54.0f;  break;   // whole ring breathes
        case LISTEN: speed = 0.35f; width = 0.16f; base = 24.0f; peak = 190.0f; break;
        case THINK:  speed = 0.95f; width = 0.07f; base = 18.0f; peak = 235.0f; break;
        case SPEAK:  speed = 0.0f;  base = 60.0f; peak = 220.0f; break;   // envelope, stubbed
        case FAULT:  speed = 0.0f;  base = 0.0f;  peak = 255.0f; break;   // hard flash, all at once
    }
    g_phase += speed * dt;
    while (g_phase > 1.0f) g_phase -= 1.0f;

    const float tsec = now / 1000.0f;
    for (int i = 0; i < kSegs; ++i) {
        float v;
        if (g_activity == FAULT) {
            v = (fmodf(tsec, 0.7f) < 0.35f) ? 1.0f : 0.06f;      // unmistakable, and theme-proof
        } else if (g_activity == IDLE) {
            v = 0.5f + 0.5f * sinf(tsec * 1.1f);                 // slow, whole-ring breath
        } else if (g_activity == SPEAK) {
            v = 0.5f + 0.5f * sinf(tsec * 7.0f);                 // placeholder for the audio envelope
        } else {
            const float d = ringDist(g_at[i], g_phase);
            v = expf(-(d * d) / (2.0f * width * width));         // a travelling bump
        }
        const float opaF = base + (peak - base) * v;
        // Quantised to 64 levels and change-guarded. 16 levels was visibly stepped -- a smooth
        // ramp is exactly what coarse quantisation betrays. The guard is what keeps the cost
        // bounded, not the step size, so the step can be fine.
        const uint8_t opa = (uint8_t)(((int)opaF >> 2) << 2);
        if (opa != g_lastOpa[i]) {
            lv_obj_set_style_bg_opa(g_seg[i], opa, LV_PART_MAIN);
            g_lastOpa[i] = opa;
        }
    }

    if (colour != g_lastColour) {
        for (int i = 0; i < kSegs; ++i)
            lv_obj_set_style_bg_color(g_seg[i], lv_color_hex(colour), LV_PART_MAIN);
        g_lastColour = colour;
    }
}

} // namespace band

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
        g_eye[i].body = plainBox(scr, lv_color_hex(0x22e0d2), (int)(kBodyW * kRadiusFrac));
        // RADIAL SHADING instead of a flat fill. This is what the bespoke renderer's loop of
        // fourteen concentric ellipses was approximating by hand; a gradient does it properly and
        // makes the eye read as a sphere rather than a disc.
        lv_obj_set_style_bg_grad_color(g_eye[i].body, lv_color_hex(0x0b6a64), LV_PART_MAIN);
        lv_obj_set_style_bg_grad_dir(g_eye[i].body, LV_GRAD_DIR_RADIAL, LV_PART_MAIN);
        lv_obj_set_style_bg_main_stop(g_eye[i].body, 40, LV_PART_MAIN);
        lv_obj_set_style_bg_grad_stop(g_eye[i].body, 255, LV_PART_MAIN);
        lv_obj_set_size(g_eye[i].body, kBodyW, kBodyH);
        lv_obj_set_pos(g_eye[i].body, (i == 0 ? kSlotL : kSlotR) - kBodyW / 2, kEyeY - kBodyH / 2);

        // The pupil is a CHILD of the body: it inherits clipping, so it can be driven to the rim
        // without ever spilling outside the eye.
        g_eye[i].pupil = plainBox(g_eye[i].body, lv_color_hex(0x04201e), 999);
        // SOFT SPECULAR, not a hard disc. White at the core fading to fully transparent at the
        // rim -- the hard circle was a limitation of hand-rasterising, not a choice.
        // CHILD OF THE PUPIL. A catchlight is a corneal reflection sitting over the iris and
        // pupil, not a spot floating on the sclera -- and parenting it here means it travels with
        // the pupil for free and can never escape the eye, which retires the clamping that was
        // needed when it was a child of the body.
        g_eye[i].glint = plainBox(g_eye[i].pupil, lv_color_hex(0xffffff), 999);
        lv_obj_set_style_bg_grad_color(g_eye[i].glint, lv_color_hex(0xffffff), LV_PART_MAIN);
        lv_obj_set_style_bg_grad_dir(g_eye[i].glint, LV_GRAD_DIR_RADIAL, LV_PART_MAIN);
        lv_obj_set_style_bg_main_opa(g_eye[i].glint, 235, LV_PART_MAIN);
        lv_obj_set_style_bg_grad_opa(g_eye[i].glint, LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_bg_main_stop(g_eye[i].glint, 20, LV_PART_MAIN);
        // The eyelid is a real object that descends, not a height squash of the body. Created
        // last so it draws over the pupil and glint, and pinned foreground to keep it that way.
        g_eye[i].lid = plainBox(g_eye[i].body, lv_color_hex(0x000000), 0);
        lv_obj_move_foreground(g_eye[i].lid);

        // THE BROW IS A LINE, NOT A ROTATED BOX. A rotated LVGL object is rendered through a
        // layer, which means allocating a buffer the size of its bounding box on every draw --
        // "lv_draw_buf_create_ex: No memory: 438x42 ... 73584Byte" repeating until the idle task
        // starved and the task watchdog fired. An lv_line draws an angled, rounded, thick segment
        // with no transform and no layer allocation at all.
        // Child of the SCREEN, not the body: as a child it would be clipped by what it sits above.
        g_eye[i].brow = lv_line_create(scr);
        lv_obj_remove_style_all(g_eye[i].brow);
        lv_obj_set_style_line_color(g_eye[i].brow, lv_color_hex(0x19d1c4), LV_PART_MAIN);
        lv_obj_set_style_line_rounded(g_eye[i].brow, true, LV_PART_MAIN);
    }

    band::build(scr);

    g_label = lv_label_create(scr);
    lv_obj_set_style_text_color(g_label, lv_color_hex(0x666666), LV_PART_MAIN);
    lv_obj_align(g_label, LV_ALIGN_TOP_MID, 0, 24);
    lv_label_set_text(g_label, "1.1 gate: eased saccades + sprung shape, independent layers");
}

void applyEmote(int idx);

void applyState(int idx)
{
    const St& s = kStates[idx];
    g_colR = (float)s.r;  g_colG = (float)s.g;  g_colB = (float)s.b;
    if (s.locked >= 0) applyEmote(s.locked);
}

void applyEmote(int idx)
{
    const Emote& e = kEmotes[idx];
    const float w = kBodyW * e.wMul, h = kBodyH * e.hMul;
    g_bodyW  = w;          // assignment animates -- spring by default
    g_bodyH  = h;
    g_radius = (w < h ? w : h) * e.radiusFrac;
    g_lidDrop = e.lid;
    g_browRaise = e.browRaise;
    g_browAngle = e.browAngle;
    char buf[128];
    snprintf(buf, sizeof(buf), "%s  -  %s  -  %s", kStates[g_state].label, e.name,
             band::name(band::g_activity));
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

    for (AnimateValue* v : {&g_gazeX, &g_gazeY, &g_bodyW, &g_bodyH, &g_radius, &g_lidDrop,
                            &g_asym, &g_browRaise, &g_browAngle, &g_colR, &g_colG, &g_colB})
        v->begin();
    for (AnimateValue* v : {&g_colR, &g_colG, &g_colB}) {
        v->animationType = animation_type::easing;
        v->easingOptions().duration = 0.45f;
    }
    g_gazeX.animationType = animation_type::easing;
    g_gazeY.animationType = animation_type::easing;
    applyState(g_state);
    applyEmote(0);
}

void AppFace::onRunning()
{
    const uint32_t now = GetHAL()->millis();

    // TELEMETRY IS OFF BY DEFAULT, and that is a correctness matter rather than tidiness.
    // The ESP-IDF console goes out over USB-serial-JTAG, and when no host is draining the port a
    // write can BLOCK THE CALLING TASK -- which here is the task driving the animation. So a
    // debug print in this loop is itself a plausible cause of the face freezing, and it cannot be
    // used to diagnose a frozen face without becoming a suspect. Build with
    // -DOPENDUBS_FACE_TELEMETRY to turn it on, and only while a reader is actually attached.
#ifdef OPENDUBS_FACE_TELEMETRY
    static uint32_t dbg = 0;
    if (now - dbg > 1000) {
        dbg = now;
        printf("[AppFace] hal_ms=%u ui_tick=%u gaze=%.3f,%.3f bodyW=%.1f lid=%.2f\n",
               (unsigned)now, (unsigned)smooth_ui_toolkit::ui_hal::get_tick(),
               (float)g_gazeX, (float)g_gazeY, (float)g_bodyW, (float)g_lidDrop);
        fflush(stdout);
    }
#endif

    if (now > g_nextSaccade) newSaccade(now);
    // No StamPLC link on the bench, so walk the states to see every colour. Phase 2 wires the
    // existing "STATE <NAME>" command to this.
    if (now > g_nextState) {
        g_nextState = now + 6000;
        g_state = (g_state + 1) % kNStates;
        GetHAL()->lvglLock();
        applyState(g_state);
        if (kStates[g_state].locked < 0) applyEmote(g_emote);   // refresh the label
        GetHAL()->lvglUnlock();
        g_nextEmote = now + 2400;
    }
    // Emotes cycle only where the face is free. In a reporting state the pinned emote stands,
    // while saccades and blinks carry on as normal.
    if (kStates[g_state].locked < 0 && now > g_nextEmote) {
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

    // FAULT is not a choice: the two reporting states that mean something is wrong own it, and a
    // theme can never recolour or re-time it into ambiguity.
    if (g_state == 4 || g_state == 5) {                    // DEGRADED, E_STOP
        band::g_activity = band::FAULT;
    } else {
        // No audio or Thor on the bench, so walk the vocabulary to see the motions.
        static uint32_t nextAct = 0;
        if (now > nextAct) {
            nextAct = now + 5000;
            const band::Activity cycle[] = {band::IDLE, band::LISTEN, band::THINK, band::SPEAK};
            static int k = 0;
            band::g_activity = cycle[k++ % 4];
        }
    }

    GetHAL()->lvglLock();
    const float gx = g_gazeX, gy = g_gazeY, asym = g_asym;
    band::update(now, ((uint32_t)(float)g_colR << 16) | ((uint32_t)(float)g_colG << 8)
                      | (uint32_t)(float)g_colB);
    for (int i = 0; i < 2; ++i) {
        Last& L = g_last[i];

        // Per-eye width: the asymmetry between the two eyes is the gaze cue in a pupil-less face,
        // and it costs nothing here because each eye is its own object.
        const int w = (int)((float)g_bodyW * (1.0f + (i == 0 ? -asym : asym)));
        const int h = (int)(float)g_bodyH;
        const int cx = (i == 0 ? kSlotL : kSlotR);
        if (w != L.w || h != L.h) {
            lv_obj_set_size(g_eye[i].body, w, h);
            lv_obj_set_pos(g_eye[i].body, cx - w / 2, kEyeY - h / 2);
            L.w = w; L.h = h;
        }
        const int radius = (int)(float)g_radius;
        if (radius != L.radius) {
            lv_obj_set_style_radius(g_eye[i].body, radius, LV_PART_MAIN);
            L.radius = radius;
        }
        // State colour: the eye's base, its gradient rim at 35% of the same hue so the sphere
        // falloff follows, and the brow.
        const uint32_t colour = ((uint32_t)(float)g_colR << 16)
                              | ((uint32_t)(float)g_colG << 8) | (uint32_t)(float)g_colB;
        if (colour != L.colour) {
            const uint32_t dim = (((colour >> 16 & 0xff) * 35 / 100) << 16)
                               | (((colour >> 8 & 0xff) * 35 / 100) << 8)
                               | ((colour & 0xff) * 35 / 100);
            lv_obj_set_style_bg_color(g_eye[i].body, lv_color_hex(colour), LV_PART_MAIN);
            lv_obj_set_style_bg_grad_color(g_eye[i].body, lv_color_hex(dim), LV_PART_MAIN);
            lv_obj_set_style_line_color(g_eye[i].brow, lv_color_hex(colour), LV_PART_MAIN);
            L.colour = colour;
        }

        // Pupil travel is (body - pupil)/2 - rim, which is what makes gaze legible at all.
        const int pw = (int)(w * kPupilFrac);
        const int travelX = (w - pw) / 2 - kRimPad, travelY = (h - pw) / 2 - kRimPad;
        const int px = (int)(gx * (travelX > 0 ? travelX : 0));
        const int py = (int)(gy * (travelY > 0 ? travelY : 0));
        if (pw != L.pupilW) { lv_obj_set_size(g_eye[i].pupil, pw, pw); L.pupilW = pw; }
        if (px != L.pupilX || py != L.pupilY) {
            lv_obj_align(g_eye[i].pupil, LV_ALIGN_CENTER, px, py);
            L.pupilX = px; L.pupilY = py;
        }

        // One highlight, inside the pupil. Upper-left is the conventional catchlight position and
        // it implies a fixed light source up and to the left of the robot.
        const int gw = pw / 3;
        const int gxo = -pw / 6, gyo = -pw / 6;
        if (gw != L.glintW) { lv_obj_set_size(g_eye[i].glint, gw, gw); L.glintW = gw; }
        if (gxo != L.glintX || gyo != L.glintY) {
            lv_obj_align(g_eye[i].glint, LV_ALIGN_CENTER, gxo, gyo);
            L.glintX = gxo; L.glintY = gyo;
        }

        // Brow as an angled line. The angle becomes a vertical offset between the two ends, so
        // there is no rotation and therefore no layer buffer. "Inner" is the end toward the face
        // centre, which is the right-hand end for the left eye and vice versa, so one signed
        // angle still describes a symmetric pair.
        const int bw = (int)(w * kBrowWFrac);
        const int bh0 = (int)(h * kBrowHFrac), bh = bh0 < 6 ? 6 : bh0;
        const float angRad = (float)g_browAngle * 3.14159265f / 180.0f;
        const int half = (int)(bw * tanf(angRad) * 0.5f);
        const int span = half < 0 ? -half : half;
        const int objH = bh + 2 * span;
        const int bx = cx - bw / 2;
        const int by = kEyeY - h / 2 - (int)(h * kBrowGap) - objH
                     - (int)((float)g_browRaise * h * kBrowRange);

        // LVGL keeps the pointer, so the points must outlive the call.
        static lv_point_precise_t pts[2][2];
        const int mid = objH / 2;
        const int inner = mid + (i == 0 ? half : half);
        const int outer = mid - (i == 0 ? half : half);
        pts[i][0].x = (i == 0) ? 0 : bw;        // outer end
        pts[i][0].y = outer;
        pts[i][1].x = (i == 0) ? bw : 0;        // inner end
        pts[i][1].y = inner;

        if (bw != L.browW || bh != L.browH || objH != L.browRot) {
            lv_obj_set_style_line_width(g_eye[i].brow, bh, LV_PART_MAIN);
            lv_obj_set_size(g_eye[i].brow, bw + bh, objH + bh);
            L.browW = bw; L.browH = bh; L.browRot = objH;
        }
        if (bx != L.browX || by != L.browY) {
            lv_obj_set_pos(g_eye[i].brow, bx, by);
            L.browX = bx; L.browY = by;
        }
        lv_line_set_points(g_eye[i].brow, pts[i], 2);

        const int lidH = (int)((float)g_lidDrop * h);
        const int lh = lidH < 0 ? 0 : lidH;
        if (w != L.lidW || lh != L.lidH) {
            lv_obj_set_size(g_eye[i].lid, w, lh);
            lv_obj_align(g_eye[i].lid, LV_ALIGN_TOP_MID, 0, 0);
            L.lidW = w; L.lidH = lh;
        }
    }
    GetHAL()->lvglUnlock();
}

void AppFace::onClose() { mclog::tagInfo(getAppInfo().name, "on close"); }
