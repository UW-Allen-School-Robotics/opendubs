#include "ui_touch.h"
#include "touch_ui_config.h"
#include "ui_controls.h"
#include "ui_grade.h"
#include "face_mailbox.h"
// A net STAND-IN, not part of ui/. It is driven from this timer for the same reason the
// architecture note drives the debug camera preview from a UI timer — the one task allowed to
// draw is also the only one with a clock we already tick — and it compiles to nothing unless
// OPENDUBS_TOUCH_DEBUG=1. When `net` lands, this include and its one call site go.
#include "dbg_stimulus.h"
#include <cstdio>
#include <cstring>

#if OPENDUBS_TOUCH_UI

namespace {

constexpr int kW = 1280, kH = 720;

// ---- the entry gesture -----------------------------------------------------------------------
// Between the eyes. app_face.cpp puts the eye slots at x 318 and 962 with a 480 px body, so the
// eyes occupy 78..558 and 722..1202 and the gap is 558..722. This sits inside it, 140 x 180 px
// (12 x 16 mm at the panel's 0.0865 mm/px) — big enough to hit deliberately with a thumb,
// nowhere near an edge a sleeve brushes.
constexpr int kGateX = 570, kGateY = 270, kGateW = 140, kGateH = 180;
constexpr uint32_t kGateHoldMs   = 1500;   // deliberate, matching grade 4's gesture length
constexpr uint32_t kGateRevealMs = 400;    // show progress only after this, so a stray touch
                                           // teaches nothing and a deliberate one teaches
                                           // everything

// ---- idle reverts ----------------------------------------------------------------------------
// Two timers, because the two modes have different audiences. The technician screen belongs to
// someone with a job in hand, so a minute of thinking time is reasonable. Deliberate mode
// enables the controls that GRANT energy and must not be left armed while its owner walks away,
// which is why grade 4's definition includes "an idle timeout back to the face" at all.
constexpr uint32_t kTechIdleMs  = 60000;
constexpr uint32_t kDelibIdleMs = 20000;

lv_obj_t* g_gate     = nullptr;
lv_obj_t* g_gateBar  = nullptr;
lv_obj_t* g_cross1   = nullptr;   // debug crosshair, two thin rects (no rotation, no layer)
lv_obj_t* g_cross2   = nullptr;

bool        g_gateHeld   = false;
uint32_t    g_gateStart  = 0;
int32_t     g_gateLastW  = -1;

PanelScreen g_screen     = PanelScreen::Face;
uint32_t    g_lastTouch  = 0;
bool        g_indevOk    = false;
const char* g_indevNote  = "searching";
char        g_status[224] = "touch: not built";
char        g_revertBuf[32];

inline bool due(uint32_t now, uint32_t at) { return (int32_t)(now - at) >= 0; }

// Is any pointer device pressed, and where? One pass over the indev list per tick; there are two
// at most (the BSP's touch, plus a USB HID mouse if one is plugged in — bsp_usb_host_start and
// hid_init are both called by the HAL, so that is a real possibility on a bench, and it is
// harmless: a mouse is a pointer and every control here is pointer-driven).
bool pointerState(lv_point_t* p)
{
    for (lv_indev_t* in = lv_indev_get_next(nullptr); in; in = lv_indev_get_next(in)) {
        if (lv_indev_get_type(in) != LV_INDEV_TYPE_POINTER) continue;
        if (lv_indev_get_state(in) != LV_INDEV_STATE_PRESSED) continue;
        if (p) lv_indev_get_point(in, p);
        return true;
    }
    return false;
}

// DISCOVERY, NOT CREATION. The BSP has already registered the indev (see the header). Creating
// a second one here would double-read the I2C touch controller and fight the first for the
// press/release edges — a bug that would present as "every other tap is ignored", which is
// exactly the kind of symptom this project's history says gets attributed to the hardware.
//
// So: find it, record what was found, and if nothing was found say so where a human will see it
// rather than failing silently. The fix for an absent indev is in `hal_esp32.cpp` (its own
// `lvTouchpad` block, currently commented out) or in the BSP's panel detection — both vendor
// files already on the README's modification list, and neither is this file's business to patch
// from inside an app.
void discoverIndev()
{
    int pointers = 0, others = 0;
    for (lv_indev_t* in = lv_indev_get_next(nullptr); in; in = lv_indev_get_next(in)) {
        if (lv_indev_get_type(in) == LV_INDEV_TYPE_POINTER) ++pointers;
        else ++others;
    }
    g_indevOk = pointers > 0;
    if (pointers == 1)      g_indevNote = "indev ok";
    else if (pointers > 1)  g_indevNote = "indev ok (several pointers)";
    else if (others > 0)    g_indevNote = "NO POINTER INDEV (non-pointer devices only)";
    else                    g_indevNote = "NO INDEV AT ALL — check bsp_display_start";
}

void enter(PanelScreen s, uint32_t nowMs)
{
    if (g_screen == s) return;
    // LEAVING A SCREEN STOPS EVERYTHING IT WAS ASKING FOR. "The screen went away" must mean
    // "the lift stopped", not "the lift is still being asked by a control nobody can see" — and
    // the fault screen can take the panel out from under a held jog at any moment.
    gradeAbortAll(nowMs);
    if (s != PanelScreen::Tech) uiControlsSetDeliberate(false, nowMs);

    g_screen = s;
    uiControlsShowTech(s == PanelScreen::Tech);
    uiControlsShowFault(s == PanelScreen::Fault);
    // The gate is never clickable — tickGate() hit-tests the indev directly and returns early
    // off the face — so there is no flag to flip here. Reset its gesture, though: a hold that
    // was in progress when a fault took the panel must not complete against a hidden face.
    g_gateHeld = false;
    if (g_gateBar && g_gateLastW != 0) { lv_obj_set_width(g_gateBar, 0); g_gateLastW = 0; }
    g_lastTouch = nowMs;
}

void tickGate(uint32_t nowMs)
{
    if (g_screen != PanelScreen::Face) return;

    lv_point_t p{};
    const bool pressed = pointerState(&p);
    const bool inside  = pressed && p.x >= kGateX && p.x < kGateX + kGateW
                                 && p.y >= kGateY && p.y < kGateY + kGateH;

    // POLLED, NOT EVENT-DRIVEN, and on purpose. The face's own objects are not clickable and the
    // gate must not become a visible target, so there is nothing to attach an event to that a
    // bystander could not also find. Polling the indev is two reads per tick and it means the
    // gesture is defined by a rectangle and a duration rather than by an object's hit box.
    if (!inside) {
        if (g_gateHeld) {
            g_gateHeld = false;
            if (g_gateBar) {
                lv_obj_add_flag(g_gateBar, LV_OBJ_FLAG_HIDDEN);
                if (g_gateLastW != 0) { lv_obj_set_width(g_gateBar, 0); g_gateLastW = 0; }
            }
        }
        return;
    }

    if (!g_gateHeld) { g_gateHeld = true; g_gateStart = nowMs; }
    const uint32_t el = (uint32_t)(nowMs - g_gateStart);

    if (g_gateBar && el >= kGateRevealMs) {
        lv_obj_remove_flag(g_gateBar, LV_OBJ_FLAG_HIDDEN);
        const uint32_t span = kGateHoldMs - kGateRevealMs;
        const uint32_t into = el - kGateRevealMs;
        int32_t w = (int32_t)((into < span ? into : span) * (uint32_t)kGateW / span);
        w = (w + 2) & ~3;                       // quantised: a resize is the expensive write
        if (w > kGateW) w = kGateW;
        if (w != g_gateLastW) { lv_obj_set_width(g_gateBar, w); g_gateLastW = w; }
    }

    if (el >= kGateHoldMs) {
        g_gateHeld = false;
        if (g_gateBar) {
            lv_obj_add_flag(g_gateBar, LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_width(g_gateBar, 0);
            g_gateLastW = 0;
        }
        enter(PanelScreen::Tech, nowMs);
    }
}

#if OPENDUBS_TOUCH_DEBUG
void tickCrosshair()
{
    lv_point_t p{};
    const bool pressed = pointerState(&p);
    if (!g_cross1 || !g_cross2) return;
    if (!pressed) {
        lv_obj_add_flag(g_cross1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(g_cross2, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    // THE ONE INSTRUMENT THAT SETTLES THE MAPPING. If the cross sits under the finger, the
    // rotation transform and the digitiser's handedness agree; if it mirrors, or if x and y are
    // swapped, that is a one-field fix in the BSP's esp_lcd_touch_config_t flags
    // (swap_xy / mirror_x / mirror_y) and not an investigation. Two thin rectangles rather than
    // lines or a rotated glyph: no layer, no mask, two cheap writes.
    lv_obj_remove_flag(g_cross1, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(g_cross2, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_pos(g_cross1, (int32_t)p.x - 40, (int32_t)p.y - 1);
    lv_obj_set_pos(g_cross2, (int32_t)p.x - 1,  (int32_t)p.y - 40);
}
#endif

}  // namespace

void touchUiBuild(lv_obj_t* parent)
{
    discoverIndev();

    // The gate region. Transparent, non-clickable, and it never receives an LVGL event: it
    // exists so the progress bar has a parent with the right geometry, and so that the region's
    // coordinates live in exactly one place. tickGate() does the hit testing against the indev.
    g_gate = lv_obj_create(parent);
    lv_obj_remove_style_all(g_gate);
    lv_obj_remove_flag(g_gate, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(g_gate, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(g_gate, kGateX, kGateY);
    lv_obj_set_size(g_gate, kGateW, kGateH);
    lv_obj_set_style_bg_opa(g_gate, LV_OPA_TRANSP, LV_PART_MAIN);

    g_gateBar = lv_obj_create(g_gate);
    lv_obj_remove_style_all(g_gateBar);
    lv_obj_remove_flag(g_gateBar, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(g_gateBar, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(g_gateBar, 0, kGateH - 8);
    lv_obj_set_size(g_gateBar, 0, 6);
    lv_obj_set_style_bg_opa(g_gateBar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(g_gateBar, lv_color_hex(0x38d6f0), LV_PART_MAIN);
    lv_obj_add_flag(g_gateBar, LV_OBJ_FLAG_HIDDEN);

#if OPENDUBS_TOUCH_DEBUG
    auto mkBar = [parent](int w, int h) {
        lv_obj_t* o = lv_obj_create(parent);
        lv_obj_remove_style_all(o);
        lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(o, w, h);
        lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_PART_MAIN);
        lv_obj_set_style_bg_color(o, lv_color_hex(0xff00ff), LV_PART_MAIN);
        lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
        return o;
    };
    g_cross1 = mkBar(80, 3);
    g_cross2 = mkBar(3, 80);
#endif

    uiControlsBuild(parent);
#if OPENDUBS_TOUCH_DEBUG
    lv_obj_move_foreground(g_cross1);
    lv_obj_move_foreground(g_cross2);
#endif
    g_screen = PanelScreen::Face;
    std::snprintf(g_status, sizeof(g_status), "touch: %s  ·  face", g_indevNote);
}

void touchUiTick(uint32_t nowMs)
{
    dbgStimulusTick(nowMs);          // no-op unless OPENDUBS_TOUCH_DEBUG=1

    lv_point_t p{};
    if (pointerState(&p)) g_lastTouch = nowMs;

    // PRIORITY, AS THE DASHBOARD NOTE ORDERS IT: fault over technician over face. A fault takes
    // the panel even out of a technician screen someone is working on, because the fault is new
    // information and the readouts are not.
    const bool faultWanted = uiControlsFaultPending(nowMs);

    if (faultWanted && g_screen != PanelScreen::Fault) {
        enter(PanelScreen::Fault, nowMs);
    } else if (!faultWanted && g_screen == PanelScreen::Fault) {
        enter(PanelScreen::Face, nowMs);
    }

    if (uiControlsTakeTechRequest()) enter(PanelScreen::Tech, nowMs);

    if (g_screen == PanelScreen::Tech) {
        // Deliberate mode leaves first, and leaving it does not leave the screen: the person is
        // still reading, they just no longer have the energising controls armed.
        if (uiControlsDeliberate()
            && (uint32_t)(nowMs - uiControlsDeliberateSince()) > kDelibIdleMs
            && !gradeAnyHeld()) {
            uiControlsSetDeliberate(false, nowMs);
        }
        const uint32_t idle = (uint32_t)(nowMs - g_lastTouch);
        // A HELD JOG IS NOT IDLE, and the touch poll above already covers that; the explicit
        // check is there because a jog held past the timeout is precisely when reverting would
        // be worst — it would stop the lift mid-move for a reason the operator cannot see.
        if (idle > kTechIdleMs && !gradeAnyHeld()) {
            enter(PanelScreen::Face, nowMs);
        } else {
            const uint32_t left = (kTechIdleMs - (idle < kTechIdleMs ? idle : kTechIdleMs)) / 1000;
            std::snprintf(g_revertBuf, sizeof(g_revertBuf), "face in %us", (unsigned)left);
            uiControlsSetRevertText(g_revertBuf);
        }
    }

    tickGate(nowMs);
#if OPENDUBS_TOUCH_DEBUG
    tickCrosshair();
#endif
    uiControlsTick(nowMs);

    std::snprintf(g_status, sizeof(g_status), "%s  ·  %s", g_indevNote, uiControlsStatus());
}

bool touchUiOwnsPanel() { return g_screen != PanelScreen::Face; }
PanelScreen touchUiScreen() { return g_screen; }
const char* touchUiStatus() { return g_status; }

#else   // ---- compiled out -------------------------------------------------------------------

void touchUiBuild(lv_obj_t*) {}
void touchUiTick(uint32_t) {}
bool touchUiOwnsPanel() { return false; }
PanelScreen touchUiScreen() { return PanelScreen::Face; }
const char* touchUiStatus() { return "touch: compiled out"; }

#endif
