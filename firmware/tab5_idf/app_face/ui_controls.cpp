#include "ui_controls.h"
#include "touch_ui_config.h"
#include "ui_grade.h"
#include "face_mailbox.h"
#include "dbg_stimulus.h"
#include <cstdio>
#include <cstring>
#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#endif

#if OPENDUBS_TOUCH_UI

namespace {

constexpr int kW = 1280, kH = 720;

// ---- palette ----------------------------------------------------------------------------------
// Lifted from firmware/tab5_face/src/hmi.cpp so the two heads cannot drift on it, exactly as
// app_face.cpp lifted the state colours from ui_shared.cpp for the same reason.
//
// ONE DELIBERATE OMISSION: no large red. The Arduino HMI's e-stop is a 150 px red octagon
// labelled "E-STOP", and the dashboard note's rewrite calls that out as the thing it got most
// wrong — "The *action* is safe; the **affordance** is the hazard. A big red STOP on glass next
// to a real mushroom-head e-stop teaches a bystander that the glass is the emergency control."
// So red here is reserved for FAULT ANNUNCIATION, which is a statement rather than a target, and
// the software stop is amber and ordinary-sized and says what it is.
constexpr uint32_t GROUND = 0x0a0e13;
constexpr uint32_t PANEL  = 0x141b22;
constexpr uint32_t HAIR   = 0x26313b;
constexpr uint32_t INK    = 0xe8eef2;
constexpr uint32_t INKDIM = 0x8b9aa6;
constexpr uint32_t INKFT  = 0x5a6670;
constexpr uint32_t CYAN   = 0x38d6f0;
constexpr uint32_t GOOD   = 0x5ed98a;
constexpr uint32_t AMBER  = 0xffb24d;
constexpr uint32_t REDBRT = 0xff5a5a;
constexpr uint32_t STALEC = 0x7a6a3a;   // a value we HAD: legible, visibly not live

// ---- readout rows -----------------------------------------------------------------------------
//
// STALENESS THRESHOLDS ARE PER GROUP, and each is a number with a source rather than a taste.
// A single global threshold would be wrong in both directions at once: 5 s would let a dead RC
// link look live for five seconds, and 500 ms would show the IP as stale constantly.
struct RowSpec {
    const char* key;
    MbGroup     grp;
    uint32_t    staleMs;
};
const RowSpec kRows[] = {
    {"MODE",       MB_MODE,    1500},   // the FSM pushes on change + heartbeat
    {"E-STOP",     MB_ESTOP,   1500},   // the chain is the one thing that must never look live
    {"BATTERY",    MB_BATTERY, 5000},   // SOC moves over minutes; the BMS stream is faster
    {"RC LINK",    MB_RC,      1000},   // the dashboard treats RC freshness sub-second
    {"THOR LINK",  MB_THOR,    5000},   // architecture note §1.9: 5 s, "as today"
    {"Z-AXIS",     MB_LIFT,    1500},   // exactly LiftController::REPORT_STALE_MS
    {"IP",         MB_NET,    30000},   // an association does not go stale in a second
    {"HEAD",       MB_GROUP_COUNT, 0},  // our OWN health: always live by definition, no age
};
constexpr int kNRows = (int)(sizeof(kRows) / sizeof(kRows[0]));

struct Row {
    lv_obj_t* val = nullptr;
    lv_obj_t* age = nullptr;
    char      valBuf[56] = {0};
    char      ageBuf[20] = {0};
    uint32_t  lastColour = 0xffffffff;
};
Row g_row[kNRows];

// ---- faults -----------------------------------------------------------------------------------
//
// WHAT FAILED, AND WHAT CLEARS IT. The second column is the entire reason this screen exists:
// the dashboard already shows a fault name, and a name on its own sends the person who walked
// over to the robot back to a laptop to find out what to do about it.
//
// The flags and the wording track software/kiosk-dashboard/backend/status.py's `_robot_state`
// and `try_estop_clear`, so the panel cannot describe a clearing procedure the robot does not
// implement. ORDER IS PRIORITY: the first active row is the headline.
struct FaultRow {
    uint32_t    bit;
    bool        fatal;
    const char* what;
    const char* clears;
};
const FaultRow kFaults[] = {
    {MB_FAULT_CONTACTOR_WELDED, true,
     "DRIVE CONTACTOR WELDED",
     "The contactor was commanded open and its aux contact still reads closed, so power cannot\n"
     "be removed by the contactor. This needs service. Do not attempt to clear it; use the\n"
     "battery disconnect before working on the drive."},
    {MB_FAULT_PDH_HARDWARE, true,
     "POWER DISTRIBUTION HUB FAULT",
     "The PDH reports a hardware fault. This needs service; FAULT CLEAR will not clear it."},
    {MB_FAULT_ESTOP_LATCHED, false,
     "E-STOP LATCHED",
     "Release the mushroom head, then re-arm. Re-arm is refused while the button is still\n"
     "pressed, while telemetry is stale, when the contactor reads welded, or when CH5 is not\n"
     "OFF. The re-arm decision is the robot's, not this panel's."},
    {MB_FAULT_PDH_BROWNOUT, false,
     "POWER DISTRIBUTION HUB BROWN-OUT",
     "Clears when the rail recovers. Check the pack state of charge and the main fuse, then\n"
     "FAULT CLEAR on the technician screen."},
    {MB_FAULT_OVERTEMP, false,
     "OVER TEMPERATURE",
     "Clears by itself once it cools. Stop driving and let it stand; do not clear the fault to\n"
     "keep working."},
    {MB_FAULT_LIFT_SILENT, false,
     "Z-AXIS MODULE NOT REPORTING",
     "Clears as soon as the StamPLC reports the lift again. Check the lift harness. A StamPLC\n"
     "reboot — which every OTA flash causes — also shows this for a few seconds."},
    {MB_FAULT_RC_LOST, false,
     "RC LINK LOST",
     "Clears when the transmitter is back. Check that it is switched on and in range."},
};
constexpr int kNFaults = (int)(sizeof(kFaults) / sizeof(kFaults[0]));

// ---- actions ----------------------------------------------------------------------------------
enum Act : int32_t {
    ACT_MAST_UP = 1, ACT_MAST_DOWN,
    ACT_SOFT_STOP, ACT_AUX_ON, ACT_AUX_OFF,
    ACT_MUTE, ACT_FAULT_CLEAR,
    ACT_DELIBERATE_ENTER, ACT_DELIBERATE_EXIT,
    ACT_REARM,
    ACT_FAULT_ACK, ACT_FAULT_DETAILS
};

// ---- objects ----------------------------------------------------------------------------------
lv_obj_t* g_tech     = nullptr;
lv_obj_t* g_fault    = nullptr;
lv_obj_t* g_banner   = nullptr;   // deliberate-mode banner
lv_obj_t* g_revert   = nullptr;   // "face in Ns"
lv_obj_t* g_faultBox = nullptr;   // the fault summary on the technician screen
lv_obj_t* g_fHead    = nullptr;   // fault screen headline
lv_obj_t* g_fBody    = nullptr;   // fault screen "what clears it"
lv_obj_t* g_fMore    = nullptr;
lv_obj_t* g_dbg1     = nullptr;   // debug builds only
lv_obj_t* g_dbg2     = nullptr;

struct Btn {
    lv_obj_t* root = nullptr;
    lv_obj_t* lbl  = nullptr;
    lv_obj_t* sub  = nullptr;
    lv_obj_t* bar  = nullptr;
    int       id   = -1;
    char      subBuf[72] = {0};
};
Btn g_bMastUp, g_bMastDn, g_bSoftStop, g_bAux, g_bMute, g_bFaultClr, g_bAdv;
Btn g_bRearm, g_bAdvExit;
Btn g_bAck, g_bDetails;

// ---- our published intent ---------------------------------------------------------------------
// One owner (the LVGL task), so a plain struct plus a publish is enough; the seqlock in
// face_mailbox.cpp is what makes the reader safe.
UiIntent g_intent{};
bool     g_auxOn = false;

bool     g_deliberate = false;
uint32_t g_delibSince = 0;
uint32_t g_ackUntil   = 0;
bool     g_ackValid   = false;
bool     g_wantTech   = false;
char     g_status[160] = "touch: idle";

// A cached copy of the snapshot, refreshed at the top of each tick so every reader in this file
// sees one generation. Reading the mailbox per row would be correct but could straddle a publish
// and show two different robots in two rows.
RobotSnapshot g_snap{};
uint32_t      g_lastRefresh = 0;

inline bool due(uint32_t now, uint32_t at) { return (int32_t)(now - at) >= 0; }

// ---- small helpers ----------------------------------------------------------------------------

// RE-SET ONLY ON CHANGE. lv_label_set_text_static keeps our pointer, so mutating the buffer
// alone would leave the label un-invalidated and showing the old string; re-calling it with the
// same pointer re-measures and invalidates without a malloc. The strcmp is the change-guard
// AUDIT.md invariant 8 asks for — without it a 4 Hz refresh repaints every label whether or not
// anything moved.
void setStatic(lv_obj_t* lbl, char* buf, size_t cap, const char* s)
{
    if (!lbl) return;
    if (std::strncmp(buf, s, cap - 1) == 0) return;
    std::snprintf(buf, cap, "%s", s);
    lv_label_set_text_static(lbl, buf);
}

void setColour(lv_obj_t* o, uint32_t* last, uint32_t rgb)
{
    if (*last == rgb) return;
    lv_obj_set_style_text_color(o, lv_color_hex(rgb), LV_PART_MAIN);
    *last = rgb;
}

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h, uint32_t bg, uint32_t border)
{
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(o, lv_color_hex(bg), LV_PART_MAIN);
    if (border) {
        lv_obj_set_style_border_width(o, 2, LV_PART_MAIN);
        lv_obj_set_style_border_color(o, lv_color_hex(border), LV_PART_MAIN);
        lv_obj_set_style_border_opa(o, LV_OPA_COVER, LV_PART_MAIN);
    }
    lv_obj_set_style_radius(o, 10, LV_PART_MAIN);
    return o;
}

lv_obj_t* text(lv_obj_t* parent, int x, int y, const lv_font_t* f, uint32_t colour,
               const char* initial)
{
    lv_obj_t* l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, f, LV_PART_MAIN);
    lv_obj_set_style_text_color(l, lv_color_hex(colour), LV_PART_MAIN);
    lv_obj_set_pos(l, x, y);
    lv_label_set_text_static(l, initial);
    return l;
}

// ---- intents ----------------------------------------------------------------------------------

uint32_t g_mastPublishes = 0;     // the countable verdict for the heartbeat; see below

void publishMast(float v, uint32_t nowMs)
{
    g_intent.mast   = v;
    g_intent.mastMs = nowMs;      // the stamp IS the authorisation; staleness here means stop
    ++g_mastPublishes;
    mbIntentPublish(g_intent);
}

void publishReq(MbRequest r)
{
    g_intent.req = (uint8_t)r;
    ++g_intent.reqSeq;            // a NEW request, distinguishable from a redelivery
    mbIntentPublish(g_intent);
}

// ---- grade-2 handler --------------------------------------------------------------------------
void onHold(int32_t arg, bool held, uint32_t nowMs)
{
    const float v = (arg == ACT_MAST_UP) ? 1.0f : -1.0f;
    // The release edge publishes an EXPLICIT ZERO rather than merely going quiet. Silence at the
    // far end is treated as stop too, but saying so is faster than timing out — the same
    // reasoning firmware/tab5_face/src/lift.h gives for sending Motion::STOP.
    publishMast(held ? v : 0.0f, nowMs);
}

// ---- grade-1/3/4 handler ----------------------------------------------------------------------
void onFire(int32_t arg)
{
    const uint32_t now = lv_tick_get();
    switch (arg) {
        case ACT_SOFT_STOP:    publishReq(MB_REQ_SOFT_STOP); break;
        case ACT_AUX_ON:       g_auxOn = true;  publishReq(MB_REQ_AUX_RELAY_ON);  break;
        case ACT_AUX_OFF:      g_auxOn = false; publishReq(MB_REQ_AUX_RELAY_OFF); break;
        case ACT_MUTE:         g_intent.mute = !g_intent.mute; mbIntentPublish(g_intent); break;
        case ACT_FAULT_CLEAR:  publishReq(MB_REQ_FAULT_CLEAR); break;
        case ACT_REARM:        publishReq(MB_REQ_ESTOP_REARM); break;
        case ACT_DELIBERATE_ENTER: uiControlsSetDeliberate(true,  now); break;
        case ACT_DELIBERATE_EXIT:  uiControlsSetDeliberate(false, now); break;
        case ACT_FAULT_ACK:
            // SUPPRESSED, NOT DISMISSED. The fault screen comes back when the window expires if
            // the condition still holds, because a fault someone walked away from is still a
            // fault — and the band on the face goes on saying so in the meantime.
            g_ackUntil = now + 30000;
            g_ackValid = true;
            break;
        case ACT_FAULT_DETAILS: g_wantTech = true; break;
        default: break;
    }
}

// ---- button construction ----------------------------------------------------------------------
// `sub` is the STATED PRECONDITION, and it is not decoration: grade 3's test in the dashboard
// note is "a named action, a stated precondition, and a sustained gesture with visible progress".
// A hold-to-confirm with no precondition on the glass is a slower tap.
void buildBtn(Btn& b, lv_obj_t* parent, int x, int y, int w, int h, const char* label,
              const char* sub, Grade grade, int32_t act, bool isHold)
{
    b.root = box(parent, x, y, w, h, 0x1a2430, HAIR);
    b.lbl  = text(b.root, 16, sub ? h / 2 - 34 : h / 2 - 16, &lv_font_montserrat_24, INK, label);
    if (sub) {
        std::snprintf(b.subBuf, sizeof(b.subBuf), "%s", sub);
        b.sub = text(b.root, 16, h / 2 + 2, &lv_font_montserrat_18, INKDIM, b.subBuf);
    }
    if (grade == Grade::HoldToConfirm || grade == Grade::Deliberate) {
        // The progress bar: a child rect at the bottom edge whose WIDTH ui_grade.cpp owns.
        // Width 0 at rest. A bar and not a ring — see the note at the top of ui_grade.h.
        b.bar = lv_obj_create(b.root);
        lv_obj_remove_style_all(b.bar);
        lv_obj_remove_flag(b.bar, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_remove_flag(b.bar, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_pos(b.bar, 0, h - 10);
        lv_obj_set_size(b.bar, 0, 8);
        lv_obj_set_style_bg_opa(b.bar, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_bg_color(b.bar, lv_color_hex(CYAN), LV_PART_MAIN);
    }
    b.id = gradeAdd(b.root, grade, act, isHold ? nullptr : onFire, isHold ? onHold : nullptr,
                    b.bar, w);
}

// ---- readout rendering ------------------------------------------------------------------------

void fmtAge(char* out, size_t cap, uint32_t age)
{
    if (age == MB_AGE_NEVER)   { std::snprintf(out, cap, "no data"); return; }
    if (age < 1000)            { out[0] = 0; return; }          // live: say nothing
    if (age < 10000)           { std::snprintf(out, cap, "%u.%us old", (unsigned)(age / 1000),
                                               (unsigned)((age % 1000) / 100)); return; }
    if (age < 600000)          { std::snprintf(out, cap, "%us old", (unsigned)(age / 1000)); return; }
    std::snprintf(out, cap, ">10min old");
}

const char* authName(uint8_t a)
{
    switch (a) {
        case MB_AUTH_OFF:    return "CH5 OFF";
        case MB_AUTH_MANUAL: return "MANUAL";
        case MB_AUTH_AUTO:   return "AUTO";
        default:             return "unknown";
    }
}

void renderRow(int i, uint32_t nowMs)
{
    const RowSpec& spec = kRows[i];
    Row& r = g_row[i];
    char v[56];
    char a[20] = {0};

    // The HEAD row is our own health, which cannot be stale by construction — it is read from
    // this chip. Architecture note §7.3 asks for internal heap on the technician card in
    // release, and it is the one readout that has already settled a whole class of
    // investigation: "23 MB free, 22 MB largest block, and frozen" (AUDIT.md).
    if (spec.grp == MB_GROUP_COUNT) {
#ifdef ESP_PLATFORM
        std::snprintf(v, sizeof(v), "heap %uk free / %uk largest  ·  up %um",
                      (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                      (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) / 1024),
                      (unsigned)(nowMs / 60000));
#else
        std::snprintf(v, sizeof(v), "desktop build  ·  up %um", (unsigned)(nowMs / 60000));
#endif
        setStatic(r.val, r.valBuf, sizeof(r.valBuf), v);
        setStatic(r.age, r.ageBuf, sizeof(r.ageBuf), "");
        setColour(r.val, &r.lastColour, INKDIM);
        return;
    }

    const uint32_t age   = mbAge(g_snap, spec.grp, nowMs);
    const bool     never = (age == MB_AGE_NEVER);
    const bool     stale = !never && age > spec.staleMs;

    if (never) {
        // NO NUMBER EXISTS. Not a zero, not a default, not last boot's value.
        std::snprintf(v, sizeof(v), "--");
    } else {
        switch (spec.grp) {
            case MB_MODE:
                std::snprintf(v, sizeof(v), "%s", g_snap.stateName[0] ? g_snap.stateName : "?");
                break;
            case MB_ESTOP:
                std::snprintf(v, sizeof(v), "button %s  ·  contactor %s%s",
                              g_snap.estopButton ? "PRESSED" : "released",
                              g_snap.contactorEnergized ? "closed" : "OPEN",
                              g_snap.contactorWelded ? "  ·  WELDED" : "");
                break;
            case MB_BATTERY:
                std::snprintf(v, sizeof(v), "%u%%  ·  %u.%u V  ·  %s%d.%u A",
                              (unsigned)g_snap.socPct,
                              (unsigned)(g_snap.packDv / 10), (unsigned)(g_snap.packDv % 10),
                              g_snap.packDa < 0 ? "-" : "",
                              (int)(g_snap.packDa < 0 ? -g_snap.packDa : g_snap.packDa) / 10,
                              (unsigned)((g_snap.packDa < 0 ? -g_snap.packDa : g_snap.packDa) % 10));
                break;
            case MB_RC:
                std::snprintf(v, sizeof(v), "%s  ·  %s  ·  deadman %s",
                              g_snap.rcLive ? "live" : "NO SIGNAL",
                              authName(g_snap.rcAuthority),
                              g_snap.rcDeadman ? "held" : "released");
                break;
            case MB_THOR:
                std::snprintf(v, sizeof(v), "%s", stale ? "not responding" : "responding");
                break;
            case MB_LIFT:
                if (g_snap.liftHeightMm < 0)
                    std::snprintf(v, sizeof(v), "height unknown  ·  %s%s",
                                  g_snap.liftMotion == 1 ? "raising"
                                  : g_snap.liftMotion == 2 ? "lowering" : "stopped",
                                  g_snap.liftGuardHold ? "  ·  GUARD HOLDING" : "");
                else
                    std::snprintf(v, sizeof(v), "%d mm  ·  %s%s%s", (int)g_snap.liftHeightMm,
                                  g_snap.liftMotion == 1 ? "raising"
                                  : g_snap.liftMotion == 2 ? "lowering" : "stopped",
                                  g_snap.liftAtTop ? "  ·  at top"
                                  : g_snap.liftAtBot ? "  ·  at bottom" : "",
                                  g_snap.liftGuardHold ? "  ·  GUARD HOLDING" : "");
                break;
            case MB_NET:
                std::snprintf(v, sizeof(v), "%s", g_snap.ip[0] ? g_snap.ip : "not associated");
                break;
            default:
                std::snprintf(v, sizeof(v), "--");
                break;
        }
    }
    fmtAge(a, sizeof(a), age);
    setStatic(r.val, r.valBuf, sizeof(r.valBuf), v);
    setStatic(r.age, r.ageBuf, sizeof(r.ageBuf), a);
    setColour(r.val, &r.lastColour, never ? INKFT : (stale ? STALEC : INK));
}

// ---- preconditions ----------------------------------------------------------------------------
//
// THE PANEL DOES NOT ENFORCE SAFETY HERE, AND MUST NOT BE READ AS DOING SO. Every guard that
// matters is on the robot: the pinch guard, the limits, the deadman, CH5, the supervisor's
// re-arm conditions. This function exists for a human-factors reason only — the dashboard note's
// "With CH5 in OFF a panel jog does nothing, and the panel must say *why* rather than appearing
// broken." A disabled button with a reason under it is the difference between a rule you can see
// and "a just-right flick of the stick".
struct Gate { bool ok; const char* why; };

Gate gateJog(uint32_t nowMs, bool up)
{
    if (mbAge(g_snap, MB_ESTOP, nowMs) > kRows[1].staleMs) return {false, "no fresh telemetry"};
    if (!g_snap.contactorEnergized)                        return {false, "contactor open"};
    if (g_snap.rcAuthority == MB_AUTH_OFF)                 return {false, "CH5 is OFF"};
    if (g_snap.rcAuthority == MB_AUTH_UNKNOWN)             return {false, "authority unknown"};
    if (up   && g_snap.liftAtTop)                          return {false, "at the top limit"};
    if (!up  && g_snap.liftAtBot)                          return {false, "at the bottom limit"};
    return {true, up ? "hold to raise  ·  release stops" : "hold to lower  ·  release stops"};
}

Gate gateSoftStop(uint32_t nowMs)
{
    if (mbAge(g_snap, MB_ESTOP, nowMs) > kRows[1].staleMs) return {false, "no fresh telemetry"};
    if (!g_snap.contactorEnergized)                        return {false, "already de-energized"};
    return {true, "hold 1s  ·  software stop, drops the contactor"};
}

Gate gateRearm(uint32_t nowMs)
{
    // Locally-derivable blockers only, and labelled as such. The authoritative answer comes from
    // the supervisor and the panel must render whatever it returns; until the transport exists
    // this is the honest subset — it can say "no" for a reason it can see, and it can never say
    // "yes" on the robot's behalf.
    if (mbAge(g_snap, MB_ESTOP, nowMs) > kRows[1].staleMs) return {false, "telemetry is stale"};
    if (g_snap.estopButton)                                return {false, "button still pressed"};
    if (g_snap.contactorWelded)                            return {false, "contactor reads welded"};
    if (g_snap.rcAuthority != MB_AUTH_OFF)                 return {false, "CH5 is not OFF"};
    return {true, "hold 1.5s to REQUEST  ·  the robot decides"};
}

void applyGate(Btn& b, const Gate& g)
{
    gradeSetEnabled(b.id, g.ok);
    if (b.sub) setStatic(b.sub, b.subBuf, sizeof(b.subBuf), g.why);
}

int firstActiveFault(uint32_t bits, int* count)
{
    int n = 0, first = -1;
    for (int i = 0; i < kNFaults; ++i)
        if (bits & kFaults[i].bit) { if (first < 0) first = i; ++n; }
    if (count) *count = n;
    return first;
}

// ---- the two screens --------------------------------------------------------------------------

void buildTech(lv_obj_t* parent)
{
    g_tech = box(parent, 0, 0, kW, kH, GROUND, 0);
    lv_obj_set_style_radius(g_tech, 0, LV_PART_MAIN);
    lv_obj_add_flag(g_tech, LV_OBJ_FLAG_HIDDEN);
    // OPAQUE AND CLICKABLE AS A WHOLE, so a touch that misses every control lands here and is
    // swallowed rather than reaching the face underneath. Without this the technician screen is
    // a decal over a live face.
    lv_obj_add_flag(g_tech, LV_OBJ_FLAG_CLICKABLE);

    text(g_tech, 24, 16, &lv_font_montserrat_24, INK, "OpenDubs");
    text(g_tech, 170, 20, &lv_font_montserrat_18, CYAN, "TECHNICIAN");
    g_revert = lv_label_create(g_tech);
    lv_obj_set_style_text_font(g_revert, &lv_font_montserrat_18, LV_PART_MAIN);
    lv_obj_set_style_text_color(g_revert, lv_color_hex(INKFT), LV_PART_MAIN);
    lv_obj_set_pos(g_revert, kW - 260, 20);
    lv_label_set_text_static(g_revert, "");

    // The deliberate-mode banner. "a visible mode banner" is part of grade 4's definition, not
    // a nicety: the whole point of a mode is that you can tell you are in it.
    g_banner = box(g_tech, 24, 52, kW - 48, 40, 0x3a2a08, AMBER);
    text(g_banner, 14, 7, &lv_font_montserrat_18, AMBER,
         "ADVANCED MODE  ·  controls that GRANT energy are enabled  ·  leaves itself in 20 s");
    lv_obj_add_flag(g_banner, LV_OBJ_FLAG_HIDDEN);

    // Readouts. Key / value / age, three labels per row, and the age column is never optional.
    for (int i = 0; i < kNRows; ++i) {
        const int y = 108 + i * 56;
        text(g_tech, 24, y + 6, &lv_font_montserrat_18, INKFT, kRows[i].key);
        g_row[i].val = text(g_tech, 168, y, &lv_font_montserrat_24, INKFT, "--");
        g_row[i].age = lv_label_create(g_tech);
        lv_obj_set_style_text_font(g_row[i].age, &lv_font_montserrat_18, LV_PART_MAIN);
        lv_obj_set_style_text_color(g_row[i].age, lv_color_hex(AMBER), LV_PART_MAIN);
        lv_obj_set_pos(g_row[i].age, 24, y + 30);
        lv_label_set_text_static(g_row[i].age, "");
    }

    // THE POINTER AT THE REAL E-STOP. "The robot's emergency control is a mushroom head you hit
    // without reading, and nothing on a screen may compete with it for that role."
    text(g_tech, 24, 576, &lv_font_montserrat_18, INKDIM,
         "The red mushroom heads are the emergency stop. They break the\n"
         "contactor coil directly and work with this screen dead — which it is\n"
         "whenever the contactor is already open, because this panel is powered\n"
         "from the e-stop-protected drive branch.");

#if OPENDUBS_TOUCH_DEBUG
    // THE COUNTABLE VERDICT. Without a `net`, holding MAST UP produces no observable effect
    // anywhere — which is indistinguishable from a broken control, and CLAUDE.md's bench rules
    // say so in as many words: "a feature triggered by a serial write cannot be distinguished
    // from a broken feature when nothing happens." A republish COUNTER fixes that with nothing
    // attached: hold the jog for two seconds and the count must rise by about 40 (50 ms
    // republish), then stop rising within one tick of release, and the intent must read EXPIRED.
    // That is the grade-2 contract, measured on the panel, by one person with one finger.
    g_dbg1 = text(g_tech, 24, 668, &lv_font_montserrat_18, 0xff5ad6, "dbg intent: --");
    g_dbg2 = text(g_tech, 24, 692, &lv_font_montserrat_18, 0xff5ad6, "dbg: --");
#endif

    // Faults, summarised. The detail lives on the fault screen.
    g_faultBox = box(g_tech, 544, 108, kW - 568, 140, PANEL, HAIR);
    text(g_faultBox, 16, 10, &lv_font_montserrat_18, INKFT, "FAULTS");
    g_fMore = text(g_faultBox, 16, 44, &lv_font_montserrat_24, GOOD, "none");

    // GRADE 2 — the mast jog. The biggest targets on the screen at 340 x 160 px, which at the
    // panel's 0.0865 mm/px is 29 x 14 mm: comfortably past the ~9 mm a finger needs, and sized
    // for someone holding it while watching the clearance rather than watching the glass.
    buildBtn(g_bMastUp, g_tech, 544, 272, 340, 160, "MAST  UP",
             "hold to raise  ·  release stops", Grade::HoldToAct, ACT_MAST_UP, true);
    buildBtn(g_bMastDn, g_tech, 904, 272, 340, 160, "MAST  DOWN",
             "hold to lower  ·  release stops", Grade::HoldToAct, ACT_MAST_DOWN, true);

    // GRADE 3 — discrete and consequential.
    buildBtn(g_bSoftStop, g_tech, 544, 444, 340, 112, "SOFTWARE STOP",
             "hold 1s  ·  drops the contactor", Grade::HoldToConfirm, ACT_SOFT_STOP, false);
    buildBtn(g_bAux, g_tech, 904, 444, 340, 112, "AUX RELAY 4",
             "hold 1s  ·  spare channel", Grade::HoldToConfirm, ACT_AUX_ON, false);

    // GRADE 1 — nothing moves, nothing is energised, undone by its own inverse.
    buildBtn(g_bMute, g_tech, 544, 568, 220, 112, "MUTE", nullptr, Grade::Tap, ACT_MUTE, false);
    buildBtn(g_bFaultClr, g_tech, 784, 568, 220, 112, "FAULT\nCLEAR", nullptr, Grade::Tap,
             ACT_FAULT_CLEAR, false);
    // The DOOR to grade 4, itself grade 3: a named action with a stated consequence and a
    // sustained gesture. It is labelled because by the time someone is on the technician screen
    // they have already made a deliberate gesture to get here; the non-obvious gate is the one
    // on the FACE (ui_touch.cpp), which is where a bystander is.
    buildBtn(g_bAdv, g_tech, 1024, 568, 220, 112, "ADVANCED", nullptr, Grade::HoldToConfirm,
             ACT_DELIBERATE_ENTER, false);

    // GRADE 4 — only visible in deliberate mode, and it occupies the same row, so the mode
    // visibly replaces the ordinary controls rather than quietly adding to them.
    buildBtn(g_bRearm, g_tech, 544, 568, 460, 112, "REQUEST E-STOP RE-ARM",
             "hold 1.5s to REQUEST  ·  the robot decides", Grade::Deliberate, ACT_REARM, false);
    buildBtn(g_bAdvExit, g_tech, 1024, 568, 220, 112, "LEAVE\nADVANCED", nullptr, Grade::Tap,
             ACT_DELIBERATE_EXIT, false);
    lv_obj_add_flag(g_bRearm.root,   LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_bAdvExit.root, LV_OBJ_FLAG_HIDDEN);
}

void buildFault(lv_obj_t* parent)
{
    g_fault = box(parent, 0, 0, kW, kH, 0x160a0a, 0);
    lv_obj_set_style_radius(g_fault, 0, LV_PART_MAIN);
    lv_obj_add_flag(g_fault, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(g_fault, LV_OBJ_FLAG_CLICKABLE);

    // A red BAND, not a red button. Annunciation is a statement; the only red targets on this
    // robot are the mushroom heads.
    lv_obj_t* bar = box(g_fault, 0, 0, kW, 10, REDBRT, 0);
    lv_obj_set_style_radius(bar, 0, LV_PART_MAIN);

    text(g_fault, 48, 48, &lv_font_montserrat_18, REDBRT, "FAULT");
    g_fHead = text(g_fault, 48, 84, &lv_font_montserrat_40, INK, "--");
    text(g_fault, 48, 182, &lv_font_montserrat_18, INKFT, "WHAT CLEARS IT");
    g_fBody = text(g_fault, 48, 214, &lv_font_montserrat_24, INK, "--");

    buildBtn(g_bAck, g_fault, 48, 540, 340, 112, "ACKNOWLEDGE",
             "returns to the face  ·  comes back if it persists", Grade::Tap, ACT_FAULT_ACK, false);
    buildBtn(g_bDetails, g_fault, 408, 540, 340, 112, "TECHNICIAN",
             "the full readout, and the controls", Grade::Tap, ACT_FAULT_DETAILS, false);
}

char g_fBodyBuf[320];
char g_fHeadBuf[64];
char g_fMoreBuf[96];
char g_revertBuf[32];
char g_dbg1Buf[200];
char g_dbg2Buf[200];

}  // namespace

// -----------------------------------------------------------------------------------------------

void uiControlsBuild(lv_obj_t* parent)
{
    buildTech(parent);
    buildFault(parent);
    // Both layers above the face, fault above technician. Set once at build; nothing re-orders
    // them at runtime, so there is no per-frame z-order work.
    lv_obj_move_foreground(g_tech);
    lv_obj_move_foreground(g_fault);
    g_intent.screen = 0;
    mbIntentPublish(g_intent);
}

void uiControlsShowTech(bool on)
{
    if (!g_tech) return;
    if (on) lv_obj_remove_flag(g_tech, LV_OBJ_FLAG_HIDDEN);
    else    lv_obj_add_flag(g_tech, LV_OBJ_FLAG_HIDDEN);
    g_intent.screen = on ? 1 : (uiControlsFaultPending(lv_tick_get()) ? 2 : 0);
    mbIntentPublish(g_intent);
}

void uiControlsShowFault(bool on)
{
    if (!g_fault) return;
    if (on) lv_obj_remove_flag(g_fault, LV_OBJ_FLAG_HIDDEN);
    else    lv_obj_add_flag(g_fault, LV_OBJ_FLAG_HIDDEN);
    if (on) { g_intent.screen = 2; mbIntentPublish(g_intent); }
}

void uiControlsSetDeliberate(bool on, uint32_t nowMs)
{
    g_deliberate = on;
    g_delibSince = nowMs;
    if (!g_banner) return;
    if (on) lv_obj_remove_flag(g_banner, LV_OBJ_FLAG_HIDDEN);
    else    lv_obj_add_flag(g_banner, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t* ordinary[3] = {g_bMute.root, g_bFaultClr.root, g_bAdv.root};
    for (lv_obj_t* o : ordinary) {
        if (!o) continue;
        if (on) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        else    lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_t* advanced[2] = {g_bRearm.root, g_bAdvExit.root};
    for (lv_obj_t* o : advanced) {
        if (!o) continue;
        if (on) lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
        else    lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
    // LEAVING THE MODE ABANDONS ANY GESTURE IN IT. A hold that completes against a hidden
    // button is the worst possible outcome of a mode timeout.
    if (!on) gradeAbortAll(nowMs);
}

bool     uiControlsDeliberate()      { return g_deliberate; }
uint32_t uiControlsDeliberateSince() { return g_delibSince; }

bool uiControlsFaultPending(uint32_t nowMs)
{
    RobotSnapshot s;
    mbSnapshotGet(&s);
    if ((s.valid & (1u << MB_FAULTS)) == 0) return false;   // never heard: not a fault, unknown
    if (s.faultBits == 0) { g_ackValid = false; return false; }
    if (g_ackValid && !due(nowMs, g_ackUntil)) return false;
    g_ackValid = false;
    return true;
}

void uiControlsTick(uint32_t nowMs)
{
    const bool techVisible  = g_tech  && !lv_obj_has_flag(g_tech,  LV_OBJ_FLAG_HIDDEN);
    const bool faultVisible = g_fault && !lv_obj_has_flag(g_fault, LV_OBJ_FLAG_HIDDEN);

    // GRADES RUN EVEN WHEN NOTHING IS VISIBLE. A held jog whose screen was pulled out from under
    // it must still reach its stop, and gradeTick's independent re-check is what delivers it.
    gradeTick(nowMs);

    if (!techVisible && !faultVisible) {
        std::snprintf(g_status, sizeof(g_status), "touch: face  ·  %s", gradeStatus());
        return;
    }

    // 4 Hz for the readouts, which is as fast as a number a human is reading needs to move, and
    // eight times cheaper than the 33 ms tick. The grade machinery above is NOT on this divider:
    // a jog's heartbeat and a confirm's progress are both per-tick concerns.
    if (!due(nowMs, g_lastRefresh)) return;
    g_lastRefresh = nowMs + 250;
    mbSnapshotGet(&g_snap);

    if (techVisible) {
        for (int i = 0; i < kNRows; ++i) renderRow(i, nowMs);

        int n = 0;
        const int f = firstActiveFault(g_snap.faultBits, &n);
        char buf[96];
        if ((g_snap.valid & (1u << MB_FAULTS)) == 0) std::snprintf(buf, sizeof(buf), "unknown");
        else if (f < 0)                              std::snprintf(buf, sizeof(buf), "none");
        else if (n == 1)                             std::snprintf(buf, sizeof(buf), "%s", kFaults[f].what);
        else std::snprintf(buf, sizeof(buf), "%s  (+%d more)", kFaults[f].what, n - 1);
        setStatic(g_fMore, g_fMoreBuf, sizeof(g_fMoreBuf), buf);
        lv_obj_set_style_text_color(g_fMore, lv_color_hex(f < 0 ? GOOD : REDBRT), LV_PART_MAIN);

        // Re-gate everything, every refresh. A precondition that changed must reach the glass
        // within 250 ms, because the operator's next action depends on it.
        applyGate(g_bMastUp,    gateJog(nowMs, true));
        applyGate(g_bMastDn,    gateJog(nowMs, false));
        applyGate(g_bSoftStop,  gateSoftStop(nowMs));
        applyGate(g_bRearm,     gateRearm(nowMs));
        gradeSetEnabled(g_bAux.id, mbAge(g_snap, MB_ESTOP, nowMs) <= kRows[1].staleMs);
        // FAULT CLEAR is only meaningful for a non-fatal fault that is actually present.
        uint32_t clearable = 0;
        for (int i = 0; i < kNFaults; ++i)
            if (!kFaults[i].fatal) clearable |= kFaults[i].bit;
        gradeSetEnabled(g_bFaultClr.id, (g_snap.faultBits & clearable) != 0);

        setStatic(g_bMute.lbl, g_bMute.subBuf, sizeof(g_bMute.subBuf),
                  g_intent.mute ? "UNMUTE" : "MUTE");

#if OPENDUBS_TOUCH_DEBUG
        {
            const uint32_t mAge = (uint32_t)(nowMs - g_intent.mastMs);
            char d[200];
            // Integer formatting: the mast intent is only ever -1, 0 or +1 from a jog, and a
            // %f here would pull float printf into a 4 Hz path for nothing.
            std::snprintf(d, sizeof(d),
                          "dbg intent: mast %+d  ·  %ums old (%s)  ·  %u republishes  ·  req %u seq %u",
                          (int)(g_intent.mast > 0.5f ? 1 : g_intent.mast < -0.5f ? -1 : 0),
                          (unsigned)mAge,
                          mAge > MB_MAST_EXPIRY_MS ? "EXPIRED = STOP" : "LIVE",
                          (unsigned)g_mastPublishes,
                          (unsigned)g_intent.req, (unsigned)g_intent.reqSeq);
            setStatic(g_dbg1, g_dbg1Buf, sizeof(g_dbg1Buf), d);
            setStatic(g_dbg2, g_dbg2Buf, sizeof(g_dbg2Buf), dbgStimulusPhase());
        }
#endif
    }

    if (faultVisible) {
        int n = 0;
        const int f = firstActiveFault(g_snap.faultBits, &n);
        if (f >= 0) {
            setStatic(g_fHead, g_fHeadBuf, sizeof(g_fHeadBuf), kFaults[f].what);
            setStatic(g_fBody, g_fBodyBuf, sizeof(g_fBodyBuf), kFaults[f].clears);
        }
    }

    std::snprintf(g_status, sizeof(g_status), "touch: %s%s  ·  %s",
                  faultVisible ? "fault" : "tech", g_deliberate ? "+adv" : "", gradeStatus());
}

const char* uiControlsStatus() { return g_status; }

bool uiControlsTakeTechRequest()
{
    const bool v = g_wantTech;
    g_wantTech = false;
    return v;
}

void uiControlsSetRevertText(const char* s)
{
    if (!g_revert) return;
    setStatic(g_revert, g_revertBuf, sizeof(g_revertBuf), s);
}

#else   // ---- compiled out -------------------------------------------------------------------

void uiControlsBuild(lv_obj_t*) {}
void uiControlsShowTech(bool) {}
void uiControlsShowFault(bool) {}
void uiControlsTick(uint32_t) {}
bool uiControlsFaultPending(uint32_t) { return false; }
void uiControlsSetDeliberate(bool, uint32_t) {}
bool uiControlsDeliberate() { return false; }
uint32_t uiControlsDeliberateSince() { return 0; }
const char* uiControlsStatus() { return "touch: compiled out"; }
bool uiControlsTakeTechRequest() { return false; }
void uiControlsSetRevertText(const char*) {}

#endif
