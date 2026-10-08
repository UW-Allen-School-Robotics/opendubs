#include "app_face.h"
#include "cost_gate.h"
#include "cues.h"
#include "net.h"
#include "net_cmd.h"
#include "sim_inputs.h"
#include "frame_tap.h"
#include "cfg.h"
#include "walkin.h"
#include "ui_settings.h"
#include "ui_hmi.h"
#include "pantilt.h"
#include "percept/percept.h"
#include "percept/percept_config.h"

// Bench scaffolding: the HUD line, the status label, and the automatic state/emote cycling.
// Must come AFTER percept_config.h, which is where build.sh defines the flag -- putting the
// default first would shadow it and silently force every build to the bench behaviour.
#ifndef OPENDUBS_DBG_UI
#define OPENDUBS_DBG_UI 0
#endif
#include "shared/due.h"
#include <cstring>
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <smooth_ui_toolkit.h>
#include <lvgl.h>
#include <cmath>
#include <math.h>
#include <cstdio>
#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_system.h"
#ifdef ESP_PLATFORM
#include "esp_random.h"
#endif
#endif

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
    int browP0 = -9999, browP1 = -9999;
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
// Perception gets its OWN label. It first shared g_label with the state/heap HUD and lost: that
// HUD is rewritten on every state and emote change, so the status was clobbered within a frame --
// and the change-guard compared against WHAT THIS CODE LAST WROTE rather than what is on the
// label, so once clobbered it was never rewritten. Two writers, one widget, guaranteed to fail.
lv_obj_t* g_probeLabel = nullptr;
// And the touch UI gets its own, for the same reason the probe does. It carries the one fact
// nothing else can report on a head with no cable and no network yet: WHETHER AN LVGL INPUT
// DEVICE WAS FOUND AT ALL. Without it, "touch does nothing" has three indistinguishable causes
// — no indev, a wrong coordinate mapping, or a bug in the gesture — and this line eliminates
// the first in one glance. AUDIT.md's closing lesson, applied: "Put a falsifying measurement on
// the panel *first* — it costs one line and eliminates a whole category."
lv_obj_t* g_quote = nullptr;

// ---- the quotes ------------------------------------------------------------------------------
// Kept from the bespoke face, and kept in their PLACE. Two constraints decide where they live:
//
// Text is the LEAST ACCESSIBLE channel we have -- it needs literacy, proximity, attention and a
// shared language -- so it must never carry anything that matters. Jokes are the ideal payload for
// exactly that reason.
//
// And they are an IDLE-STATE artefact. They appear when the robot has nothing else to say, which
// is precisely the right scope, so they yield the moment there is real state to communicate. In
// the reporting states -- INIT, DEGRADED, E_STOP, CHARGING -- they are hidden outright, because a
// robot making jokes while degraded is the same lie as one looking cheerful while degraded.
//
// They also get their own strip, clear of the eyes and clear of the band, so three channels can be
// read at once without competing.
const char* const kQuotes[] = {
    "Idling at maximum efficiency.",
    "This IS the droid you're looking for.",
    "Standing by. Emphasis on standing.",
    "All systems nominal. Ambitions: also nominal.",
    "I could lift things. I'm choosing not to.",
    "Fully charged. Mildly bored.",
    "Running on 52 volts and good intentions.",
    "Waiting for humans. As usual.",
    "My other mode is autonomous.",
    "Powered on. Opinions loading...",
};
constexpr int kNQuotes = sizeof(kQuotes) / sizeof(kQuotes[0]);
int      g_quoteIdx  = 0;
uint32_t g_nextQuote = 0;
int      g_quoteShown = -1;

// ---- animated state --------------------------------------------------------------------------
// Gaze is EASED, not sprung: a saccade is ballistic and must not overshoot or wobble.
AnimateValue g_gazeX(0.0f), g_gazeY(0.0f);

// The reticle: a hollow box showing WHERE THE FIRMWARE THINKS THE TARGET IS. It exists because
// "the eyes follow me" is not actually falsifiable by looking at it -- the eyes are always moving,
// so a wrong mapping reads as ordinary wandering. The reticle makes the mapping visible: if it
// sits on you and moves with you, tracking is right; if it moves the wrong way, the sign is wrong
// and that is a one-line fix rather than an investigation.
//
// It is now also the only way to tell the two cues apart by eye: AMBER for motion, CYAN for a
// confirmed face. Without that, "the detector finally works" and "motion happened to be on your
// face" look identical, and this project has already drawn a confident conclusion from exactly
// that kind of ambiguity.
lv_obj_t* g_reticle = nullptr;
uint32_t g_lastGazeSeq = 0;
bool g_tracking = false;
uint8_t g_reticleKind = 0xff;   // last kind written, so the colour is change-guarded

// ---- attention -> gaze ----------------------------------------------------------------------
// HOW FAR THE EYES COMMIT IS THE ARBITER'S CONFIDENCE, scaled from this floor to full travel. A
// low-confidence target therefore gets a GLANCE in its direction rather than a fixation, and a
// confident one gets the full turn -- which is the behaviour "a weak hit is a brief low-amplitude
// glance; a strong, repeated hit earns a sustained look" asks for, with the dwell half of it
// handled by the target's own expiry (percept/attention.cpp).
//
// The floor is not zero: a target worth publishing is worth looking at a little, and an amplitude
// that collapses to nothing would make a weak hit indistinguishable from no hit at all.
constexpr float kGazeAmpFloor = 0.35f;

// The face's own vertical range is narrower than its horizontal one (newSaccade uses amp * 0.55),
// because eyes move less vertically than horizontally. A perception target has to be compressed
// the same way or a target near the top of the frame drives the pupil into the eyelid.
constexpr float kGazeVerticalScale = 0.55f;
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

// A uniform random in [0,n). esp_random() is in esp_random.h, not esp_system.h, and the desktop
// SDL build has neither -- so the choice is made once here rather than at each call site.
inline uint32_t rnd(uint32_t n)
{
    if (n == 0) return 0;
#ifdef ESP_PLATFORM
    return esp_random() % n;
#else
    return (uint32_t)rand() % n;
#endif
}

// The perimeter band. OFF by default -- see band::build() for why. Build with
// -DOPENDUBS_FACE_BAND=1 to get it back for an experiment.
#ifndef OPENDUBS_FACE_BAND
#define OPENDUBS_FACE_BAND 0
#endif

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
// `reserved` is the SAFETY-BEARING field: a theme may never recolour a reserved state, because
// that is how "something is wrong" stops being distinguishable from a decoration. It is a property
// OF THE STATE rather than an index comparison beside the table -- it used to be
// `state == 4 || state == 5`, which silently protects the wrong states the moment anyone reorders
// kStates, in a table that is about to become authorable data.
struct St {
    const char* name; const char* label;
    uint8_t r, g, b;
    int  locked;      // < 0 = the face may emote freely in this state
    bool reserved;    // colour is never themed, and the firmware enforces it
    // `fault` is deliberately NOT `reserved`, even though the same two states carry both today --
    // that coincidence is precisely the trap. `reserved` is a RENDERING rule: no theme may recolour
    // this. `fault` is a BEHAVIOURAL one: something is wrong, so the head must not move on its own
    // and the fault cue latches. Reusing one for the other couples a safety decision to a styling
    // decision, and the first state that needs one without the other would silently get both.
    bool fault;       // something is wrong: motion inhibited, fault cue latched, band flashes
    bool charging;    // the band shows pack SOC as a gauge
};
// Ported from firmware/tab5_face/src/hmi.cpp. The chip shows the state's NAME large and this
// underneath it: an operator who does not know the vocabulary still learns what the robot is
// doing, which is the whole point of putting a state on a face.
const char* stateDesc(const char* n)
{
    if (!strcmp(n, "SAFE_IDLE"))  return "Motion disabled";
    if (!strcmp(n, "MANUAL"))     return "RC teleop";
    if (!strcmp(n, "AUTONOMOUS")) return "Mission active";
    if (!strcmp(n, "CHARGING"))   return "On charger";
    if (!strcmp(n, "DEGRADED"))   return "Reduced function";
    if (!strcmp(n, "E_STOP"))     return "E-stop latched";
    return "Booting";
}

const St kStates[] = {
    //  name         label        r    g    b   locked emote  resvd  fault  charging
    {"INIT",       "INIT",       223, 231, 240, EM_NEUTRAL, false, false, false},
    {"SAFE_IDLE",  "SAFE IDLE",   46, 204,  88, -1,         false, false, false},
    {"MANUAL",     "MANUAL",     242, 246, 251, -1,         false, false, false},
    {"AUTONOMOUS", "AUTONOMOUS",   0, 176, 235, -1,         false, false, false},
    {"DEGRADED",   "DEGRADED",   243, 182,  74, EM_SQUINT,  true, true,  false},
    {"E_STOP",     "E-STOP",     255,  77,  77, EM_ALERT,   true, true,  false},
    // CHARGING no longer pins an emote. Charging is a CONDITION TO DISPLAY, not a mood to adopt:
    // the level goes on the band and the eyes stay free to be attending or idle as the situation
    // warrants -- which also settles the "charging is not necessarily idle" objection without
    // having to decide it, because the face no longer has to claim either.
    {"CHARGING",   "CHARGING",     0, 196, 168, -1,         false, false, true },
};
constexpr int kNStates = sizeof(kStates) / sizeof(kStates[0]);

// ASK THE STATE WHAT IT IS, never where it sits. Five checks compared g_state against 4, 5 and 6
// directly -- two of them deciding whether the head may move, two latching the fault cue -- so
// inserting or reordering a state silently retargeted the safety behaviour. The struct comment
// above already made this argument for `reserved`; these are the rest of that job.
//
// Both tolerate an out-of-range index, because g_linkState is -1 whenever the robot's state is
// unknown and "unknown" must not read as "faulted" or as "charging".
bool stateFault(int idx)    { return idx >= 0 && idx < kNStates && kStates[idx].fault; }
bool stateCharging(int idx) { return idx >= 0 && idx < kNStates && kStates[idx].charging; }
int g_state = 1;                       // SAFE_IDLE, as ui_shared.cpp defaults
uint32_t g_nextState = 0;

// ---- the StamPLC link ------------------------------------------------------------------------
// THE FACE MUST NOT CLAIM A STATE IT CANNOT CONFIRM.
//
// The Arduino firmware times out the link at 5 s and sets g_linkOnline=false -- and then face.cpp
// never reads it. So the face keeps showing whatever it was last told, indefinitely. That is the
// stale-display problem the dashboard note argues at length: a panel showing SAFE_IDLE because
// that is what it heard four minutes ago is indistinguishable from one showing it because it is
// true, and the second is what people will assume.
//
// So link loss is a VISIBLE CONDITION here, not a silent freeze. It deliberately is not an eighth
// entry in kStates, because it is not a state of the robot -- it is the absence of information
// about the robot, and giving it a row would let it be themed, emoted and cycled like a real one.
//
// It is also the NORMAL condition on a bench, which is the honest default: with no link the face
// should say it has no link, not pretend to be idle.
constexpr uint32_t kLinkStaleMs = 5000;   // matches the Arduino firmware
bool     g_linkUp      = false;
uint32_t g_nextLinkChk = 0;
int      g_linkState   = -1;              // index into kStates, or -1 when unknown

uint32_t g_nextSaccade = 0, g_nextEmote = 0, g_nextBlink = 0;
bool g_blinking = false;
uint32_t g_blinkEnd = 0;
// Wall-clock at onOpen, so "3 s after the face came up" is expressible without comparing the HAL's
// clock against anything else.
uint32_t g_openMs = 0;


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

// ---- themes ---------------------------------------------------------------------------------
// A theme is three things laid over an UNCHANGED state machine: a palette, an accessory layer, and
// (later) a band motion flavour. Nothing about a theme may touch the states themselves.
//
// ACCESSORIES ARE STATIC. They are created once when the theme activates and never animated,
// because a static LVGL object is never invalidated and therefore costs nothing per frame. That is
// the whole reason an accessory layer is affordable here, and it is also the right constraint: a
// decoration that animates is competing with the face for the frame budget.
//
// THE ONE RULE: a theme may restyle anything EXCEPT the state distinction. DEGRADED's amber and
// E_STOP's red are reserved and pass through untouched, so a theme can never make "something is
// wrong" ambiguous.
//
// That rule has teeth, and it cost this theme its most obvious colour. Halloween wants pumpkin
// orange -- which sits close enough to DEGRADED's amber to be confusable. So the palette gives
// orange up entirely and leans on purple and a toxic green, letting the ACCESSORIES carry the
// seasonal identity instead. A theme has to yield the hue the reserved states own.
namespace theme {

enum Id { NONE = 0, HALLOWEEN, COUNT };
Id g_active = NONE;

constexpr int kMaxAcc = 24;
lv_obj_t* g_acc[kMaxAcc];
int g_nAcc = 0;

// DERIVED FROM THE TABLE, not from indices. It used to be `state == 4 || state == 5`, which
// silently protects the wrong states the moment anyone reorders kStates -- in a table that is
// about to become authorable data, where reordering is exactly what an editor does.
//
// An out-of-range index is treated as RESERVED, so the failure mode of a bad index is "refuses to
// theme" rather than "themes a fault colour". Fail toward the safe answer.
bool reserved(int state)
{
    if (state < 0 || state >= kNStates) return true;
    return kStates[state].reserved;
}

// A theme's palette, KEYED BY STATE NAME.
//
// This was `switch (state) { case 0 ... case 6 }`, and that was a latent bug of exactly the kind
// reserved() had just been fixed to avoid: the guard addressed states BY NAME while the palette
// it guards addressed them BY ORDINAL. Insert a state anywhere above CHARGING and every themed
// colour is handed to its neighbour, while whichever state is pushed past the last case silently
// stops being themed at all. The reserved guard would still be correct throughout, so nothing
// would look broken -- the colours would just be wrong, which is the hardest kind to notice.
//
// DEGRADED and E_STOP are deliberately ABSENT rather than listed-and-rejected. Reserved expressed
// as absence is not a code path; a rejection is, and a code path can have a bug. reserved() still
// guards independently, so the two mechanisms have to fail together to leak a fault colour.
const char* name(Id id);   // defined below; validatePalettes() reports by theme name

struct Swatch { const char* state; uint32_t rgb; };

const Swatch kHalloween[] = {
    {"INIT",       0xe8e2d0},   // bone
    {"SAFE_IDLE",  0x8ac926},   // toxic green
    {"MANUAL",     0xd9c2e9},   // pale lavender
    {"AUTONOMOUS", 0x7b2d8e},   // purple
    {"CHARGING",   0x2f9e44},   // deep green
};

struct Palette { const Swatch* sw; int n; };

// Switching on the theme ID is not the same mistake: the enum IS the theme's identity, not its
// position in some other table. Adding a theme adds a case here and a table above.
Palette paletteFor(Id id)
{
    if (id == HALLOWEEN) return {kHalloween, (int)(sizeof(kHalloween) / sizeof(Swatch))};
    return {nullptr, 0};
}

uint32_t stateColour(int state, uint32_t base)
{
    if (g_active == NONE || reserved(state)) return base;
    if (state < 0 || state >= kNStates) return base;

    const char* n = kStates[state].name;

    // An authored palette answers by name, exactly as a compiled one does. Note what is NOT here:
    // no check for reserved states, because reserved() above already returned `base` for them and
    // cfg.cpp never reads their keys in the first place.
    if ((int)g_active >= (int)COUNT) {
        uint32_t rgb = 0;
        if (cfgThemeColour((int)g_active - (int)COUNT, n, &rgb)) return rgb;
        return base;
    }

    const Palette p = paletteFor(g_active);
    for (int i = 0; i < p.n; ++i)
        if (strcmp(p.sw[i].state, n) == 0) return p.sw[i].rgb;

    // A state the theme does not name keeps its own colour. Absence means "inherit", which is what
    // lets a theme describe only what it changes.
    return base;
}

// Lookup by name turns a typo into SILENCE -- the swatch simply never matches and the state keeps
// its base colour, which looks like a deliberate choice. So check the palettes once at startup and
// say so loudly. Also catches the authoring error that matters: a theme trying to recolour a
// reserved state. That is refused by reserved() at runtime regardless, but an author who wrote it
// should be told rather than quietly ignored.
void validatePalettes()
{
    for (int t = NONE + 1; t < COUNT; ++t) {
        const Palette p = paletteFor((Id)t);
        for (int i = 0; i < p.n; ++i) {
            bool found = false;
            for (int st = 0; st < kNStates; ++st) {
                if (strcmp(p.sw[i].state, kStates[st].name) != 0) continue;
                found = true;
                if (kStates[st].reserved)
                    mclog::tagError("theme", "palette '{}' names RESERVED state '{}' -- ignored",
                                    name((Id)t), p.sw[i].state);
                break;
            }
            if (!found)
                mclog::tagError("theme", "palette '{}' names unknown state '{}' -- typo?",
                                name((Id)t), p.sw[i].state);
        }
    }
}

// (A forward declaration of a resetPointPools() that was never defined used to sit here. The
// compiler had been saying so -- "declared 'static' but never defined" -- for as long as it has
// existed. Removed rather than left as noise next to the warnings that matter.)

// BUILT ONCE, THEN SHOWN OR HIDDEN. The first version created and deleted these objects on every
// theme change, which is allocation churn on a loop -- the same run-then-freeze signature as the
// gradient cache, and not "static" in any sense that matters. Nothing is allocated after startup.
void hideAll()
{
    for (int i = 0; i < g_nAcc; ++i) lv_obj_add_flag(g_acc[i], LV_OBJ_FLAG_HIDDEN);
}

// A cobweb in a corner: radial strands plus three polyline cross-threads, all lv_line.
//
// INSET CLEAR OF THE BAND. "A static object costs nothing" only holds if nothing redraws beneath
// it -- and the band strips redraw every frame along the whole perimeter, so webs overlapping them
// would be recomposited on every band write. Starting them inside the band's 16 px keeps the two
// from sharing any pixels.
//
// Mirrored by NEGATING POINT COORDINATES, not by a transform: a transform style is layer-inducing,
// which is what killed the rotated brow. Arithmetic on points is free.
void cobweb(lv_obj_t* scr, int cx, int cy, int sx, uint32_t colour)
{
    // HEADROOM, and the guards below fail silently when it runs out -- a third cobweb would
    // simply not draw, with no error anywhere. Sized for four webs rather than the two used.
    static lv_point_precise_t strand[16][2];
    static lv_point_precise_t cross[12][4];
    static int sN = 0, cN = 0;
    const int R[3] = {80, 145, 210};
    const float ang[4] = {0.0f, 0.42f, 0.90f, 1.5708f};

    for (int a = 0; a < 4 && g_nAcc < kMaxAcc && sN < 16; ++a) {
        lv_obj_t* l = lv_line_create(scr);
        lv_obj_remove_style_all(l);
        lv_obj_set_style_line_color(l, lv_color_hex(colour), LV_PART_MAIN);
        lv_obj_set_style_line_width(l, 2, LV_PART_MAIN);
        // POINTS MUST BE POSITIVE. lv_line coordinates are relative to the object's own box, so
        // the negative coordinates the mirror produced fell outside it and LVGL clipped them --
        // which is why the right-hand web was missing rather than mirrored.
        const float span = 225.0f;
        const float ox = (sx > 0) ? 0.0f : span;
        strand[sN][0].x = (lv_coord_t)ox; strand[sN][0].y = 0;
        strand[sN][1].x = (lv_coord_t)(ox + sx * cosf(ang[a]) * span);
        strand[sN][1].y = (lv_coord_t)(sinf(ang[a]) * span);
        lv_line_set_points(l, strand[sN], 2);
        lv_obj_set_size(l, 232, 232);
        lv_obj_set_pos(l, (sx > 0) ? cx : cx - 225, cy);
        lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
        g_acc[g_nAcc++] = l; ++sN;
    }
    for (int r = 0; r < 3 && g_nAcc < kMaxAcc && cN < 12; ++r) {
        lv_obj_t* l = lv_line_create(scr);
        lv_obj_remove_style_all(l);
        lv_obj_set_style_line_color(l, lv_color_hex(colour), LV_PART_MAIN);
        lv_obj_set_style_line_width(l, 2, LV_PART_MAIN);
        const float ox2 = (sx > 0) ? 0.0f : 225.0f;
        for (int a = 0; a < 4; ++a) {
            cross[cN][a].x = (lv_coord_t)(ox2 + sx * cosf(ang[a]) * R[r]);
            cross[cN][a].y = (lv_coord_t)(sinf(ang[a]) * R[r]);
        }
        lv_line_set_points(l, cross[cN], 4);
        lv_obj_set_size(l, 232, 232);
        lv_obj_set_pos(l, (sx > 0) ? cx : cx - 225, cy);
        lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
        g_acc[g_nAcc++] = l; ++cN;
    }
}

// Called once, from buildScene(). Every theme's accessories exist from startup.
void buildAll(lv_obj_t* scr)
{
    // Once, at startup: a mis-named swatch is otherwise silent.
    validatePalettes();

    const int inset = 22;                      // clear of the band's 16 px
    cobweb(scr, inset, inset, +1, 0x9a8fb0);
    cobweb(scr, kScreenW - inset, inset, -1, 0x9a8fb0);
}

void apply(lv_obj_t*, Id id)
{
    g_active = id;
    hideAll();
    if (id == HALLOWEEN)
        for (int i = 0; i < g_nAcc; ++i) lv_obj_clear_flag(g_acc[i], LV_OBJ_FLAG_HIDDEN);
}

// AUTHORED THEMES OCCUPY THE IDS ABOVE COUNT. The compiled ones keep their ordinals, so nothing
// that already works moves, and an authored set adds names rather than replacing a table.
const char* name(Id id)
{
    if ((int)id >= (int)COUNT) return cfgThemeName((int)id - (int)COUNT);
    return id == HALLOWEEN ? "halloween" : "plain";
}

} // namespace theme

}  // namespace -- the lookups below must have EXTERNAL linkage

// ---- name lookups for the shared command parser (net_cmd.h) ----
// The anonymous namespace is closed around these deliberately: everything else in this file is
// internal, and leaving them inside it made them invisible to net_cmd.cpp at link time. They can
// still see kEmotes and theme::name, which are anonymous-namespace members of this same
// translation unit.
// Here rather than in the parser because the TABLES are here. A second copy of these names in
// net_cmd.cpp would be one more thing to keep in step, and the twin exists to be in step.
// Case-insensitive, so `EMOTE alert` and `EMOTE ALERT` both work -- an operator typing a line by
// hand should not have to guess.
int faceEmoteByName(const char* n)
{
    for (int i = 0; i < kNEmotes; ++i)
        if (strcasecmp(kEmotes[i].name, n) == 0) return i;
    return -1;
}

int faceThemeByName(const char* n)
{
    for (int i = 0; i < theme::COUNT; ++i)
        if (strcasecmp(theme::name((theme::Id)i), n) == 0) return i;
    // Then the authored sets, so `THEME <authored name>` works the moment CFG installs one.
    for (int k = 0; k < cfgThemeCount(); ++k)
        if (strcasecmp(cfgThemeName(k), n) == 0) return (int)theme::COUNT + k;
    // `NONE` is the enum's name for what theme::name() calls "plain"; accept both so the command
    // matches the code as well as the panel.
    if (strcasecmp(n, "NONE") == 0) return theme::NONE;
    return -1;
}

int faceStateCount() { return kNStates; }

const char* faceStateName(int idx)
{
    return (idx >= 0 && idx < kNStates) ? kStates[idx].name : "?";
}

// TRUE OUT OF RANGE, deliberately. An unknown state must not be colourable by a file -- the safe
// direction for "I do not recognise this" is to treat it as reserved.
bool faceStateReserved(int idx)
{
    return (idx >= 0 && idx < kNStates) ? kStates[idx].reserved : true;
}

int faceCueByName(const char* n)
{
    // Ordered exactly as `enum class Cue` in cues.h. Kept as a table rather than a chain of ifs so
    // adding a cue there and forgetting here is a compile-time length mismatch, not silence.
    static const char* kNames[(int)Cue::Count] = {
        "heard", "attend", "refuse", "fault", "mode", "boot",
    };
    for (int i = 0; i < (int)Cue::Count; ++i)
        if (strcasecmp(kNames[i], n) == 0) return i;
    return -1;
}

namespace {   // reopened; the rest of this file is internal again

namespace band {

// FOUR STRIPS, NOT FORTY-EIGHT SEGMENTS.
//
// The segment version froze, and an observation at the robot located it precisely: it went on
// MANUAL / ALERT / idle, which is the peak-cost frame. ALERT is the largest body (1.06 x 1.08,
// so the biggest dirty area on every resize) and IDLE is the band's worst case, because breathing
// is UNIFORM -- all 48 segments change opacity every frame, where a sweep only touches the few the
// bump is passing over. Maximum eye area plus a guaranteed 48 writes per frame.
//
// So: one strip per edge, each carrying a FIXED LINEAR GRADIENT along its length, and the only
// per-frame write is each strip's opacity. That is:
//
//   * 4 objects instead of 48, and at most 4 writes per frame instead of 48
//   * smoothness from the RENDERER rather than from quantisation steps -- a linear gradient is a
//     per-scanline lookup, which is the cheap category, and it has no visible stepping
//   * travel from phase-offsetting the four edges, so the bright region still circulates
//
// It fixes both complaints at once: the freeze (fewer writes) and the pixelation (the gradient
// replaces 16 discrete opacity levels).
constexpr int kThick = 16;
constexpr int kEdges = 4;                 // top, right, bottom, left

lv_obj_t* g_edge[kEdges];
uint8_t   g_lastOpa[kEdges];
int       g_lastLen[kEdges];
uint32_t  g_lastColour = 0xffffffff;

enum Activity { IDLE = 0, LISTEN, THINK, SPEAK, FAULT, GAUGE };

// State of charge, 0..1. On the robot this comes from the pack's RS485 BMS, which already reports
// SOC to the StamPLC -- so nothing new has to be sensed for this.
float g_soc = 0.62f;

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
        case GAUGE:  return "charge";
        default:     return "idle";
    }
}

void build(lv_obj_t* scr)
{
    struct Spec { int x, y, w, h; lv_grad_dir_t dir; };
    const Spec spec[kEdges] = {
        {0,                0,                kScreenW, kThick,   LV_GRAD_DIR_HOR},   // top
        {kScreenW - kThick, 0,               kThick,   kScreenH, LV_GRAD_DIR_VER},   // right
        {0,                kScreenH - kThick, kScreenW, kThick,  LV_GRAD_DIR_HOR},   // bottom
        {0,                0,                kThick,   kScreenH, LV_GRAD_DIR_VER},   // left
    };
    for (int i = 0; i < kEdges; ++i) {
        lv_obj_t* o = lv_obj_create(scr);
        lv_obj_remove_style_all(o);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_pos(o, spec[i].x, spec[i].y);
        lv_obj_set_size(o, spec[i].w, spec[i].h);
        // A LINEAR gradient, which is a per-scanline lookup -- the cheap category -- and smooth
        // without any quantisation. Dim at one end, full at the other; the direction alternates so
        // the bright ends meet at the corners and the ring reads as continuous.
        lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, LV_PART_MAIN);
        lv_obj_set_style_bg_color(o, lv_color_hex(0x2ecc58), LV_PART_MAIN);
        lv_obj_set_style_bg_grad_color(o, lv_color_hex(0x2ecc58), LV_PART_MAIN);
        lv_obj_set_style_bg_grad_dir(o, spec[i].dir, LV_PART_MAIN);
        lv_obj_set_style_bg_main_opa(o, LV_OPA_20, LV_PART_MAIN);
        lv_obj_set_style_bg_grad_opa(o, LV_OPA_COVER, LV_PART_MAIN);
        g_edge[i] = o; g_lastOpa[i] = 0xff; g_lastLen[i] = -1;
    }
    // Sides sit between top and bottom so the corners overlap cleanly.
    lv_obj_move_background(g_edge[1]);
    lv_obj_move_background(g_edge[3]);
}

void update(uint32_t now, uint32_t colour)
{
    // dt IS CLAMPED. It feeds a phase accumulator, and one long frame -- a scheduling hiccup, a
    // slow redraw, a wrap -- would otherwise make the phase enormous. Nothing good comes of a
    // frame step longer than a tenth of a second anyway.
    float dt = (g_lastMs == 0) ? 0.0f : (now - g_lastMs) / 1000.0f;
    if (dt < 0.0f || dt > 0.1f) dt = 0.1f;
    g_lastMs = now;
    const float tsec = now / 1000.0f;

    float speed = 0.0f, base = 26.0f, peak = 170.0f, width = 0.30f;
    switch (g_activity) {
        case IDLE:   speed = 0.00f; base = 22.0f; peak =  60.0f; break;
        case LISTEN: speed = 0.30f; base = 24.0f; peak = 200.0f; width = 0.34f; break;
        case THINK:  speed = 0.85f; base = 18.0f; peak = 240.0f; width = 0.20f; break;
        case SPEAK:  speed = 0.00f; base = 60.0f; peak = 220.0f; break;
        case FAULT:  speed = 0.00f; base =  0.0f; peak = 255.0f; break;
        case GAUGE:  speed = 0.00f; base = 16.0f; peak = 235.0f; break;
    }
    g_phase += speed * dt;
    // fmodf, NOT a while loop. `while (g_phase > 1.0f) g_phase -= 1.0f;` iterates once per unit,
    // so a phase of 3.6e6 -- which one long frame could produce before dt was clamped -- means
    // millions of iterations inside the render path. That is a CPU hang with the heap untouched,
    // which is exactly the freeze signature we were seeing: 23 MB free and still stopped.
    g_phase = fmodf(g_phase, 1.0f);
    if (g_phase < 0.0f) g_phase += 1.0f;

    // THE GAUGE IS A LENGTH, not a phase, so it sizes the strips instead of fading them. SOC moves
    // over minutes, so these writes almost never happen -- which is why a level can afford to
    // resize where a sweep cannot.
    if (g_activity == GAUGE) {
        const float peri = 2.0f * (kScreenW + kScreenH);
        float left = g_soc * peri;
        const float lenOf[kEdges] = {(float)kScreenW, (float)kScreenH, (float)kScreenW, (float)kScreenH};
        for (int i = 0; i < kEdges; ++i) {
            const float take = left < lenOf[i] ? left : lenOf[i];
            left -= take;
            if (left < 0) left = 0;
            const int L = (int)take;
            if (L != g_lastLen[i]) {
                if (i == 0)      lv_obj_set_size(g_edge[i], L, kThick);
                else if (i == 2) { lv_obj_set_size(g_edge[i], L, kThick);
                                   lv_obj_set_pos(g_edge[i], kScreenW - L, kScreenH - kThick); }
                else             lv_obj_set_size(g_edge[i], kThick, L);
                g_lastLen[i] = L;
            }
            const float shimmer = 0.78f + 0.22f * sinf(tsec * 2.0f - i * 1.4f);
            const uint8_t opa = (uint8_t)(base + (peak - base) * (L > 0 ? shimmer : 0.0f));
            if (opa != g_lastOpa[i]) {
                lv_obj_set_style_bg_opa(g_edge[i], opa, LV_PART_MAIN);
                g_lastOpa[i] = opa;
            }
        }
    } else {
        // Restore full-length edges if we have just left the gauge.
        for (int i = 0; i < kEdges; ++i) {
            const int full = (i == 0 || i == 2) ? kScreenW : kScreenH;
            if (g_lastLen[i] != full) {
                if (i == 0)      lv_obj_set_size(g_edge[i], kScreenW, kThick);
                else if (i == 2) { lv_obj_set_size(g_edge[i], kScreenW, kThick);
                                   lv_obj_set_pos(g_edge[i], 0, kScreenH - kThick); }
                else             lv_obj_set_size(g_edge[i], kThick, kScreenH);
                g_lastLen[i] = full;
            }
        }
        for (int i = 0; i < kEdges; ++i) {
            float v;
            if (g_activity == FAULT)      v = (fmodf(tsec, 0.7f) < 0.35f) ? 1.0f : 0.06f;
            else if (g_activity == IDLE)  v = 0.5f + 0.5f * sinf(tsec * 1.1f);
            else if (g_activity == SPEAK) v = 0.5f + 0.5f * sinf(tsec * 7.0f);
            else {
                // Phase-offset per edge, so the bright region circulates the perimeter.
                const float at = (float)i / (float)kEdges;
                // Same hazard, same fix: wrap into -0.5..+0.5 arithmetically rather than by
                // repeated addition, so the cost cannot depend on the magnitude of the input.
                float d = fmodf(at - g_phase, 1.0f);
                if (d < 0.0f) d += 1.0f;
                if (d > 0.5f) d -= 1.0f;
                // GUARDED. width is only non-zero today because no case that reaches this branch
                // leaves it at its initialiser -- one added activity away from a divide by zero,
                // an inf, and a NaN propagating into a style value.
                const float wsq = (width > 0.01f) ? (width * width) : 0.0001f;
                v = expf(-(d * d) / (2.0f * wsq));
            }
            // Quantised to 32 levels. Each strip is 1280x16 or 720x16 of gradient, so a write
            // here is a big redraw -- and unlike the old 48-segment version the smoothness comes
            // from the gradient, not from the step size, so coarsening the step costs nothing
            // visually while halving how often four large areas are invalidated.
            const uint8_t opa = (uint8_t)(((int)(base + (peak - base) * v) >> 3) << 3);
            if (opa != g_lastOpa[i]) {
                lv_obj_set_style_bg_opa(g_edge[i], opa, LV_PART_MAIN);
                g_lastOpa[i] = opa;
            }
        }
    }

    if (colour != g_lastColour) {
        for (int i = 0; i < kEdges; ++i) {
            lv_obj_set_style_bg_color(g_edge[i], lv_color_hex(colour), LV_PART_MAIN);
            lv_obj_set_style_bg_grad_color(g_edge[i], lv_color_hex(colour), LV_PART_MAIN);
        }
        g_lastColour = colour;
    }
}

} // namespace band

// due() now lives in shared/due.h. It was defined identically in three files, and the third copy
// carried a note saying a fourth should become a header; the fourth was about to be written.

lv_obj_t* plainBox(lv_obj_t* parent, lv_color_t colour, int radius)
{
    lv_obj_t* o = lv_obj_create(parent);
    // THE FACE IS DECORATIVE AND MUST NOT CONSUME INPUT. lv_obj_create() sets LV_OBJ_FLAG_CLICKABLE
    // by DEFAULT in LVGL 9, so every eye body, pupil, glint, band strip and accessory was quietly
    // swallowing touch events before they could reach anything that wanted them. Nothing in this
    // file ever cleared it, which is why touch "did not work": it worked perfectly and was being
    // absorbed by a 480x480 eye.
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
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
        // VERTICAL, not radial. A radial gradient is cached per geometry and this body animates
        // its size, so every frame minted a new one until an allocation failed. A linear gradient
        // is a per-scanline lookup, costs almost nothing, and top-lit still reads as a sphere.
        lv_obj_set_style_bg_grad_dir(g_eye[i].body, LV_GRAD_DIR_VER, LV_PART_MAIN);
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
        // Solid, not a radial fade. The glint's size tracks the pupil, which tracks the body,
        // so it animates too -- same cache churn, smaller object. A hard-edged highlight is a
        // small aesthetic price for not minting a gradient per frame.
        lv_obj_set_style_bg_opa(g_eye[i].glint, 235, LV_PART_MAIN);
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

    // THE BAND IS SUPPRESSED. Decided 2026-10-06: it spun continuously and meant nothing, so it
    // carried no information and trained people to ignore the one channel that should carry
    // urgency. Not deleted -- F2 in the workplan still holds that motion could earn its place once
    // there is a real source for it (mic gate, inference running, audio playing). Until then it is
    // not built, which also removes 48 animated segments from every frame.
    //
    // band::update() tolerates never having been built: it writes only to g_edge[]/g_seg[], which
    // stay null, and every write is guarded. The FAULT path is the one loss worth naming -- a hard
    // flash on DEGRADED/E_STOP -- and the state colour plus the fault cue still carry that.
#if OPENDUBS_FACE_BAND
    band::build(scr);
#endif
    theme::buildAll(scr);

    // Own strip, below the eyes and above the bottom band. The eyes span y 120..600 and the band
    // occupies the last 16 px, so 648 sits clear of both.
    g_quote = lv_label_create(scr);
    lv_obj_set_style_text_color(g_quote, lv_color_hex(0x5a5a5a), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_quote, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_obj_set_width(g_quote, kScreenW - 160);
    lv_obj_set_style_text_align(g_quote, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_label_set_long_mode(g_quote, LV_LABEL_LONG_CLIP);
    lv_label_set_text(g_quote, "");
    lv_obj_align(g_quote, LV_ALIGN_TOP_MID, 0, 648);

    g_label = lv_label_create(scr);
    lv_obj_add_flag(g_label, LV_OBJ_FLAG_HIDDEN);   // off until SETTINGS asks for it
    lv_obj_set_style_text_color(g_label, lv_color_hex(0x666666), LV_PART_MAIN);
    lv_obj_align(g_label, LV_ALIGN_TOP_MID, 0, 24);
    lv_label_set_text(g_label, "1.1 gate: eased saccades + sprung shape, independent layers");

    g_probeLabel = lv_label_create(scr);
    lv_obj_set_style_text_color(g_probeLabel, lv_color_hex(0xffd24a), LV_PART_MAIN);
    lv_obj_align(g_probeLabel, LV_ALIGN_TOP_MID, 0, 56);
    lv_label_set_text_static(g_probeLabel, perceptStatus());
    // Hidden until the 4 Hz block below consults the telemetry switch, so a release does not show
    // a diagnostic line for the first quarter-second of every boot.
    lv_obj_add_flag(g_probeLabel, LV_OBJ_FLAG_HIDDEN);


    // Built once, hidden, never reallocated -- steady-state allocation is AUDIT.md invariant 6.
    g_reticle = lv_obj_create(scr);
    lv_obj_remove_style_all(g_reticle);
    lv_obj_remove_flag(g_reticle, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(g_reticle, 120, 120);
    lv_obj_set_style_radius(g_reticle, 8, 0);
    lv_obj_set_style_bg_opa(g_reticle, LV_OPA_TRANSP, 0);          // hollow: never hide the face
    lv_obj_set_style_border_width(g_reticle, 4, 0);
    lv_obj_set_style_border_color(g_reticle, lv_color_hex(0x00e5ff), 0);
    lv_obj_set_style_border_opa(g_reticle, LV_OPA_80, 0);
    lv_obj_add_flag(g_reticle, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(g_reticle);
}

// ---- camera bring-up, and why it lives in the FACE app --------------------------------------
//
// THE FACE APP OWNS EVERY LVGL CALL, INCLUDING THE ONES PERCEPTION WOULD OTHERWISE NEED. The
// vendor HAL hands over camera frames by pointing an lv_canvas at its PPA output, so acquiring a
// frame buffer means creating an LVGL object and reading from it -- which the perception layer is
// not allowed to do, and which is the structural reason percept/ is handed a pointer rather than
// fetching one (percept/percept.h).
//
// So this runs HERE, on the main task, in short non-blocking steps. The old arrangement did it
// inside the detector task: that task created a hidden canvas, a visible preview canvas and a
// reticle, took the LVGL lock to invalidate them every frame, and was a best-effort worker holding
// the one lock the renderer needs. Four unlocked LVGL calls in this file froze the face earlier
// today and a lock/unlock balance count reported it clean; the fix is not more careful locking, it
// is having nothing outside the UI that could take the lock at all.
//
// Nothing below blocks. A step that is not ready returns and is retried on the next tick, so a
// camera that never starts costs the animation nothing and says so on the panel.
constexpr int kCamW = 1280, kCamH = 720;     // read from hal_camera.cpp, not assumed
constexpr uint32_t kCamStartDelayMs = 3000;  // let the scene and the startup animation finish
constexpr uint32_t kCamPollMs = 250;
constexpr int kCamPollGiveUp = 120;          // 30 s before we call it dead

enum CamPhase { CAM_IDLE = 0, CAM_WAITING, CAM_UP, CAM_FAILED };
CamPhase g_camPhase = CAM_IDLE;
lv_obj_t* g_camCanvas = nullptr;
uint32_t g_camNext = 0;
int g_camPolls = 0;
char g_camStatus[48] = "camera: not started";

#if defined(OPENDUBS_DBG_PERCEPT)
// THE DEBUG PREVIEW, sized so it cannot starve the UI. A canvas pointed straight at percept's own
// 320x180 RGB565 buffer: no copy, no LVGL scaling, so no layer allocation (AUDIT.md invariant 2).
// It is invalidated from the main task at 10 Hz at most -- 57,600 px per invalidation, bounded
// regardless of camera frame rate. The 20 Hz full-rate version of this is what parked the main
// task in eBlocked.
lv_obj_t* g_preview = nullptr;
uint32_t g_nextPreview = 0;
uint32_t g_lastPreviewSeq = 0;
constexpr uint32_t kPreviewMs = 100;

void buildPerceptDebug()
{
    const uint16_t* buf = perceptPreview();
    if (!buf || g_preview) return;
    g_preview = lv_canvas_create(lv_screen_active());
    lv_canvas_set_buffer(g_preview, (void*)buf, perceptPreviewW(), perceptPreviewH(),
                         LV_COLOR_FORMAT_RGB565);
    lv_obj_align(g_preview, LV_ALIGN_BOTTOM_LEFT, 8, -8);
    lv_obj_move_foreground(g_preview);
}
#else
inline void buildPerceptDebug() {}
#endif

// Called from onRunning with NO lock held; takes it only around the LVGL calls.
void cameraStep(uint32_t now, uint32_t sinceOpen)
{
    if (g_camPhase == CAM_UP || g_camPhase == CAM_FAILED) return;

    if (g_camPhase == CAM_IDLE) {
        if (sinceOpen < kCamStartDelayMs) return;
        GetHAL()->lvglLock();
        // 1280x720 and HIDDEN: we want the HAL's buffer pointer, not its picture. Showing it would
        // cover the face, and LVGL never rasterises a hidden object, so it costs nothing per frame.
        g_camCanvas = lv_canvas_create(lv_screen_active());
        lv_obj_add_flag(g_camCanvas, LV_OBJ_FLAG_HIDDEN);
        GetHAL()->lvglUnlock();
        // Outside the lock: startCameraCapture only stores the canvas pointer and spawns the
        // vendor's own cam task (priority 5, pinned CPU1 -- read from hal_camera.cpp).
        GetHAL()->startCameraCapture(g_camCanvas);
        strncpy(g_camStatus, "camera: waiting for first frame", sizeof(g_camStatus) - 1);
        g_camPhase = CAM_WAITING;
        g_camNext = now + kCamPollMs;
        return;
    }

    if (!due(now, g_camNext)) return;
    g_camNext = now + kCamPollMs;

    // The HAL calls lv_canvas_set_buffer with the SAME pointer every frame, so one read is enough
    // and the perception loop then needs no lock at all to read pixels.
    GetHAL()->lvglLock();
    const uint16_t* src = (const uint16_t*)lv_canvas_get_buf(g_camCanvas);
    GetHAL()->lvglUnlock();

    if (!src) {
        if (++g_camPolls > kCamPollGiveUp) {
            // NOT "the camera is broken". This says what was observed: capture never produced a
            // buffer. A verdict the firmware cannot distinguish must not be stated as fact.
            strncpy(g_camStatus, "camera: NO BUFFER after 30s", sizeof(g_camStatus) - 1);
            g_camPhase = CAM_FAILED;
        }
        return;
    }

    if (perceptStart(src, kCamW, kCamH)) {
        GetHAL()->lvglLock();
        buildPerceptDebug();
        GetHAL()->lvglUnlock();
        g_camStatus[0] = 0;              // percept owns the status line from here
        g_camPhase = CAM_UP;
    } else {
        strncpy(g_camStatus, "percept: start failed", sizeof(g_camStatus) - 1);
        g_camPhase = CAM_FAILED;
    }
}

// Called with the LVGL lock HELD.
void reticleColourFor(uint8_t kind)
{
    if (kind == g_reticleKind) return;   // change-guarded: a style write invalidates
    g_reticleKind = kind;
    lv_obj_set_style_border_color(g_reticle,
                                  lv_color_hex(kind == GAZE_FACE ? 0x00e5ff : 0xffb020), 0);
}

void applyEmote(int idx);
void applyNoLink();

// EMOTE <name> pins an expression. -1 is AUTO: perception reactions and the random cycler resume.
//
// PRECEDENCE, agreed 2026-10-07: a state's `locked` emote > an EMOTE pin > the perception
// reaction > the random cycler. The reasoning for putting the pin above perception is that a pin is
// a SHOW CUE, and a show whose expression changes because someone walked past the robot is not a
// show. The cost is that a pinned face stops reacting to people until EMOTE AUTO, which is a real
// behaviour change and was accepted deliberately rather than fallen into.
int g_emotePin = -1;

void applyState(int idx)
{
    const St& s = kStates[idx];
    const uint32_t base = ((uint32_t)s.r << 16) | ((uint32_t)s.g << 8) | (uint32_t)s.b;
    const uint32_t c = theme::stateColour(idx, base);
    g_colR = (float)((c >> 16) & 0xff);  g_colG = (float)((c >> 8) & 0xff);  g_colB = (float)(c & 0xff);
    if (s.locked >= 0) applyEmote(s.locked);
}

// NO LINK is deliberately NOT rendered as FAULT. FAULT flashes hard and is owned by DEGRADED and
// E_STOP, so borrowing it would have the face announce a fault the robot has not reported -- the
// same class of lie as showing a stale state, just louder. "I cannot see the robot" is not "the
// robot is in trouble".
//
// Instead: a desaturated slate colour that matches no state in the table, on the IDLE breath. It
// reads as alive but uncommitted, which is exactly the claim being made. The eyes keep running --
// a dead face reads as a dead robot, a worse lie than admitting ignorance.
void applyNoLink()
{
    g_colR = 96.0f; g_colG = 108.0f; g_colB = 124.0f;   // slate; no state uses it
    band::g_activity = band::IDLE;
}

// THE STATUS LINE, COMPOSED ON ITS OWN so it can be refreshed without side effects.
//
// It used to live inside applyEmote(), which meant the only way to update the text was to re-run
// a function that ALSO retargets every animation -- so refreshing the label would have restarted
// the springs. That is why the line was a boot-time snapshot: applyEmote runs once in onOpen and
// then only on a state or emote change, and with no StamPLC link neither ever happens. The panel
// therefore sat on "wifi: idle" -- the initial value of net.cpp's status buffer, never
// overwritten -- while the radio was in a NO_AP_FOUND retry loop. The one place a person would
// look for the wifi state was the one place guaranteed not to have it.
//
// Caller holds the LVGL lock.
int g_shownEmote = 0;   // which emote the label is describing; see refreshLabel()

void refreshLabel()
{
    const Emote& e = kEmotes[g_shownEmote];
    char buf[160];
#ifdef ESP_PLATFORM
    snprintf(buf, sizeof(buf), "%s  -  %s  -  %s  -  %s   heap %uk / largest %uk",
             kStates[g_state].label, e.name, band::name(band::g_activity),
             theme::name(theme::g_active),
             // INTERNAL, not total. esp_get_free_heap_size() counts PSRAM, of which there are
             // 32 MB -- so it read ~23 MB and looked reassuring while the resource that actually
             // binds us, internal DRAM, sits at 250-310 KB. A readout that cannot show the
             // constraint is worse than none, because it gets believed.
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) / 1024));
    // Append the network state to the SAME line rather than adding a third label: the panel
    // already carries two, and an IP address is exactly the thing someone at the bench needs to
    // read off the glass before they can OTA to it.
    strncat(buf, "  ", sizeof(buf) - strlen(buf) - 1);
    strncat(buf, netStatus(), sizeof(buf) - strlen(buf) - 1);
#else
    snprintf(buf, sizeof(buf), "%s  -  %s  -  %s  -  %s", kStates[g_state].label, e.name,
             band::name(band::g_activity), theme::name(theme::g_active));
#endif
    lv_label_set_text(g_label, buf);
    // THE BENCH HUD IS NOT SHIP SURFACE. state/emote/band/theme plus internal heap plus the IP
    // address is exactly what someone at a desk wants and exactly what a robot in a room should
    // not be covered in. It goes behind the SETTINGS telemetry switch that already gates the
    // perception line -- the same control, persisted in NVS, so it comes back without a reflash.
    if (settingsTelemetryVisible()) lv_obj_remove_flag(g_label, LV_OBJ_FLAG_HIDDEN);
    else                            lv_obj_add_flag(g_label, LV_OBJ_FLAG_HIDDEN);
}

void applyEmote(int idx)
{
    const Emote& e = kEmotes[idx];
    g_shownEmote = idx;
    const float w = kBodyW * e.wMul, h = kBodyH * e.hMul;
    g_bodyW  = w;          // assignment animates -- spring by default
    g_bodyH  = h;
    g_radius = (w < h ? w : h) * e.radiusFrac;
    g_lidDrop = e.lid;
    g_browRaise = e.browRaise;
    g_browAngle = e.browAngle;
    // HEAP ON SCREEN, not over serial. A console write can block the render task when no host is
    // draining the port, so console telemetry is itself a suspect for a freeze and cannot be used
    // to diagnose one. Everything that has frozen so far has taken a while to do it, which points
    // at accumulation rather than an over-budget frame -- so what matters is whether free heap
    // TRENDS DOWN, and that is readable from the panel without any instrument.
    refreshLabel();
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


// ONE PASS OF THE FACE'S GEOMETRY. Extracted from onRunning so that it can also run ONCE in
// onOpen, under the lock, before anything is presented.
//
// WHY THIS EXISTS. plainBox() creates an object and removes all its styles but never sizes it, and
// buildScene() sizes only the eye BODY -- so until the first render pass ran, each eye's children
// sat at LVGL's default 130x130. Measured on the desktop build: body 480x480 (correct), pupil
// 130 (should be 163), GLINT 130 (should be 54, so a white blob filling the whole pupil), and LID
// 130x130 (should be 480x0, so a black square over the eye). That, plus the body still wearing
// buildScene's teal instead of the state colour, is the distorted face seen at boot.
//
// It was VISIBLE rather than a one-frame flicker because this pass sits at the END of onRunning,
// behind cameraStep() -- so on the device the first correct frame waited on camera and perception
// bring-up, seconds rather than milliseconds. Calling it from onOpen decouples the face entirely
// from perception, which never had anything to do with whether the face looks right.
//
// CALLER HOLDS THE LVGL LOCK. There is deliberately no lock call in here, so the one in onOpen can
// span build -> style -> this pass as a single hold. LVGL cannot render while that mutex is held,
// which means the barrier is structural: no half-built frame can reach the panel, rather than
// merely being unlikely to.
void renderFace(uint32_t now)
{
    const float gx = g_gazeX, gy = g_gazeY, asym = g_asym;
#if OPENDUBS_FACE_BAND
    band::update(now, ((uint32_t)(float)g_colR << 16) | ((uint32_t)(float)g_colG << 8)
                      | (uint32_t)(float)g_colB);
#endif
    for (int i = 0; i < 2; ++i) {
        Last& L = g_last[i];

        // Per-eye width: the asymmetry between the two eyes is the gaze cue in a pupil-less face,
        // and it costs nothing here because each eye is its own object.
        // QUANTISED TO 8 px. Resizing is the most expensive thing we do -- it dirties the old
        // rectangle and the new one, on the largest object on screen -- and during a sprung
        // transition the size changes every single frame. Snapping to 8 px makes the resize happen
        // roughly eight times less often and is invisible at these speeds, because a spring's
        // intermediate positions are not information.
        const int wRaw = (int)((float)g_bodyW * (1.0f + (i == 0 ? -asym : asym)));
        const int hRaw = (int)(float)g_bodyH;
        const int w = (wRaw + 4) & ~7;
        const int h = (hRaw + 4) & ~7;
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
        // GUARDED. This was the one write with no guard on it: lv_line_set_points invalidates the
        // brow every frame whether or not a point moved, which on SLEEPY -- where the brow is
        // static -- was pure waste at ~16 k px per eye per frame.
        if (pts[i][0].y != L.browP0 || pts[i][1].y != L.browP1) {
            lv_line_set_points(g_eye[i].brow, pts[i], 2);
            L.browP0 = (int)pts[i][0].y; L.browP1 = (int)pts[i][1].y;
        }

        const int lidH = (int)((float)g_lidDrop * h);
        const int lh = lidH < 0 ? 0 : lidH;
        if (w != L.lidW || lh != L.lidH) {
            lv_obj_set_size(g_eye[i].lid, w, lh);
            lv_obj_align(g_eye[i].lid, LV_ALIGN_TOP_MID, 0, 0);
            L.lidW = w; L.lidH = lh;
        }
    }
}

} // namespace

AppFace::AppFace() { setAppInfo().name = "AppFace"; }

void AppFace::onCreate()
{
    // KICKED HERE, NOT PERFORMED HERE. Mounting inline on the boot path cost three flashes: the
    // vendor's bsp_spiffs_mount() aborts the device on a mount failure (its error macro is
    // ESP_ERROR_CHECK), so the image rebooted, aborted, and was rolled back before anything could
    // say why. cfgStart() only spawns a task -- the mount, and the first-use format of a 5.5 MB
    // partition, happen off this path where they can take as long as they need.
    cfgStart();

    mclog::tagInfo(getAppInfo().name, "on create");
    open();
}

void AppFace::onOpen()
{
    mclog::tagInfo(getAppInfo().name, "on open");
    // Seeded, so saccade and blink timing are not identical on every unit and every boot. An
    // unseeded rand() made every robot's idle behaviour the same sequence.
    srand((unsigned)GetHAL()->millis());

    g_openMs = (uint32_t)GetHAL()->millis();

    GetHAL()->lvglLock();
#ifndef OPENDUBS_NO_SCENE
    buildScene();
#else
    // BISECT: face scene not built, so only the settings panel and its touch probe exist. If
    // touch works here and not with the scene, the scene is eating input -- which is a fact, not
    // a theory about z-order.
    mclog::tagInfo("bisect", "face scene SKIPPED");
#endif
    // The technician and fault screens, built here and hidden: "a second pre-built LVGL screen
    // of ~30 objects, built once at startup, hidden" (architecture note §1.7). They go up AFTER
    // buildScene so that lv_obj_move_foreground puts them above the face, the band and the
    // reticle, and so that nothing re-orders the z-stack per frame.
    costGateBegin();          // desktop only; measures what this would cost the device
    cuesBegin();              // render the earcon PCM once
    // NOTHING THE FACE DRAWS MAY ABSORB INPUT.
    //
    // lv_obj_create() sets LV_OBJ_FLAG_CLICKABLE by default in LVGL 9. Clearing it inside
    // plainBox() covered the eyes but NOT the band strips, which are created directly and cover
    // all four screen edges -- so any press landing near an edge was swallowed by a strip with no
    // handler, propagated nowhere, and never reached the screen. Verified: the GT911 initialises
    // and answers (TouchPad_ID 0x39,0x31,0x31), so presses were being read and then eaten.
    //
    // Walking the children is deliberate rather than fixing each creation site: the face gains
    // objects over time (accessories, themes, the preview canvas), and a per-site fix is a list
    // that goes stale -- which is how the band strips were missed in the first place.
    {
        lv_obj_t* scr = lv_screen_active();
        const uint32_t n = lv_obj_get_child_count(scr);
        for (uint32_t i = 0; i < n; ++i)
            lv_obj_remove_flag(lv_obj_get_child(scr, i), LV_OBJ_FLAG_CLICKABLE);
        mclog::tagInfo("touch", "cleared CLICKABLE on {} scene objects", (int)n);
    }

    netStart();               // Wi-Fi station + UDP command port + OTA. Independent of perception.
    settingsBuild();          // audio + telemetry, persisted in NVS
    hmiBuild();               // the lift-controller HMI + touch-anywhere reveal
    pantiltBegin();           // 8Servo head. Self-disables when absent (bench).
    walkinBuild();            // no-op unless OPENDUBS_WALKIN=1
    // THE LOCK IS NOT RELEASED HERE, and that single fact is the fix. LVGL cannot render while
    // this mutex is held, so holding it from buildScene() all the way through the first geometry
    // pass below makes the barrier STRUCTURAL: there is no window in which a half-built frame
    // could reach the panel, rather than merely a narrow one. Releasing here is what made the
    // as-built state -- teal bodies, 130x130 glint and lid -- visible at boot.
    //
    // What sits between here and the geometry pass is the AnimateValue setup, which touches no
    // LVGL object at all, so extending the hold across it costs microseconds.
    // Perception is NOT started here. It needs a camera frame buffer, which does not exist until
    // the vendor cam task has produced one, so cameraStep() brings it up from onRunning in
    // non-blocking steps. Nothing about the face waits for it.

    for (AnimateValue* v : {&g_gazeX, &g_gazeY, &g_bodyW, &g_bodyH, &g_radius, &g_lidDrop,
                            &g_asym, &g_browRaise, &g_browAngle, &g_colR, &g_colG, &g_colB})
        v->begin();
    for (AnimateValue* v : {&g_colR, &g_colG, &g_colB}) {
        v->animationType = animation_type::easing;
        v->easingOptions().duration = 0.45f;
    }
    g_gazeX.animationType = animation_type::easing;
    g_gazeY.animationType = animation_type::easing;
    // STILL INSIDE THE LOCK TAKEN ABOVE, and that is a fix rather than tidying: applyEmote()
    // calls lv_label_set_text(), so when the lock was released earlier this was an
    // unsynchronised LVGL write during startup -- AUDIT.md invariant 1, the class of bug that
    // froze this face before.
    // NO LINK, NOT SAFE_IDLE. g_state initialises to 1 (SAFE_IDLE) as a seed value, so calling
    // applyState() here painted the eyes GREEN -- the face asserting "motion disabled, all well"
    // before it had heard a single word from the robot. applyNoLink()'s own comment calls that out
    // as the lie it exists to prevent. Not a desktop-only problem, which is how it was first
    // reported: on the robot the same green frame covers Wi-Fi association and the first telemetry.
    applyNoLink();
    applyEmote(0);

    // The twin's simulated inputs and its SIMULATED badge. Inside the lock that onOpen already
    // holds, because this creates LVGL objects. A no-op on the device.
    simBegin(lv_screen_active());
    // The twin's finished frames, shared with the MuJoCo body twin (choreography 2.5). Off unless
    // OPENDUBS_FACE_FRAMES is set; a no-op on the device.
    frameTapBegin();

    // THE EYES START SHUT, and the boot reads as the robot waking up instead of a face appearing.
    // teleport() rather than assignment: this must be instant, not animated, because it has to be
    // true before the single geometry pass below. onRunning opens them once, on its first frame.
    g_lidDrop.teleport(1.0f);

    // ONE GEOMETRY PASS BEFORE ANYTHING IS SHOWN. Still holding the lock taken above, so the whole
    // sequence -- build, style, lids shut, geometry -- is atomic with respect to the LVGL task and
    // the first frame the panel ever receives is a correct, closed-eyed face.
    renderFace(0);
    GetHAL()->lvglUnlock();
}

void AppFace::onRunning()
{
    const uint32_t now = GetHAL()->millis();

#ifdef OPENDUBS_NO_SCENE
    // BISECT: with the scene unbuilt, every object below is nullptr and the render loop faults --
    // which is what the first attempt at this did. Skip the whole body; the settings panel and its
    // touch probe are built independently and keep working.
    (void)now;
    return;
#endif


    // TELEMETRY IS OFF BY DEFAULT, and that is a correctness matter rather than tidiness.
    // The ESP-IDF console goes out over USB-serial-JTAG, and when no host is draining the port a
    // write can BLOCK THE CALLING TASK -- which here is the task driving the animation. So a
    // debug print in this loop is itself a plausible cause of the face freezing, and it cannot be
    // used to diagnose a frozen face without becoming a suspect. Build with
    // -DOPENDUBS_FACE_TELEMETRY to turn it on, and only while a reader is actually attached.
#ifdef OPENDUBS_FACE_TELEMETRY
    static uint32_t dbg = 0;
    if (due(now, dbg + 1000)) {
        dbg = now;
        printf("[AppFace] hal_ms=%u ui_tick=%u gaze=%.3f,%.3f bodyW=%.1f lid=%.2f\n",
               (unsigned)now, (unsigned)smooth_ui_toolkit::ui_hal::get_tick(),
               (float)g_gazeX, (float)g_gazeY, (float)g_bodyW, (float)g_lidDrop);
        fflush(stdout);
    }
#endif

    // TOUCH, FIRST AND UNCONDITIONALLY. It arbitrates which screen owns the panel, drives the
    // entry gesture and runs the grade machinery -- and the grade machinery must run even when
    // the face is up, because a jog that was held when the screen changed still has to reach its
    // stop. LOCKED: onRunning does NOT hold the LVGL lock throughout, it takes it in short
    // bursts per block, and four calls that forgot that froze the face (AUDIT.md invariant 1).
    GetHAL()->lvglLock();
    GetHAL()->lvglUnlock();

    // THE FACE YIELDS THE GLASS COMPLETELY. Architecture note §1.7: "While the controls screen
    // is shown the face timers are paused, so the two never compete." Everything below this
    // point writes LVGL; none of it would be visible under an opaque full-screen layer, and all
    // of it would still cost its dirty area. What survives the early return is the audio, which
    // is not on the glass and whose fault latch must not be left stale -- a repeating fault
    // announcement that stops because someone opened a readout screen is a lie by omission.
    // THE HMI, which is the screen a touch on the face reveals. Fed rather than reaching into
    // this file's state table: the face owns the state, ui_hmi.cpp owns the pixels.
    {
        const bool linked = g_linkUp && g_linkState >= 0;
        const St&  st     = kStates[linked ? g_linkState : 0];
        HmiFeed    f{};
        f.stateLabel  = linked ? st.label : "NO LINK";
        f.stateDesc   = linked ? stateDesc(st.name) : "no controller";
        f.stateColour = ((uint32_t)st.r << 16) | ((uint32_t)st.g << 8) | (uint32_t)st.b;
        f.stateLinked = linked;
        f.net         = netStatus();
        // ONLY WHEN IT IS OPEN, and that is the whole point rather than an optimisation.
        // hmiTick() early-returns when the HMI is closed, so taking the LVGL lock to call it was
        // pure churn -- and an extra per-frame lvglLock/lvglUnlock pair in this loop is the one
        // variable that tracks every touch failure today: the probe build that worked for minutes
        // and then stopped, and all three HMI builds. The touchscreen is POLLED by a 16 ms LVGL
        // timer that runs inside lv_timer_handler() and needs this same recursive mutex, and a
        // starved poll does not see a short press at all. Closed, this now costs nothing.
        if (hmiOwnsPanel()) {
            GetHAL()->lvglLock();
            hmiTick(now, f);
            GetHAL()->lvglUnlock();
        }
    }

    // HEAD MOTION, HOISTED ABOVE EVERY EARLY RETURN -- and that placement is the whole fix.
    //
    // This used to sit after the `settingsOwnsPanel() || hmiOwnsPanel()` return, so pan/tilt
    // stopped being ticked exactly while the controller panel was open: the d-pad set a rate that
    // nothing ever integrated, and the servos were not written either. The arrows looked dead
    // while the e-stop beside them worked, because the e-stop's effect leaves over the network and
    // does not need this loop.
    //
    // While a panel owns the screen there is no gaze to follow -- the face is not even drawn -- so
    // autonomous tracking is passed haveTarget=false and only the touch lease steers. An OTA is
    // the one case that still skips it: nothing should be commanding servos mid-update.
    // The gaze inputs are computed further down, after this returns, so this call covers ONLY the
    // panel case and passes no target: with a panel over the face there is nothing to look at and
    // only the touch lease should steer. The normal path still ticks at its original place below,
    // so the head is ticked exactly once per frame either way.
    if ((settingsOwnsPanel() || hmiOwnsPanel()) && !netOtaActive()) {
        const bool known   = g_linkUp && g_linkState >= 0;
        const bool stopped = stateFault(g_state);
        pantiltTick(now, known && !stopped, false, 0.0f, 0.0f, 0.0f);
    }

    simTick(now);

    // ---- apply whatever the command parser recorded ----
    // BEFORE the early returns below, so EMOTE/THEME/CUE take effect even while a panel owns the
    // screen. The parser only ever records an intent (net_cmd.h): the network task must not call
    // LVGL, which is AUDIT invariant 1 and the class of bug that froze this face before.
    {
        NetUiReq req;
        if (netTakeUiReq(&req)) {
            if (req.emote != -2 || req.theme >= 0) {
                GetHAL()->lvglLock();
                if (req.emote != -2) {
                    g_emotePin = req.emote;                   // -1 == AUTO
                    if (g_emotePin >= 0) {
                        // A locked state outranks a pin, so do not fight it -- the pin is still
                        // recorded and asserts itself once the state frees the face again.
                        if (kStates[g_state].locked < 0) applyEmote(g_emotePin);
                    } else {
                        g_nextEmote = now;                    // AUTO: cycle again at once
                    }
                }
                if (req.theme >= 0) {
                    theme::apply(lv_screen_active(), (theme::Id)req.theme);
                    applyState(g_state);
                }
                GetHAL()->lvglUnlock();
            }
            // Audio, not LVGL -- deliberately outside the lock.
            if (req.cue >= 0) cuePlay((Cue)req.cue);
        }
    }

    // KEEP A PIN ASSERTED. Re-applied only when what is shown differs from what is pinned, never
    // every frame: applyEmote() retargets the springs, so calling it repeatedly would stop them
    // ever settling. This is what restores the pin after a locked state (DEGRADED, E_STOP) has had
    // its turn and the robot recovers.
    if (g_emotePin >= 0 && kStates[g_state].locked < 0 && g_shownEmote != g_emotePin) {
        GetHAL()->lvglLock();
        applyEmote(g_emotePin);
        GetHAL()->lvglUnlock();
    }

    // QUIESCE WHILE AN OTA IS DOWNLOADING.
    //
    // The OTA runs on the network task at priority 2. The LVGL task is priority 4 and this face is
    // measured at 107-112% of its frame budget, so rendering preempts the download -- and
    // pantiltTick adds 50 Hz of servo I2C on top. The OTA then competes for CPU it cannot win, and
    // its own 10 s stall deadline is what would eventually abort it. An OTA lasts 15-40 s and the
    // face has nothing to say during one; the panel shows the percentage, which is more use than
    // moving eyes. Audio still ticks, because a fault announcement must not be silenced by a
    // firmware update.
    if (netOtaActive()) {
        cueFaultActive(stateFault(g_state));
        cueTick(now);
        return;
    }

    if (settingsOwnsPanel() || hmiOwnsPanel()) {
        cueFaultActive(stateFault(g_state));
        cueTick(now);
        return;
    }

    // Periodic cost report, and a final verdict once the bench has walked the whole matrix of
    // states, emotes, activities and themes -- which is when the PEAK frame has been seen.
    static uint32_t nextCost = 10000;
    if (due(now, nextCost)) {
        nextCost = now + 10000;
        costGateReport(now > 80000);
    }

    // Quotes: only where the face is free to be idle, and changed slowly enough to be read. The
    // label text is the only steady-state allocation in the app, so a slow rotation matters.
    {
        const bool want = kStates[g_state].locked < 0;
        if (!want) {
            if (g_quoteShown != -1) {
                GetHAL()->lvglLock();
                lv_label_set_text(g_quote, "");
                GetHAL()->lvglUnlock();
                g_quoteShown = -1;
            }
        } else if (due(now, g_nextQuote) || g_quoteShown == -1) {
            g_nextQuote = now + 9000;
            g_quoteIdx = (g_quoteIdx + 1) % kNQuotes;
            GetHAL()->lvglLock();
            lv_label_set_text(g_quote, kQuotes[g_quoteIdx]);
            GetHAL()->lvglUnlock();
            g_quoteShown = g_quoteIdx;
        }
    }

    cueTick(now);

    // Camera and perception bring-up, in non-blocking steps on this task. See cameraStep().
    // THE STATE LINK. Checked at 4 Hz: a state change is a human-timescale event and there is no
    // reason to spend a frame on it.
    if (due(now, g_nextLinkChk)) {
        g_nextLinkChk = now + 250;
        const char* name = nullptr;
        unsigned    age  = 0;
        const bool  had  = linkState(&name, &age);
        const bool  up   = had && age < kLinkStaleMs;

        int idx = -1;
        if (up) {
            for (int i = 0; i < kNStates; ++i)
                if (strcmp(kStates[i].name, name) == 0) { idx = i; break; }
            // An unknown name is NOT silently ignored and NOT mapped to state 0: being told
            // something we cannot interpret is closer to having no link than to being idle.
            if (idx < 0) mclog::tagError("link", "unknown STATE '{}' -- treating as no link", name);
        }

        // THE FIRST OBSERVATION ALWAYS APPLIES. This was change-detection against the initial
        // values -- and the first poll's answer when nothing has been heard (up=false, idx=-1) is
        // EXACTLY those values, so the no-link presentation was never applied at all. A state is
        // only "unchanged" once something has actually been observed.
        static bool applied = false;
        if (!applied || up != g_linkUp || idx != g_linkState) {
            applied     = true;
            g_linkUp    = up;
            g_linkState = idx;
            GetHAL()->lvglLock();
            if (idx >= 0) {
                g_state = idx;
                applyState(g_state);
                band::g_activity = band::IDLE;
            } else {
                applyNoLink();
            }
            GetHAL()->lvglUnlock();
            mclog::tagInfo("link", "{}", idx >= 0 ? kStates[idx].name : "NO LINK");
        }
    }

    cameraStep(now, now - g_openMs);

    // WALK-IN TEST MODE takes the rest of the frame. It must come AFTER cameraStep(), not before:
    // perception is brought up FROM THIS LOOP in non-blocking steps, so returning early above it
    // means the camera never starts, perceptGaze() never has anything to report, and the panel
    // sits on NOTHING forever -- with the face hidden, which is a screen that cannot change by
    // construction rather than a crash. That is exactly what the first flash of this did.
    {
        GetHAL()->lvglLock();
        const bool took = walkinTick(now);
        GetHAL()->lvglUnlock();
        if (took) return;
    }

    // PERCEPTION STATUS, on its own label. Repainted on a TIMER rather than on change: the
    // change-guard version sat on one stale string while the probe was demonstrably running, and
    // rather than theorise about why, this removes the guard from the critical path entirely. 4 Hz
    // costs one label's area a quarter of a second -- cheap, and it cannot get stuck.
    {
        static uint32_t nextStatus = 0;
        if (due(now, nextStatus)) {
            nextStatus = now + 250;
            // LOCKED. onRunning does NOT hold the LVGL lock throughout -- it takes it in short
            // bursts around each block that touches LVGL, and this sits between two of them. I
            // wrote a comment below asserting the opposite and it was simply false.
            GetHAL()->lvglLock();
            // _static: no copy, so no malloc. CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 means
            // every allocation under 16 KB lands in INTERNAL DRAM, so a 4 Hz lv_label_set_text
            // was churning the scarcest resource on the chip for a debug string. The source is a
            // static buffer that outlives the label, which is exactly what _static requires.
            // percept double-buffers its status and swaps an atomic pointer, so the string a
            // reader holds is never the one being rewritten.
            // THE TELEMETRY SWITCH GOVERNS THIS LABEL. It is the on-screen readout the operator
            // can turn on in the field -- the perception/camera status line. Until now the switch
            // saved a flag that nothing read, so flipping it changed no pixel: a dead control,
            // which is worse than no control because it teaches the operator the panel lies.
            // Default off, so a face shows a face.
            // Keep the status line LIVE. Cheap guard: only recompose when the network string
            // actually changed, so a healthy head costs one strcmp per 250 ms rather than an
            // snprintf and a label invalidation.
            {
                static char lastNet[64] = {0};
                if (strncmp(lastNet, netStatus(), sizeof(lastNet) - 1) != 0) {
                    strncpy(lastNet, netStatus(), sizeof(lastNet) - 1);
                    refreshLabel();
                }
            }

            if (settingsTelemetryVisible()) lv_obj_remove_flag(g_label, LV_OBJ_FLAG_HIDDEN);
            else                            lv_obj_add_flag(g_label, LV_OBJ_FLAG_HIDDEN);

            if (settingsTelemetryVisible()) {
                lv_label_set_text_static(g_probeLabel,
                                         g_camStatus[0] ? g_camStatus : perceptStatus());
                lv_obj_remove_flag(g_probeLabel, LV_OBJ_FLAG_HIDDEN);
            } else {
                lv_obj_add_flag(g_probeLabel, LV_OBJ_FLAG_HIDDEN);
            }
            GetHAL()->lvglUnlock();
        }
    }

#if defined(OPENDUBS_DBG_PERCEPT)
    // Preview repaint, rate-limited HERE rather than driven by the camera: 10 Hz of a 320x180
    // canvas is 0.58 Mpx/s and bounded whatever the camera does. Only repaint when percept has
    // actually produced a new frame, so a stalled pipeline costs nothing.
    if (g_preview && due(now, g_nextPreview)) {
        g_nextPreview = now + kPreviewMs;
        const uint32_t seq = perceptFrameSeq();
        if (seq != g_lastPreviewSeq) {
            g_lastPreviewSeq = seq;
            GetHAL()->lvglLock();
            lv_obj_invalidate(g_preview);
            GetHAL()->lvglUnlock();
        }
    }
#endif

    // GAZE: face (close, confident) > motion (any range) > autonomous saccades.
    //
    // The precedence and the grading are decided in percept/attention.cpp; what is decided HERE is
    // the only thing the face owns -- how much of its gaze range a target of a given confidence
    // earns. Everything below is one read of a seqlock and some arithmetic: no blocking, no lock
    // the perception task could be holding, and nothing that waits on an inference.
    //
    // EVERY LVGL CALL BELOW IS EXPLICITLY LOCKED. An earlier version of this comment claimed
    // onRunning "already holds the LVGL lock" and so these did not need one. That was false --
    // onRunning acquires it in short bursts per block -- and the result was four unlocked LVGL
    // calls, which froze the face.
    GazeIntent gi = {};
    // The twin's fake person first, then real perception. simGaze() is an inline `return false` on
    // the device, so this reads as one path and compiles as one.
    const bool haveGaze = simGaze(&gi) || perceptGaze(&gi);
    if (haveGaze) {
        if (gi.seq != g_lastGazeSeq) {
            g_lastGazeSeq = gi.seq;

            // AMPLITUDE SCALES WITH CONFIDENCE. A confident face gets the full turn; a weak hit
            // gets a glance in its direction. The target's own expiry handles the other half --
            // how long the eyes stay there -- so one graded number produces both behaviours.
            const float amp = kGazeAmpFloor + (1.0f - kGazeAmpFloor) * gi.confidence;

            // THE EYES GIVE BACK WHAT THE HEAD HAS COVERED -- the VOR term from
            // head-turn-gaze.md. Once the head turns, the target is no longer where it was
            // relative to the face, so aiming the eyes at the raw offset would have both aim at it
            // and overshoot together. The residual is the same number used twice, which is why
            // this is not a separate feature.
            //
            // AND IT DEGRADES BY CONSTRUCTION: with no 8Servo the head offsets are exactly 0, so
            // these two lines reduce to what they were before and the face is eyes-only with no
            // branch to get wrong.
            const float residX = gi.nx - pantiltHeadOffsetX();
            const float residY = gi.ny - pantiltHeadOffsetY();
            const float tx = residX * amp;
            const float ty = residY * amp * kGazeVerticalScale;

            // Same main-sequence timing as a spontaneous saccade, so an attentive glance does not
            // move in a visibly different way from a natural one.
            const float dur = saccadeSeconds(tx - (float)g_gazeX, ty - (float)g_gazeY);
            g_gazeX.easingOptions().duration = dur;
            g_gazeY.easingOptions().duration = dur;
            g_gazeX = tx;
            g_gazeY = ty;
            g_asym = -tx * 0.13f;

            // Hold off the random saccade while a target is live. Without this the two fight and
            // the result looks like a malfunction rather than attention.
            g_nextSaccade = now + 1200;

            // Reticle in SCREEN space: the straight inverse of percept's normalisation across the
            // whole panel. It shows the RAW target, not the amplitude-scaled gaze, because its job
            // is to make the mapping falsifiable -- scaling it would hide a sign error behind the
            // same compression that makes a weak glance look like wandering.
            const int rx = (int)((gi.nx * 0.5f + 0.5f) * (float)kScreenW);
            const int ry = (int)((gi.ny * 0.5f + 0.5f) * (float)kScreenH);
            GetHAL()->lvglLock();
            lv_obj_set_pos(g_reticle, rx - 60, ry - 60);
            reticleColourFor(gi.kind);
            GetHAL()->lvglUnlock();
        }
        if (!g_tracking) {
            g_tracking = true;
            // A PERSON ARRIVED. React once, then let the idle behaviour resume -- an emote is a
            // momentary reaction to an event, NOT a mood that persists. That boundary is what keeps
            // this from turning into an emotional model, and it is the same shape as the earcons:
            // `Attend` has existed unwired since 1.3 waiting for exactly this trigger.
            //
            // Only where the face is free. DEGRADED and E_STOP pin an emote because they are
            // communicating something, and noticing a person must not talk over that.
            // A PIN OUTRANKS THIS. An EMOTE pin is a show cue and must not be stomped by someone
            // walking past; see g_emotePin for the full precedence and what it costs.
            if (g_emotePin < 0 && kStates[g_state].locked < 0) {
                GetHAL()->lvglLock();
                applyEmote(EM_SOFT);
                GetHAL()->lvglUnlock();
                // Hold the reaction briefly against the random cycler, which would otherwise
                // overwrite it within a second or two and make the response look like noise.
                g_nextEmote = now + 6000;
            }
            cuePlay(Cue::Attend);
            // ONLY IF ASKED FOR. The reticle was created hidden and then unhidden by this line
            // whenever perception tracked, with nothing gating it -- so on a robot it was a box
            // chasing people across the face. Now behind SETTINGS -> Tracking reticle, off by
            // default. g_tracking still flips, so turning the switch on mid-run is picked up by
            // the per-frame sync below rather than needing a fresh detection.
            if (settingsReticleVisible()) {
                GetHAL()->lvglLock();
                lv_obj_remove_flag(g_reticle, LV_OBJ_FLAG_HIDDEN);
                GetHAL()->lvglUnlock();
            }
        }
        // Keep it in step with the switch while tracking, so flipping it takes effect at once.
        if (!settingsReticleVisible() && !lv_obj_has_flag(g_reticle, LV_OBJ_FLAG_HIDDEN)) {
            GetHAL()->lvglLock();
            lv_obj_add_flag(g_reticle, LV_OBJ_FLAG_HIDDEN);
            GetHAL()->lvglUnlock();
        }
    } else {
        if (g_tracking) {
            g_tracking = false;
            // They left. Return to the idle behaviour rather than holding the reaction -- and do
            // NOT play a cue for it: a sound on every departure would make the robot chatter at an
            // empty room, which is the failure the band already demonstrated.
            if (g_emotePin < 0 && kStates[g_state].locked < 0) g_nextEmote = now;
            GetHAL()->lvglLock();
            lv_obj_add_flag(g_reticle, LV_OBJ_FLAG_HIDDEN);
            GetHAL()->lvglUnlock();
        }
        // DEGRADES INTO THE AUTONOMOUS SACCADE. Not a fallback: this is the resting behaviour, and
        // a face whose eyes stop moving when nothing is detected reads as broken, not as calm.
        if (due(now, g_nextSaccade)) newSaccade(now);
    }

    // HEAD MOTION, normal path. Ticked every frame rather than only on a new gaze sample, because
    // the slew and the return to centre are continuous and must keep running when perception goes
    // quiet. MOTION IS ALLOWED ONLY WHEN WE KNOW THE ROBOT IS NOT STOPPED: autonomous head motion
    // while the base is e-stopped is the wrong behaviour, and with NO LINK we do not know the state
    // -- so "unknown" reads as "do not move" rather than "assume idle".
    {
        const bool known   = g_linkUp && g_linkState >= 0;
        const bool stopped = stateFault(g_state);
        pantiltTick(now, known && !stopped, haveGaze, gi.nx, gi.ny, gi.confidence);
    }

    // Bench only: alternate plain and themed so the difference is visible and the reserved
    // fault colours can be seen passing through untouched.
    //
    // NOW GATED. This was the last instrument still shipping: a release build changed the face's
    // decorative theme every 18 seconds on a robot in a room, which is the one parked item that was
    // active misbehaviour rather than a missing nicety. A theme is a deliberate choice, not
    // something the face should cycle through on its own.
#if OPENDUBS_DBG_UI
    static uint32_t nextTheme = 18000;
    if (due(now, nextTheme)) {
        nextTheme = now + 18000;
        GetHAL()->lvglLock();
        theme::apply(lv_screen_active(), theme::g_active == theme::NONE ? theme::HALLOWEEN
                                                                        : theme::NONE);
        applyState(g_state);
        GetHAL()->lvglUnlock();
    }
#endif

    // BENCH ONLY. With no StamPLC link the bench walks the states so every colour can be seen --
    // but on a robot this is a face cycling through DEGRADED and E-STOP on a six-second timer,
    // announcing authority changes that did not happen. It is the single most dangerous piece of
    // scaffolding in this file and it must never ship. Phase 2 replaces it with the existing
    // "STATE <NAME>" command; until then it is behind the bench flag and absent from a release.
#if OPENDUBS_DBG_UI
    if (due(now, g_nextState)) {
        g_nextState = now + 6000;
        g_state = (g_state + 1) % kNStates;
        // A mode transition must be AUDIBLE, not only visible: an operator whose eyes are on the
        // machine rather than the panel still needs to know authority changed.
        cuePlay(Cue::Mode);
        GetHAL()->lvglLock();
        applyState(g_state);
        if (kStates[g_state].locked < 0) applyEmote(g_emote);   // refresh the label
        GetHAL()->lvglUnlock();
        g_nextEmote = now + 2400;
    }
#endif
    // Emotes cycle only where the face is free. In a reporting state the pinned emote stands,
    // while saccades and blinks carry on as normal.
    // NOT behind OPENDUBS_DBG_UI any more, and random rather than sequential. Decided 2026-10-06:
    // the robot only ever showed the big neutral eyes because the only thing driving the emote
    // channel was a bench cycler compiled out of the robot build -- so every other emote was dead
    // code. Random cycling is an INTERIM answer, not the design: F1 in the workplan still asks
    // what an emote should MEAN. Until that is decided a face that varies reads as alive, and a
    // sequential walk reads as a demo loop.
    //
    // Only where the face is free: a reporting state's pinned emote still stands, because
    // DEGRADED and E_STOP are communicating something and must not drift off it.
    if (g_emotePin < 0 && kStates[g_state].locked < 0 && due(now, g_nextEmote)) {
        // Never repeat the current one -- a "change" that changes nothing looks like a freeze.
        int pick = g_emote;
        if (kNEmotes > 1) {
            pick = (int)rnd((uint32_t)(kNEmotes - 1));
            if (pick >= g_emote) ++pick;
        }
        g_emote = pick;
        // LOCK HELD. applyEmote() calls lv_label_set_text(), and this was the ONE call site that
        // did not take the lock -- so every 3.2 s it mutated LVGL's label buffer and invalidation
        // list while the LVGL task could be mid-render. An unsynchronised LVGL call corrupts
        // internal state rather than failing cleanly, which is why the symptom was a render task
        // that hung after a variable delay with the heap completely untouched.
        GetHAL()->lvglLock();
        applyEmote(g_emote);
        GetHAL()->lvglUnlock();
        // A fixed period is itself a tell. 4-9 s, so it never settles into a rhythm.
        g_nextEmote = now + 4000 + rnd(5000);
    }
    // THE EYES OPEN, ONCE, as the first thing the face does. onOpen leaves the lids shut so the
    // first presented frame cannot be a half-built one; this is what turns that constraint into
    // something deliberate. The target is the current emote's own lid value, so nothing here
    // special-cases the resting pose, and animationType is left alone -- g_lidDrop is a spring,
    // and a spring's slight overshoot is exactly how an eyelid opens.
    static bool woke = false;
    if (!woke) {
        woke = true;
        g_lidDrop = kEmotes[g_emote].lid;
        // g_nextBlink initialises to 0, so without this a blink would fire on this very frame and
        // fight the wake.
        g_nextBlink = now + 2600;
    }

    if (!g_blinking && due(now, g_nextBlink)) {
        g_blinking = true;
        g_lidDrop.easingOptions().duration = 0.07f;
        g_lidDrop = 1.0f;
        g_blinkEnd = now + 90;
    } else if (g_blinking && due(now, g_blinkEnd)) {
        g_blinking = false;
        g_lidDrop = kEmotes[g_emote].lid;
        g_nextBlink = now + 2200 + (rand() % 3000);
    }

    // FAULT is not a choice: the two reporting states that mean something is wrong own it, and a
    // theme can never recolour or re-time it into ambiguity.
    if (stateFault(g_state)) {
        band::g_activity = band::FAULT;
        cueFaultActive(true);
    } else if (stateCharging(g_state)) {
        cueFaultActive(false);
        band::g_activity = band::GAUGE;
        // Bench stand-in: walk the level so it is visibly a gauge. On the robot this is the
        // pack SOC the BMS already reports.
        band::g_soc = 0.05f + 0.90f * (0.5f + 0.5f * sinf(now / 9000.0f));
    } else {
        cueFaultActive(false);
        // No audio or Thor on the bench, so walk the vocabulary to see the motions.
        static uint32_t nextAct = 0;
        if (due(now, nextAct)) {
            nextAct = now + 5000;
            const band::Activity cycle[] = {band::IDLE, band::LISTEN, band::THINK, band::SPEAK};
            static int k = 0;
            band::g_activity = cycle[k++ % 4];
        }
    }

    GetHAL()->lvglLock();
    renderFace(now);
    GetHAL()->lvglUnlock();
}

void AppFace::onClose() { mclog::tagInfo(getAppInfo().name, "on close"); }
