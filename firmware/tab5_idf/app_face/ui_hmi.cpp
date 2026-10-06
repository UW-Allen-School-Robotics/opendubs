#include "ui_hmi.h"
#include "ui_settings.h"
#include "touch_ui_config.h"
#include "pantilt.h"
#include "net.h"

#include <lvgl.h>
#include <mooncake_log.h>
#include <cstdio>
#include <cstring>

#ifndef OPENDUBS_TOUCH_UI
#define OPENDUBS_TOUCH_UI 1
#endif

#if OPENDUBS_TOUCH_UI

namespace {

constexpr float kDpadDpsUi = 45.0f;   // the Arduino's kDpadDps

constexpr const char* kTag = "hmi";

// THE OUTBOUND LINK DOES NOT EXIST YET. See ui_hmi.h deviation 2. Flip this when a command
// transport to the StamPLC lands, and the controls arm themselves.
// THE COMMAND LINK EXISTS NOW. RAISE/LOWER go out as `MAST CMD <v>` and the e-stop as `ESTOP`,
// both to the StamPLC on udp/5005 -- see net.cpp. What is still runtime-conditional is whether we
// have HEARD the StamPLC, since its address is learned rather than configured; netCommandLink()
// answers that, and the banner below reports it honestly instead of claiming either way.
constexpr bool kCommandLink = true;

// Idle auto-revert. The Arduino HMI did this too (HmiCtx::revertSecs): a controller screen left
// up on a robot's face is a face that has stopped being a face, and nobody remembers to close it.
constexpr uint32_t kRevertMs = 30000;

// ---- palette, lifted from hmi.cpp so the two builds look like the same product ----------------
constexpr uint32_t GROUND  = 0x0a0e13;
constexpr uint32_t PANEL   = 0x141b22;
constexpr uint32_t PANEL2  = 0x0f151b;
constexpr uint32_t HAIR    = 0x26313b;
constexpr uint32_t INK     = 0xe8eef2;
constexpr uint32_t INKDIM  = 0x8b9aa6;
constexpr uint32_t INKFT   = 0x788896;
constexpr uint32_t CYAN    = 0x38d6f0;
constexpr uint32_t UPCOL   = 0x5ed98a;
constexpr uint32_t DNCOL   = 0xf0b23a;
constexpr uint32_t REDMID  = 0xb31d1d;
constexpr uint32_t REDDEEP = 0x4a0f10;
constexpr uint32_t AMBER   = 0xffb24d;

// ---- font roles. hmi.cpp: "nothing meaningful below ~12pt -- the panel is ~24in away at knee
// height". Mapped onto the Montserrat sizes lv_conf.h enables.
#define F_LABEL  &lv_font_montserrat_16
#define F_DATA   &lv_font_montserrat_22
#define F_WORD   &lv_font_montserrat_32
// 44, not 48: lv_font_montserrat_48 does not resolve in this build even though our lv_conf.h
// sets LV_FONT_MONTSERRAT_48 -- so some other lv_conf.h is shadowing ours on the include path.
// Worth chasing separately; it does not block this screen, and 44 is the next size down.
#define F_BIG    &lv_font_montserrat_44
#define F_ESTOP  &lv_font_montserrat_44

// ---- layout (1280x720), verbatim from hmi.cpp -------------------------------------------------
constexpr int TOPBAR = 64;
constexpr int CHIP[4]  = {  24,  88, 300, 170 };
constexpr int TAB5[4]  = {  24, 270, 300, 214 };
constexpr int IOP[4]   = {  24, 498, 300, 190 };
constexpr int UPB[4]   = { 348,  88, 380, 190 };
constexpr int RDO[4]   = { 348, 292, 380, 150 };
constexpr int DNB[4]   = { 348, 456, 380, 190 };
constexpr int ES_CX = 1090, ES_CY = 372, ES_R = 150;
constexpr int DP_CX = 834,  DP_CY = 372, DP_W = 66, DP_G = 36;
constexpr int IO_ROW0 = 556, IO_DY = 42;

lv_obj_t* g_root = nullptr;
bool      g_open = false;
uint32_t  g_lastTouch = 0;

// THE VEIL: a full-screen TRANSPARENT CLICKABLE CHILD, which is the reveal mechanism with actual
// evidence behind it. The ledger of six flashes is unambiguous -- every image where touch worked
// had a clickable CHILD object as its target (the settings panel's 160x160 corner Container), and
// the only two that failed were the two that removed it and attached the handler to the SCREEN
// object instead. A child is also what the demo's own fifteen panels use. So: a child, sized to
// the whole screen, which is "touch anywhere" without relying on screen-level dispatch at all.
lv_obj_t* g_veil = nullptr;

// Counters, incremented ONLY inside event callbacks. This is deliberate: the per-frame indev
// probe earlier today correlated with touch breaking and I could never account for why, so this
// instrument adds NOTHING to the render loop and takes no lock of its own. Read over UDP.
uint32_t g_veilPresses = 0;
uint32_t g_scrPresses  = 0;
bool     g_built       = false;

// dynamic widgets
lv_obj_t* g_chipSwatch[4] = {nullptr};
lv_obj_t* g_chipLabel  = nullptr;
lv_obj_t* g_chipDesc   = nullptr;
lv_obj_t* g_linkVal    = nullptr;
lv_obj_t* g_ipVal      = nullptr;
lv_obj_t* g_tofVal     = nullptr;
lv_obj_t* g_height     = nullptr;
lv_obj_t* g_revert     = nullptr;
lv_obj_t* g_lamp[4]    = {nullptr};

// ---- primitives -------------------------------------------------------------------------------
lv_obj_t* box(lv_obj_t* parent, const int r[4], uint32_t bg, int radius = 12)
{
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, (lv_obj_flag_t)(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
    lv_obj_set_pos(o, r[0], r[1]);
    lv_obj_set_size(o, r[2], r[3]);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(o, lv_color_hex(bg), 0);
    return o;
}

lv_obj_t* text(lv_obj_t* parent, const char* s, const lv_font_t* f, uint32_t colour, int x, int y)
{
    lv_obj_t* l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, f, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(colour), 0);
    lv_label_set_text(l, s);
    lv_obj_set_pos(l, x, y);
    return l;
}

// A labelled section panel: the `panel()` helper from hmi.cpp.
lv_obj_t* panel(lv_obj_t* parent, const int r[4], const char* label)
{
    lv_obj_t* p = box(parent, r, PANEL);
    if (label) text(parent, label, F_LABEL, INKFT, r[0] + 16, r[1] + 12);
    return p;
}

// A command control. DISABLED while kCommandLink is false -- greyed, and no handler attached, so
// there is no path by which a press could be mistaken for a command. See ui_hmi.h deviation 2.
// A control that is LIVE regardless of kCommandLink, because it needs no outbound transport.
//
// The pan/tilt d-pad and MODE act on servos and state THIS BOARD OWNS -- the Arduino's d-pad set
// g_dpadPanDps/g_dpadTiltDps straight into its local integration. RAISE/LOWER and the soft e-stop
// are different: they are REQUESTS to the StamPLC, so they stay inert until that path exists.
lv_obj_t* liveBox(lv_obj_t* parent, const int r[4], const char* word, uint32_t accent, int radius)
{
    lv_obj_t* b = box(parent, r, 0x18202a, radius);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_border_color(b, lv_color_hex(accent), 0);
    lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
    if (word) {
        lv_obj_t* l = text(parent, word, F_WORD, accent, 0, 0);
        lv_obj_set_width(l, r[2]);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_pos(l, r[0], r[1] + r[3] / 2 - 20);
    }
    return b;
}

lv_obj_t* commandBox(lv_obj_t* parent, const int r[4], const char* word, uint32_t accent,
                     int radius = 14)
{
    lv_obj_t* b = box(parent, r, 0x18202a, radius);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_border_color(b, lv_color_hex(accent), 0);
    // THIS FLAG WAS MISSING, and it is why RAISE and LOWER did nothing. box() does not make an
    // object clickable, so neither button ever received a press: no command was sent and the
    // robot's arm gate was never reached. liveBox() and the e-stop each added it explicitly; this
    // one was written back when the plan was to attach no handler at all.
    lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
    if (word) {
        lv_obj_t* l = text(parent, word, F_WORD, accent, 0, 0);
        lv_obj_set_width(l, r[2]);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_pos(l, r[0], r[1] + r[3] / 2 - 20);
    }
    return b;
}

lv_obj_t* g_banner = nullptr;
lv_obj_t* g_upB = nullptr, *g_dnB = nullptr;
lv_obj_t* g_liftWhyTx = nullptr, *g_modeTx = nullptr;
lv_obj_t* g_bannerTx = nullptr;

void onFacePress(lv_event_t*);
void onVeilPress(lv_event_t*);

// HOLD-TO-MOVE WITHOUT TRUSTING LV_EVENT_PRESSING.
//
// The e-stop (LV_EVENT_PRESSED) worked while RAISE, LOWER and all four arrows (LV_EVENT_PRESSING)
// did nothing, which points at PRESSING never being delivered. The mechanism is not established:
// this panel's GT911 is read by a POLLED lv_timer, and whether the controller keeps yielding
// coordinates between interrupts -- which is what makes LVGL re-send PRESSING on the next read --
// is exactly the kind of thing that has been guessed wrong on this board before.
//
// So do not depend on it either way. PRESSED latches an intent with a short lease; hmiTick(), which
// runs every frame regardless of input, re-asserts it while the lease is alive; RELEASED and
// PRESS_LOST clear it. If PRESSING does fire, every one of them refreshes the lease and this is a
// true hold. If it never fires, each PRESSED still buys kHoldLeaseMs of motion, so the control
// responds instead of being dead. Either way the lease -- not an event -- is what stops the motion,
// which is the property that matters for a lift.
constexpr uint32_t kHoldLeaseMs = 400;

enum class Held : uint8_t { NONE, PAN_L, PAN_R, TILT_U, TILT_D, RAISE, LOWER };
Held     g_held   = Held::NONE;
uint32_t g_heldMs = 0;
// Event census, read back over PING: it settles which events actually arrive on this hardware
// rather than leaving it a theory.
uint32_t g_evPressed = 0, g_evPressing = 0, g_evReleased = 0;

struct DpadRate { float pan, tilt; };
DpadRate g_dpadL{-kDpadDpsUi, 0}, g_dpadR{kDpadDpsUi, 0},
         g_dpadU{0, kDpadDpsUi},  g_dpadD{0, -kDpadDpsUi};

void holdSet(Held h)
{
    g_held   = h;
    g_heldMs = 0;      // 0 = "stamp me on the next tick", as g_lastTouch does
}
void holdClear()
{
    g_held = Held::NONE;
    pantiltTouchRate(0.0f, 0.0f);
    netLiftRequest(0.0f);
}

void onHoldPress(lv_event_t* e)
{
    const lv_event_code_t c = lv_event_get_code(e);
    if (c == LV_EVENT_PRESSED) g_evPressed++; else g_evPressing++;
    holdSet((Held)(uintptr_t)lv_event_get_user_data(e));
}
void onHoldRelease(lv_event_t*) { g_evReleased++; holdClear(); }

void onModeToggle(lv_event_t*) { pantiltSetTracking(!pantiltTracking()); }

// RAISE / LOWER. HOLD-TO-MOVE, exactly as on the Arduino: PRESSING refreshes the request every
// frame, so letting go stops the lift even if the RELEASED event is missed, and so does the head
// dying -- the StamPLC's own 250 ms heartbeat window closes.
constexpr float kLiftUp = 1.0f, kLiftDown = -1.0f;   // the StamPLC's convention: positive is UP

// STOPPING IS NEVER CONFIRMED. A stop control that asks "are you sure?" is not a stop control.
// Clearing is NOT here: only the dashboard's guarded re-arm can un-stop the robot.
void onEstopPress(lv_event_t*)    { netEstopRequest(); }
void onClose(lv_event_t*);
void onSettings(lv_event_t*);

void setOpen(bool open)
{
    g_open = open;
    // OPENING IS ACTIVITY. hmiTick() early-returns while the panel is closed, so g_lastTouch keeps
    // the timestamp from the previous session -- and `nowMs - g_lastTouch` is then already past
    // kRevertMs, which closed the panel on its very first tick. The first open after a boot worked
    // (g_lastTouch == 0 seeds it) and every one after that flashed open and shut, which is exactly
    // what it looked like from the front: controls covered by the face before they could be hit.
    if (open) g_lastTouch = 0;
    if (!g_root) return;
    if (open) {
        lv_obj_remove_flag(g_root, LV_OBJ_FLAG_HIDDEN);
        // Raised ON OPEN, not once at build: cameraStep() calls lv_obj_move_foreground() on the
        // camera preview long after this is built, so a screen raised only at build time ends up
        // underneath it.
        lv_obj_move_foreground(g_root);
    } else {
        lv_obj_add_flag(g_root, LV_OBJ_FLAG_HIDDEN);
        // Re-raise the veil on close. cameraStep() calls lv_obj_move_foreground() on the camera
        // preview long after build, so anything that must stay on top has to be re-raised rather
        // than raised once.
        if (g_veil) lv_obj_move_foreground(g_veil);
    }
}

}  // namespace

bool hmiOwnsPanel() { return g_open; }

// Reported in the UDP PING reply, so reading the diagnosis does not depend on the panel being
// diagnosed -- and so nobody has to sit at the bench pressing the glass and describing it.
const char* hmiDiag()
{
    static char buf[128];
    snprintf(buf, sizeof(buf),
             "built=%d veil=%lu open=%d | ctl pressed=%lu pressing=%lu released=%lu held=%d",
             g_built ? 1 : 0, (unsigned long)g_veilPresses, g_open ? 1 : 0,
             (unsigned long)g_evPressed, (unsigned long)g_evPressing,
             (unsigned long)g_evReleased, (int)g_held);
    return buf;
}

namespace {

// The screen-level handler stays, but ONLY as a comparator: it counts and does not act. If
// g_scrPresses rises while g_veilPresses does not, screen dispatch works and the veil is being
// missed; if the reverse, the veil works and screen dispatch does not. Either way the next step
// is decided by a number rather than a theory.
void onFacePress(lv_event_t*)
{
    g_scrPresses++;
}

void onVeilPress(lv_event_t*)
{
    g_veilPresses++;
    if (!g_open && !settingsOwnsPanel()) setOpen(true);
}

void onClose(lv_event_t*) { setOpen(false); }

void onSettings(lv_event_t*)
{
    setOpen(false);
    settingsOpen();
}

// Any press inside the HMI is activity: it defers the auto-revert.
void onAnyHmiPress(lv_event_t*) { g_lastTouch = 0; }   // 0 = "reset on next tick"

// Make a control's press count as activity. Needed per-object: LVGL 9 delivers PRESSED to exactly
// ONE object and it does not bubble without LV_OBJ_FLAG_EVENT_BUBBLE, so the handler on g_root saw
// presses on bare background and nothing else -- working a button for 30 s reverted under the
// operator's finger.
void deferRevert(lv_obj_t* o)
{
    lv_obj_add_event_cb(o, onAnyHmiPress, LV_EVENT_PRESSED,  nullptr);
    lv_obj_add_event_cb(o, onAnyHmiPress, LV_EVENT_PRESSING, nullptr);
}

void attachHold(lv_obj_t* o, Held h)
{
    void* u = (void*)(uintptr_t)h;
    lv_obj_add_event_cb(o, onHoldPress,   LV_EVENT_PRESSED,    u);
    lv_obj_add_event_cb(o, onHoldPress,   LV_EVENT_PRESSING,   u);
    lv_obj_add_event_cb(o, onHoldRelease, LV_EVENT_RELEASED,   nullptr);
    lv_obj_add_event_cb(o, onHoldRelease, LV_EVENT_PRESS_LOST, nullptr);
}

}  // namespace

void hmiBuild()
{
    lv_obj_t* scr = lv_screen_active();

    // TOUCH ANYWHERE REVEALS THE CONTROLLER -- the Arduino interaction model, and it is strictly
    // better than the corner target it replaces. That target was a 160x160 invisible rectangle:
    // Emma could not see its affordance dots at all ("covered by the eye"), and an operator who
    // does not already know a control is there cannot find it. A whole-screen reveal needs no
    // affordance, so there is nothing to hide behind the eye and nothing to discover.
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);

    // SCROLLABLE OFF, AND THAT IS NOT TIDYING. LVGL's indev_proc_release only emits CLICKED when
    // the press did not become a scroll:
    //
    //     if(scroll_obj == NULL) { ... send_event(LV_EVENT_CLICKED, ...) }
    //     else                   { send_event(scroll_obj, LV_EVENT_SCROLL_THROW_BEGIN, ...) }
    //
    // The screen is scrollable by default, a finger drifts past the 10 px scroll limit without
    // trying, and the reveal then never fired -- which looked exactly like broken touch. It also
    // means an operator could DRAG THE FACE off centre, which is a bug in its own right.
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    // PRESSED, not CLICKED. A reveal should happen the instant the glass is touched, and PRESSED
    // cannot be swallowed by a scroll the way CLICKED can. This is also the event the raw-touch
    // crosshair used, which is the one screen-level handler already proven to work on this panel.
    lv_obj_add_event_cb(scr, onFacePress, LV_EVENT_PRESSED, nullptr);

    g_root = lv_obj_create(scr);
    lv_obj_remove_style_all(g_root);
    lv_obj_set_size(g_root, LV_PCT(100), LV_PCT(100));
    lv_obj_remove_flag(g_root, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(g_root, LV_OBJ_FLAG_CLICKABLE);     // swallows presses meant for the face
    lv_obj_set_style_bg_opa(g_root, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(g_root, lv_color_hex(GROUND), 0);
    lv_obj_add_flag(g_root, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(g_root, onAnyHmiPress, LV_EVENT_PRESSED, nullptr);

    // ---- chrome: "OpenDubs . Lift Module" ----
    text(g_root, "OpenDubs", F_DATA, INK, 24, TOPBAR / 2 - 14);
    // "-", not U+00B7 MIDDLE DOT: the Montserrat subset in this build has no glyph for it, so
    // it rendered as the missing-glyph box. Any non-ASCII separator has the same problem.
    text(g_root, "-", F_DATA, CYAN, 150, TOPBAR / 2 - 14);
    text(g_root, "Lift Module", F_DATA, INK, 172, TOPBAR / 2 - 14);
    lv_obj_t* hair = lv_obj_create(g_root);
    lv_obj_remove_style_all(hair);
    lv_obj_set_pos(hair, 0, TOPBAR);
    lv_obj_set_size(hair, 1280, 1);
    lv_obj_set_style_bg_opa(hair, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(hair, lv_color_hex(HAIR), 0);

    // ---- ROBOT STATE chip ----
    panel(g_root, CHIP, "ROBOT STATE");
    for (int i = 0; i < 4; ++i) {
        const int r[4] = {CHIP[0] + 16 + i * 50, CHIP[1] + 44, 42, 54};
        g_chipSwatch[i] = box(g_root, r, PANEL2, 13);
    }
    g_chipLabel = text(g_root, "--", F_WORD, INK, CHIP[0] + 16, CHIP[1] + CHIP[3] - 66);
    g_chipDesc  = text(g_root, "no link", F_LABEL, INKDIM, CHIP[0] + 16, CHIP[1] + CHIP[3] - 28);

    // ---- Tab5 identity ----
    panel(g_root, TAB5, nullptr);
    text(g_root, "MODULE",    F_LABEL, INKFT, TAB5[0] + 16, TAB5[1] + 46);
    text(g_root, "LINK",      F_LABEL, INKFT, TAB5[0] + 16, TAB5[1] + 80);
    text(g_root, "IP",        F_LABEL, INKFT, TAB5[0] + 16, TAB5[1] + 114);
    text(g_root, "FRONT ToF", F_LABEL, INKFT, TAB5[0] + 16, TAB5[1] + 148);
    text(g_root, "Z-AXIS LIFT", F_LABEL, INK, TAB5[0] + 150, TAB5[1] + 46);
    g_linkVal = text(g_root, "OFFLINE", F_LABEL, DNCOL, TAB5[0] + 150, TAB5[1] + 80);
    g_ipVal   = text(g_root, "--",      F_LABEL, INK,   TAB5[0] + 150, TAB5[1] + 114);
    g_tofVal  = text(g_root, "no sensor", F_LABEL, INKFT, TAB5[0] + 150, TAB5[1] + 148);

    // ---- relays & limits ----
    panel(g_root, IOP, "RELAYS & LIMITS");
    static const char* kIoNames[4] = {"UP RELAY", "DOWN RELAY", "TOP LIMIT", "BOT LIMIT"};
    for (int i = 0; i < 4; ++i) {
        const int d[4] = {IOP[0] + 20, IO_ROW0 + i * IO_DY - 10, 20, 20};
        g_lamp[i] = box(g_root, d, PANEL2, 10);
        lv_obj_set_style_border_width(g_lamp[i], 2, 0);
        lv_obj_set_style_border_color(g_lamp[i], lv_color_hex(HAIR), 0);
        text(g_root, kIoNames[i], F_LABEL, INKDIM, IOP[0] + 52, IO_ROW0 + i * IO_DY - 11);
    }

    // ---- RAISE / readout / LOWER ----
    lv_obj_t* upB = g_upB = commandBox(g_root, UPB, "RAISE", UPCOL);
    attachHold(upB, Held::RAISE);
    deferRevert(upB);
    panel(g_root, RDO, "HEIGHT");
    g_height = text(g_root, "---", F_BIG, INKFT, RDO[0] + 24, RDO[1] + 56);
    text(g_root, "mm", F_DATA, INKFT, RDO[0] + 180, RDO[1] + 80);
    // Why the lift will not move, in the operator's terms, right where they are looking.
    g_liftWhyTx = text(g_root, "", F_LABEL, AMBER, 0, 0);
    lv_obj_set_width(g_liftWhyTx, RDO[2]);
    lv_obj_set_style_text_align(g_liftWhyTx, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(g_liftWhyTx, RDO[0], RDO[1] + RDO[3] - 34);
    lv_obj_t* dnB = g_dnB = commandBox(g_root, DNB, "LOWER", DNCOL);
    attachHold(dnB, Held::LOWER);
    deferRevert(dnB);

    // ---- pan / tilt d-pad + mode ----
    const int panL[4]  = {DP_CX - DP_W - DP_G, DP_CY - DP_W / 2,     DP_W, DP_W};
    const int panR[4]  = {DP_CX + DP_G,        DP_CY - DP_W / 2,     DP_W, DP_W};
    const int tiltU[4] = {DP_CX - DP_W / 2,    DP_CY - DP_W - DP_G,  DP_W, DP_W};
    const int tiltD[4] = {DP_CX - DP_W / 2,    DP_CY + DP_G,         DP_W, DP_W};
    const int mode[4]  = {DP_CX - 75,          DP_CY + DP_W + DP_G + 20, 150, 44};
    // THE D-PAD AND MODE ARE LIVE. They drive servos and state this board owns, so they need no
    // command link -- which is why they work now while RAISE/LOWER and the e-stop do not.
    struct { const int* r; const char* sym; Held h; } dp[] = {
        {tiltU, LV_SYMBOL_UP,    Held::TILT_U}, {tiltD, LV_SYMBOL_DOWN,  Held::TILT_D},
        {panL,  LV_SYMBOL_LEFT,  Held::PAN_L},  {panR,  LV_SYMBOL_RIGHT, Held::PAN_R},
    };
    for (auto& d : dp) {
        lv_obj_t* b = liveBox(g_root, d.r, d.sym, CYAN, 12);
        attachHold(b, d.h);
        deferRevert(b);
    }
    lv_obj_t* modeBtn = liveBox(g_root, mode, nullptr, CYAN, 10);
    g_modeTx = text(g_root, "TRACKING: AI", F_LABEL, CYAN, 0, 0);
    lv_obj_set_width(g_modeTx, mode[2]);
    lv_obj_set_style_text_align(g_modeTx, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(g_modeTx, mode[0], mode[1] + mode[3] / 2 - 10);
    lv_obj_add_event_cb(modeBtn, onModeToggle, LV_EVENT_CLICKED, nullptr);
    deferRevert(modeBtn);
    text(g_root, "PAN / TILT", F_LABEL, INKFT, DP_CX - 54, DP_CY + DP_W + DP_G + 32);

    // ---- soft e-stop: concentric rings (see ui_hmi.h deviation 1) ----
    const int o1[4] = {ES_CX - ES_R,      ES_CY - ES_R,      ES_R * 2,      ES_R * 2};
    const int o2[4] = {ES_CX - ES_R + 8,  ES_CY - ES_R + 8,  ES_R * 2 - 16, ES_R * 2 - 16};
    const int o3[4] = {ES_CX - ES_R + 16, ES_CY - ES_R + 16, ES_R * 2 - 32, ES_R * 2 - 32};
    box(g_root, o1, kCommandLink ? REDDEEP : 0x241215, ES_R);
    box(g_root, o2, kCommandLink ? REDMID  : 0x3a1a1c, ES_R);
    lv_obj_t* esBtn = box(g_root, o3, kCommandLink ? 0x961616 : 0x472023, ES_R);
    lv_obj_add_flag(esBtn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(esBtn, onEstopPress, LV_EVENT_PRESSED, nullptr);
    deferRevert(esBtn);
    lv_obj_t* esl = text(g_root, kCommandLink ? "E-STOP" : "E-STOP", F_ESTOP,
                         kCommandLink ? 0xffebeb : 0x8a6a6a, 0, 0);
    lv_obj_set_width(esl, ES_R * 2);
    lv_obj_set_style_text_align(esl, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(esl, ES_CX - ES_R, ES_CY - 42);

    lv_obj_t* esh = text(g_root, kCommandLink ? "tap to STOP" : "no command link",
                         F_DATA, kCommandLink ? 0xffd4d4 : INKFT, 0, 0);
    lv_obj_set_width(esh, ES_R * 2);
    lv_obj_set_style_text_align(esh, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(esh, ES_CX - ES_R, ES_CY + 20);

    // The caption that must survive the port unchanged: it tells the operator what this does NOT
    // do, which is the only thing about a soft e-stop that matters.
    lv_obj_t* cap = text(g_root,
                         "Soft inhibit - drops the authorize-heartbeat.\n"
                         "The red mushrooms work even if this does not.",
                         F_LABEL, INKFT, 0, 0);
    lv_obj_set_width(cap, ES_R * 2 + 80);
    lv_obj_set_style_text_align(cap, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(cap, ES_CX - ES_R - 40, ES_CY + ES_R + 18);

    // ---- the no-command-link banner. Unmissable, because every control above is inert. ----
    {
        const int b[4] = {348, 20, 600, 32};
        g_banner = box(g_root, b, 0x2a1f0a, 8);
        lv_obj_set_style_border_width(g_banner, 1, 0);
        lv_obj_set_style_border_color(g_banner, lv_color_hex(AMBER), 0);
        g_bannerTx = text(g_root, "WAITING FOR THE STAMPLC - lift and stop have nowhere to go",
                          F_LABEL, AMBER, 0, 0);
        lv_obj_set_width(g_bannerTx, b[2]);
        lv_obj_set_style_text_align(g_bannerTx, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_pos(g_bannerTx, b[0], b[1] + 7);
    }

    // ---- close + settings, top right ----
    const int cl[4] = {1280 - 150, 14, 126, 40};
    lv_obj_t* closeBtn = box(g_root, cl, PANEL, 10);
    lv_obj_add_flag(closeBtn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(closeBtn, onClose, LV_EVENT_CLICKED, nullptr);
    lv_obj_t* cll = text(g_root, "CLOSE", F_LABEL, CYAN, 0, 0);
    lv_obj_set_width(cll, cl[2]);
    lv_obj_set_style_text_align(cll, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(cll, cl[0], cl[1] + 11);

    const int sb[4] = {1280 - 300, 14, 140, 40};
    lv_obj_t* setBtn = box(g_root, sb, PANEL, 10);
    lv_obj_add_flag(setBtn, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(setBtn, onSettings, LV_EVENT_CLICKED, nullptr);
    deferRevert(setBtn);
    lv_obj_t* sbl = text(g_root, "SETTINGS", F_LABEL, CYAN, 0, 0);
    lv_obj_set_width(sbl, sb[2]);
    lv_obj_set_style_text_align(sbl, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(sbl, sb[0], sb[1] + 11);

    g_revert = text(g_root, "", F_LABEL, INKFT, 1280 - 460, 25);

    // ---- the veil goes on LAST: created after every face object, so it is top of the z-order
    // and hit-tested first. Transparent, so it changes nothing visually.
    g_veil = lv_obj_create(scr);
    lv_obj_remove_style_all(g_veil);
    lv_obj_set_pos(g_veil, 0, 0);
    lv_obj_set_size(g_veil, 1280, 720);
    lv_obj_set_style_bg_opa(g_veil, LV_OPA_TRANSP, 0);
    lv_obj_remove_flag(g_veil, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(g_veil, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(g_veil, onVeilPress, LV_EVENT_PRESSED, nullptr);
    lv_obj_move_foreground(g_veil);

    g_built = true;
    mclog::tagInfo(kTag, "built: command link {}", kCommandLink ? "ARMED" : "absent (read-only)");
}

void hmiTick(uint32_t nowMs, const HmiFeed& feed)
{
    if (!g_open || !g_root) return;

    // onAnyHmiPress sets g_lastTouch to 0 as a "reset me" flag rather than reading the clock from
    // an event callback, which runs on the LVGL task rather than this one.
    if (g_lastTouch == 0) g_lastTouch = nowMs;

    static char buf[48];

    // ---- re-assert whatever is being held ----
    // This, not the event, is what moves things: hmiTick runs every frame whether or not any input
    // event was delivered, so a control works even if LVGL sends PRESSED exactly once. The lease is
    // the stop: nothing keeps moving once it lapses, so a missed RELEASED costs kHoldLeaseMs.
    if (g_held != Held::NONE) {
        if (g_heldMs == 0) g_heldMs = nowMs;
        if (nowMs - g_heldMs >= kHoldLeaseMs) {
            holdClear();
        } else {
            switch (g_held) {
                case Held::PAN_L:  pantiltTouchRate(-kDpadDpsUi, 0.0f); break;
                case Held::PAN_R:  pantiltTouchRate( kDpadDpsUi, 0.0f); break;
                case Held::TILT_U: pantiltTouchRate(0.0f,  kDpadDpsUi); break;
                case Held::TILT_D: pantiltTouchRate(0.0f, -kDpadDpsUi); break;
                // Do not even send a lift request the robot would discard: the panel has already
                // said why, and a request that is refused far away looks identical to a bug.
                case Held::RAISE:  if (netLiftAllowed(nullptr)) netLiftRequest(kLiftUp);   break;
                case Held::LOWER:  if (netLiftAllowed(nullptr)) netLiftRequest(kLiftDown); break;
                default: break;
            }
            g_lastTouch = 0;   // holding a control is activity, so the panel must not revert
        }
    }

    // ---- the lift gate: grey the buttons and say what to do about it ----
    // GATING THE LIFT BEHIND MANUAL IS THE DESIGN, not a limitation to hide. What was wrong was
    // leaving it silent: the buttons looked live, did nothing, and gave the operator no idea that
    // CH5 was the reason.
    {
        const char* why = nullptr;
        const bool  ok  = netLiftAllowed(&why);
        for (lv_obj_t* b : {g_upB, g_dnB}) {
            if (!b) continue;
            lv_obj_set_style_opa(b, ok ? LV_OPA_COVER : LV_OPA_40, 0);
            if (ok) lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
            else    lv_obj_remove_flag(b, LV_OBJ_FLAG_CLICKABLE);
        }
        if (g_liftWhyTx) lv_label_set_text(g_liftWhyTx, ok ? "" : (why ? why : "robot is refusing"));
    }

    // ---- MODE reports which mode it is IN, not what it would do ----
    if (g_modeTx) {
        const bool ai = pantiltTracking();
        lv_label_set_text(g_modeTx, ai ? "TRACKING: AI" : "TRACKING: OFF - aimed by hand");
        lv_obj_set_style_text_color(g_modeTx, lv_color_hex(ai ? CYAN : AMBER), 0);
    }

    // ---- the command-link banner, reported rather than assumed ----
    // Three distinct things, and conflating them is how an operator ends up trusting a dead
    // control: we have never heard the StamPLC; we have, and a stop is in flight; we have, and
    // everything is normal (no banner at all).
    if (g_banner && g_bannerTx) {
        unsigned plcAge = 0;
        const bool haveLink = netCommandLink(&plcAge);
        const bool show     = !haveLink || netEstopPending();
        if (show) {
            lv_label_set_text(g_bannerTx,
                              !haveLink ? "WAITING FOR THE STAMPLC - lift and stop have nowhere "
                                          "to go"
                                        : "STOP REQUESTED - resending until the robot confirms");
            lv_obj_set_style_text_color(g_bannerTx, lv_color_hex(netEstopPending() ? REDMID : AMBER),
                                        0);
            lv_obj_set_style_border_color(g_banner,
                                          lv_color_hex(netEstopPending() ? REDMID : AMBER), 0);
        }
        lv_obj_set_style_opa(g_banner,   show ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
        lv_obj_set_style_opa(g_bannerTx, show ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    }

    // ---- state chip ----
    const uint32_t col = feed.stateLinked ? feed.stateColour : 0x60707e;
    for (int i = 0; i < 4; ++i)
        lv_obj_set_style_bg_color(g_chipSwatch[i], lv_color_hex(i == 0 ? col : PANEL2), 0);
    lv_label_set_text(g_chipLabel, feed.stateLabel ? feed.stateLabel : "--");
    lv_obj_set_style_text_color(g_chipLabel, lv_color_hex(feed.stateLinked ? INK : INKFT), 0);
    lv_label_set_text(g_chipDesc, feed.stateDesc ? feed.stateDesc : "no link");

    // ---- link + IP, read straight off netStatus() ----
    const bool online = feed.net && strstr(feed.net, "wifi: ") && strchr(feed.net, '.');
    lv_label_set_text(g_linkVal, online ? "ONLINE" : "OFFLINE");
    lv_obj_set_style_text_color(g_linkVal, lv_color_hex(online ? UPCOL : DNCOL), 0);
    lv_label_set_text(g_ipVal, feed.net ? feed.net : "--");

    // ---- height and ToF have no source in this firmware yet; say so rather than show a zero ----
    lv_label_set_text(g_height, "---");
    lv_label_set_text(g_tofVal, "not wired");

    // ---- idle auto-revert ----
    const uint32_t idle = nowMs - g_lastTouch;
    if (idle >= kRevertMs) {
        setOpen(false);
        return;
    }
    snprintf(buf, sizeof(buf), "face in %us", (unsigned)((kRevertMs - idle) / 1000) + 1);
    lv_label_set_text(g_revert, buf);
}

#else   // OPENDUBS_TOUCH_UI == 0

void hmiBuild()                           {}
bool hmiOwnsPanel()                       { return false; }
const char* hmiDiag()                     { return "touch UI compiled out"; }
void hmiTick(uint32_t, const HmiFeed&)    {}

#endif  // OPENDUBS_TOUCH_UI
