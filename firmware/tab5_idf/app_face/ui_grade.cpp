#include "ui_grade.h"
#include "touch_ui_config.h"
#include <cstdio>

#if OPENDUBS_TOUCH_UI

namespace {

// HEADROOM, AND OVERFLOW THAT IS NOT SILENT. AUDIT.md invariant 7 is a scar from the cobweb
// point pools, which were sized at exactly their demand and simply stopped drawing when full —
// a third web would have vanished with no error anywhere. Nine controls are registered today;
// the pool is 24 and a failed gradeAdd() shows up in gradeStatus(), which is on the panel.
constexpr int kMaxControls = 24;

struct Ctl {
    lv_obj_t*    obj      = nullptr;
    lv_obj_t*    progress = nullptr;
    GradeFireFn  fire     = nullptr;
    GradeHoldFn  hold     = nullptr;
    int32_t      arg      = 0;
    int32_t      fullW    = 0;
    Grade        grade    = Grade::Tap;
    bool         enabled  = true;
    bool         pressed  = false;     // the gesture is live
    uint32_t     pressMs  = 0;         // when it began (grades 3/4)
    int32_t      lastBarW = -1;        // change-guard, AUDIT.md invariant 8
    bool         lastDim  = false;     // change-guard on the enabled styling
};

Ctl  g_ctl[kMaxControls];
int  g_n = 0;
bool g_overflow = false;
char g_status[96] = "touch: controls not built";

// Palette. Deliberately NOT the e-stop's red for anything here: the dashboard note's sharpest
// correction is that "a thing that looks like the e-stop and is not" is the hazard, so the soft
// stop gets amber-on-slate and the mushroom head keeps red to itself.
constexpr uint32_t kBg       = 0x1a2430;
constexpr uint32_t kBgArmed  = 0x24455c;   // gesture in progress
constexpr uint32_t kBgHeld   = 0x1d6b7a;   // grade 2, actively asking
constexpr uint32_t kBgDim    = 0x141a21;   // disabled
constexpr uint32_t kBar      = 0x38d6f0;

uint32_t holdMsFor(Grade g)
{
    return (g == Grade::Deliberate) ? GRADE4_HOLD_MS : GRADE3_HOLD_MS;
}

void setBodyColour(Ctl& c, uint32_t rgb)
{
    // bg_color, NOT opa_layered. In LVGL 9.2 `calculate_layer_type()` (core/lv_obj_style.c)
    // induces a layer on transform_*, bitmap_mask_src, blend_mode and **opa_layered** — plain
    // `bg_opa` and `bg_color` are free. So "dim the disabled button" is a colour change here and
    // never an opacity one, which is the difference between one rectangle and a per-draw
    // bounding-box allocation.
    lv_obj_set_style_bg_color(c.obj, lv_color_hex(rgb), LV_PART_MAIN);
}

void resetBar(Ctl& c)
{
    if (!c.progress) return;
    if (c.lastBarW != 0) {
        lv_obj_set_width(c.progress, 0);
        c.lastBarW = 0;
    }
}

// The single place a grade-2 control stops. Reached from the release event, from the tick's
// independent re-check, and from gradeAbortAll() — three paths, one publish, so "stopped" cannot
// mean three slightly different things.
void endHold(Ctl& c, uint32_t nowMs)
{
    if (!c.pressed) return;
    c.pressed = false;
    if (c.grade == Grade::HoldToAct && c.hold) c.hold(c.arg, false, nowMs);
    resetBar(c);
    setBodyColour(c, c.enabled ? kBg : kBgDim);
}

void abortConfirm(Ctl& c)
{
    c.pressed = false;
    resetBar(c);
    setBodyColour(c, c.enabled ? kBg : kBgDim);
}

// Is the finger genuinely still on this control? Asked every tick rather than inferred from the
// last event, for the reason in the header: a grade-2 stop that depends on one event arriving is
// a lift that keeps moving when that event does not.
bool stillHeld(const Ctl& c)
{
    lv_area_t a;
    lv_obj_get_coords(c.obj, &a);
    for (lv_indev_t* in = lv_indev_get_next(nullptr); in; in = lv_indev_get_next(in)) {
        if (lv_indev_get_type(in) != LV_INDEV_TYPE_POINTER) continue;
        if (lv_indev_get_state(in) != LV_INDEV_STATE_PRESSED) continue;
        lv_point_t p;
        lv_indev_get_point(in, &p);
        if (p.x >= a.x1 && p.x <= a.x2 && p.y >= a.y1 && p.y <= a.y2) return true;
    }
    return false;
}

void onEvent(lv_event_t* e)
{
    // RUNS INSIDE lv_timer_handler(), THEREFORE INSIDE THE LVGL LOCK. No lvglLock() here, by
    // design — see the call-graph table in AUDIT.md.
    const int id = (int)(intptr_t)lv_event_get_user_data(e) - 1;
    if (id < 0 || id >= g_n) return;
    Ctl& c = g_ctl[id];
    const lv_event_code_t code = lv_event_get_code(e);
    const uint32_t now = lv_tick_get();

    switch (code) {
        case LV_EVENT_PRESSED:
            if (!c.enabled) return;
            if (c.grade == Grade::Tap) {          // visual only; Tap fires on CLICKED
                setBodyColour(c, kBgArmed);
                return;
            }
            c.pressed = true;
            c.pressMs = now;
            setBodyColour(c, c.grade == Grade::HoldToAct ? kBgHeld : kBgArmed);
            // GRADE 2 ASSERTS IMMEDIATELY, on the press edge rather than on the first tick.
            // Up to 33 ms of nothing-happens after a deliberate press reads as a dead button,
            // and the expiry makes an early assertion free: if the finger lifts before the next
            // tick, the stop follows within one tick anyway.
            if (c.grade == Grade::HoldToAct && c.hold) c.hold(c.arg, true, now);
            return;

        case LV_EVENT_CLICKED:
            if (c.grade == Grade::Tap && c.enabled && c.fire) c.fire(c.arg);
            setBodyColour(c, c.enabled ? kBg : kBgDim);
            return;

        case LV_EVENT_RELEASED:
        case LV_EVENT_PRESS_LOST:
            if (c.grade == Grade::HoldToAct)      endHold(c, now);
            else if (c.grade == Grade::Tap)       setBodyColour(c, c.enabled ? kBg : kBgDim);
            else                                  abortConfirm(c);   // short of the hold = no
            return;

        default:
            return;
    }
}

}  // namespace

int gradeAdd(lv_obj_t* target, Grade grade, int32_t arg, GradeFireFn fire, GradeHoldFn hold,
             lv_obj_t* progress, int32_t progressFullW)
{
    if (g_n >= kMaxControls) { g_overflow = true; return -1; }
    const int id = g_n++;
    Ctl& c = g_ctl[id];
    c.obj = target; c.progress = progress; c.fire = fire; c.hold = hold;
    c.arg = arg; c.fullW = progressFullW; c.grade = grade;
    c.enabled = true; c.pressed = false; c.lastBarW = -1;

    lv_obj_add_flag(target, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(target, LV_OBJ_FLAG_SCROLLABLE);
    // The whole event set, one callback. LV_EVENT_ALL costs one descriptor per object at build
    // time and nothing per frame; filtering would mean four descriptors to achieve the same.
    lv_obj_add_event_cb(target, onEvent, LV_EVENT_ALL, (void*)(intptr_t)(id + 1));
    setBodyColour(c, kBg);
    resetBar(c);
    return id;
}

void gradeSetEnabled(int id, bool enabled)
{
    if (id < 0 || id >= g_n) return;
    Ctl& c = g_ctl[id];
    if (c.enabled == enabled) return;
    c.enabled = enabled;
    // A PRECONDITION CAN EVAPORATE MID-GESTURE. CH5 is a physical switch and can go to OFF while
    // a finger is down; completing the hold then would fire a request the robot is about to
    // refuse, and the operator would read the refusal as the panel being broken. So disabling
    // aborts in flight.
    if (!enabled && c.pressed) {
        if (c.grade == Grade::HoldToAct) endHold(c, lv_tick_get());
        else                             abortConfirm(c);
    }
    if (!c.pressed) setBodyColour(c, enabled ? kBg : kBgDim);
    c.lastDim = !enabled;
}

void gradeTick(uint32_t nowMs)
{
    int held = -1;
    for (int i = 0; i < g_n; ++i) {
        Ctl& c = g_ctl[i];
        if (!c.pressed) continue;

        // THE INDEPENDENT RE-CHECK. Not a second opinion for its own sake: it is the only thing
        // that covers a release event that never arrives, a touch controller that stops
        // reporting, and a finger that slid off without LVGL noticing.
        if (!stillHeld(c)) {
            if (c.grade == Grade::HoldToAct) endHold(c, nowMs);
            else                             abortConfirm(c);
            continue;
        }

        if (c.grade == Grade::HoldToAct) {
            held = i;
            // Republish on the heartbeat, not every tick: the UI ticks at 33 ms and the
            // expiry is 250 ms, so 50 ms keeps three in flight inside one expiry window.
            if ((uint32_t)(nowMs - c.pressMs) >= GRADE2_REPUBLISH_MS) {
                c.pressMs = nowMs;
                if (c.hold) c.hold(c.arg, true, nowMs);
            }
            continue;
        }

        // Grades 3 and 4: a sustained gesture with visible progress.
        const uint32_t need = holdMsFor(c.grade);
        const uint32_t el   = (uint32_t)(nowMs - c.pressMs);
        if (c.progress) {
            int32_t w = (int32_t)((el < need ? el : need) * (uint32_t)c.fullW / need);
            w = (w + 4) & ~7;                     // quantised to 8 px, as the eye body is: a
            if (w > c.fullW) w = c.fullW;         // resize is the expensive write, and the
            if (w != c.lastBarW) {                // intermediate positions are not information
                lv_obj_set_width(c.progress, w);
                c.lastBarW = w;
            }
        }
        if (el >= need) {
            c.pressed = false;
            if (c.fire) c.fire(c.arg);
            resetBar(c);
            setBodyColour(c, c.enabled ? kBg : kBgDim);
            held = i;
        }
    }

    snprintf(g_status, sizeof(g_status), "touch: %d/%d ctl%s%s", g_n, kMaxControls,
             g_overflow ? " POOL OVERFLOW" : "",
             held >= 0 ? " held" : "");
}

void gradeAbortAll(uint32_t nowMs)
{
    for (int i = 0; i < g_n; ++i) {
        Ctl& c = g_ctl[i];
        if (!c.pressed) continue;
        if (c.grade == Grade::HoldToAct) endHold(c, nowMs);
        else                             abortConfirm(c);
    }
}

bool gradeAnyHeld()
{
    for (int i = 0; i < g_n; ++i)
        if (g_ctl[i].pressed && g_ctl[i].grade == Grade::HoldToAct) return true;
    return false;
}

const char* gradeStatus() { return g_status; }

#else   // ---- compiled out -------------------------------------------------------------------
// The face_probe.cpp pattern: the same API, bodies empty, so every call site in ui_controls.cpp
// compiles unchanged and the linker drops the lot. "Absent" is then a build property rather
// than a habit (architecture note §7.2).

int  gradeAdd(lv_obj_t*, Grade, int32_t, GradeFireFn, GradeHoldFn, lv_obj_t*, int32_t) { return -1; }
void gradeSetEnabled(int, bool) {}
void gradeTick(uint32_t) {}
void gradeAbortAll(uint32_t) {}
bool gradeAnyHeld() { return false; }
const char* gradeStatus() { return "touch: compiled out"; }

#endif
