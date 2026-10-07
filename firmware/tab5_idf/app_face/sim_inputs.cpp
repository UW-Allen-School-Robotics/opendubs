#include "sim_inputs.h"

#ifndef ESP_PLATFORM

#include <cstring>
#include <lvgl.h>

#include "hal/hal.h"
#include "net_cmd.h"
#include "percept/attention.h"
#include "shared/clock.h"

namespace {

constexpr int kScreenW = 1280, kScreenH = 720;

// -1 means "no simulated distance set", which is NOT the same as zero: zero is a plausible reading
// and the ToF driver on the head refuses to report it for exactly that reason.
float g_tofMm   = -1.0f;
bool  g_person  = false;
bool  g_estop   = false;
int   g_stateIx = -1;          // index into the face's state table; -1 = link down
uint32_t g_keyCount = 0;       // every handled key, so a press is visible even if it toggles back
uint32_t g_estopAt  = 0;       // when the simulated robot confirms the stop; 0 = nothing pending

lv_obj_t* g_badge  = nullptr;  // the word SIMULATED
lv_obj_t* g_status = nullptr;  // what is currently simulated
lv_obj_t* g_keys   = nullptr;  // the key legend, so nobody has to read this file
char      g_lastStatus[160] = "";

// The fake person's gaze target, and a sequence number the face uses to decide when to saccade.
// It must change only on a NEW target or the eyes re-aim every frame and never fixate -- which is
// the behaviour the easing/spring split exists to avoid.
float    g_gx = 0.0f, g_gy = 0.0f;
uint32_t g_gseq = 0;

int stateIxByName(const char* n)
{
    for (int i = 0; i < faceStateCount(); ++i)
        if (strcmp(faceStateName(i), n) == 0) return i;
    return -1;
}

// AN E-STOP IS TWO EVENTS, NOT ONE, and the twin was only simulating the first.
//
// On the head: the button sets a PENDING request that is resent every 50 ms, the robot eventually
// reports E_STOP, and that confirmation is what clears the pending flag. So there are two distinct
// things to see -- "STOP REQUESTED, resending" and "the robot is stopped" -- and the twin could
// only ever show the first. Emma caught this: pressing `e` moved the banner and left the face on
// whatever state it already had.
//
// On the robot the head loses power on an e-stop -- the contactor feeds it -- so this sequence is
// only ever visible here. That is intentional design, not a gap: see CLAUDE.md.
void estopRequest()
{
    g_estop   = true;                       // the pending banner
    g_estopAt = perceptNowMs() + 1200;      // the robot takes a moment to confirm
}

void estopRearm()
{
    // Only the dashboard's guarded re-arm can do this for real -- the head can always stop the
    // robot and never un-stop it. The twin offers it because a twin you cannot reset is a twin you
    // restart, and that is a worse tool.
    g_estop   = false;
    g_estopAt = 0;
    const int ix = stateIxByName("SAFE_IDLE");
    if (ix >= 0) { g_stateIx = ix; netSetState("SAFE_IDLE"); }
}

lv_indev_t* pointerIndev()
{
    for (lv_indev_t* d = lv_indev_get_next(nullptr); d; d = lv_indev_get_next(d))
        if (lv_indev_get_type(d) == LV_INDEV_TYPE_POINTER) return d;
    return nullptr;
}

void cycleState()
{
    const int n = faceStateCount();
    // Walks off the end into "link down" deliberately, so every state AND the no-link face are
    // reachable from one key rather than needing two.
    if (++g_stateIx >= n) g_stateIx = -1;
    if (g_stateIx < 0) netClearState();
    else               netSetState(faceStateName(g_stateIx));
}

void cycleTof()
{
    // A short list that covers the cases the HMI readout distinguishes: nothing set, close, mid,
    // far, and beyond a VL53L0X's reach.
    if      (g_tofMm < 0.0f)    g_tofMm = 150.0f;
    else if (g_tofMm < 200.0f)  g_tofMm = 400.0f;
    else if (g_tofMm < 500.0f)  g_tofMm = 900.0f;
    else if (g_tofMm < 1000.0f) g_tofMm = 2000.0f;
    else                        g_tofMm = -1.0f;
}

void onKey(lv_event_t* e)
{
    ++g_keyCount;
    switch (lv_event_get_key(e)) {
        case 's': case 'S': cycleState();                                   break;
        case 'l': case 'L': if (g_stateIx < 0) { g_stateIx = 0; netSetState(faceStateName(0)); }
                            else               { g_stateIx = -1; netClearState(); }   break;
        // Toggles between asking and re-arming, so one key walks the whole sequence.
        case 'e': case 'E': if (g_estop || stateIxByName("E_STOP") == g_stateIx) estopRearm();
                            else                                                 estopRequest();
                            break;
        case 'p': case 'P': g_person = !g_person;                           break;
        case 't': case 'T': cycleTof();                                     break;
        default: break;
    }
}

// KEYS GO TO THE FOCUSED OBJECT IN THE DEFAULT GROUP, which is how LVGL routes a keypad indev --
// there is no global key hook. So the twin owns a zero-size invisible object, keeps it focused, and
// takes the focus back if anything else steals it. Without the re-focus, opening the settings panel
// once would silently kill every key for the rest of the run.
void onDefocus(lv_event_t* e)
{
    lv_group_focus_obj((lv_obj_t*)lv_event_get_target(e));
}

}  // namespace

void simBegin(void* screenv)
{
    lv_obj_t* scr = (lv_obj_t*)screenv;

    // ---- the badge: unmissable, and never behind anything ----
    g_badge = lv_label_create(scr);
    lv_label_set_text(g_badge, "SIMULATED  -  TWIN, NOT THE ROBOT");
    lv_obj_set_style_text_color(g_badge, lv_color_hex(0xffb020), 0);
    lv_obj_set_style_bg_color(g_badge, lv_color_hex(0x2a1f0a), 0);
    lv_obj_set_style_bg_opa(g_badge, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(g_badge, 6, 0);
    lv_obj_set_style_text_font(g_badge, &lv_font_montserrat_20, 0);
    lv_obj_align(g_badge, LV_ALIGN_TOP_MID, 0, 6);

    g_status = lv_label_create(scr);
    lv_obj_set_style_text_color(g_status, lv_color_hex(0xffd4a0), 0);
    lv_obj_set_style_text_font(g_status, &lv_font_montserrat_16, 0);
    lv_obj_align(g_status, LV_ALIGN_TOP_MID, 0, 40);

    g_keys = lv_label_create(scr);
    lv_label_set_text(g_keys, "s state   l link   e e-stop   p person (mouse)   t ToF");
    lv_obj_set_style_text_color(g_keys, lv_color_hex(0x8a94a0), 0);
    lv_obj_set_style_text_font(g_keys, &lv_font_montserrat_16, 0);
    lv_obj_align(g_keys, LV_ALIGN_BOTTOM_MID, 0, -6);

    // ---- the key sink ----
    lv_obj_t* sink = lv_obj_create(scr);
    lv_obj_remove_style_all(sink);
    lv_obj_set_size(sink, 1, 1);
    lv_obj_set_pos(sink, 0, 0);
    lv_obj_add_flag(sink, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(sink, onKey, LV_EVENT_KEY, nullptr);
    lv_obj_add_event_cb(sink, onDefocus, LV_EVENT_DEFOCUSED, nullptr);
    lv_group_add_obj(lv_group_get_default(), sink);
    lv_group_focus_obj(sink);
}

void simTick(uint32_t nowMs)
{
    (void)nowMs;
    if (!g_status) return;

    // The simulated robot confirms the stop, which is also what clears the pending banner -- the
    // same ordering the head relies on (net.cpp clears g_estopReq when the state reads E_STOP).
    if (g_estopAt != 0 && perceptNowMs() >= g_estopAt) {
        g_estopAt = 0;
        g_estop   = false;
        const int ix = stateIxByName("E_STOP");
        if (ix >= 0) { g_stateIx = ix; netSetState("E_STOP"); }
    }

    // Follow the mouse while a person is switched on. Mapped to -1..1 in the same frame the camera
    // would have used, so the face cannot tell a simulated person from a real one -- which is the
    // whole point, and why the badge above is not optional.
    if (g_person) {
        GetHAL()->lvglLock();
        lv_indev_t* p = pointerIndev();
        lv_point_t  pt{0, 0};
        if (p) lv_indev_get_point(p, &pt);
        GetHAL()->lvglUnlock();
        const float nx = (float)pt.x / (float)kScreenW * 2.0f - 1.0f;
        const float ny = (float)pt.y / (float)kScreenH * 2.0f - 1.0f;
        // A NEW TARGET only when it has actually moved. Bumping the sequence every frame would make
        // the eyes re-saccade continuously and never settle on anything.
        if (nx < g_gx - 0.04f || nx > g_gx + 0.04f || ny < g_gy - 0.04f || ny > g_gy + 0.04f) {
            g_gx = nx; g_gy = ny; ++g_gseq;
        }
    }

    char buf[160];
    const char* sname = (g_stateIx < 0) ? "NO LINK" : faceStateName(g_stateIx);
    if (g_tofMm < 0.0f)
        snprintf(buf, sizeof(buf), "state %s   e-stop %s   person %s   ToF --",
                 sname, g_estop ? "REQUESTED" : "off", g_person ? "ON (mouse)" : "off");
    else
        snprintf(buf, sizeof(buf), "state %s   e-stop %s   person %s   ToF %d mm",
                 sname, g_estop ? "REQUESTED" : "off", g_person ? "ON (mouse)" : "off", (int)g_tofMm);

    // Only on change: the labels sit on the same screen as the face, and rewriting plus re-raising
    // them every frame would invalidate that area 30 times a second for nothing.
    if (strcmp(buf, g_lastStatus) != 0) {
        snprintf(g_lastStatus, sizeof(g_lastStatus), "%s", buf);
        GetHAL()->lvglLock();
        lv_label_set_text(g_status, buf);
        lv_obj_move_foreground(g_badge);
        lv_obj_move_foreground(g_status);
        lv_obj_move_foreground(g_keys);
        GetHAL()->lvglUnlock();
    }
}

bool simGaze(GazeIntent* out)
{
    if (!g_person || !out) return false;
    const uint32_t now = perceptNowMs();
    out->nx          = g_gx;
    out->ny          = g_gy;
    out->confidence  = 0.9f;        // a simulated person is a confident one; that is the useful case
    out->seq         = g_gseq;
    out->stampMs     = now;
    out->goodUntilMs = now + 1000;
    out->kind        = 0;           // GAZE_FACE
    return true;
}

bool simTofMm(float* mm)
{
    if (g_tofMm < 0.0f) return false;
    if (mm) *mm = g_tofMm;
    return true;
}

bool simEstop() { return g_estop; }

int simReport(char* out, size_t cap)
{
    return snprintf(out, cap, "sim[state=%s estop=%s person=%d tof=%d keys=%u]",
                    (g_stateIx < 0) ? "nolink" : faceStateName(g_stateIx),
                    g_estop ? "PENDING" : "off",
                    g_person ? 1 : 0, (int)g_tofMm, (unsigned)g_keyCount);
}

int simCommand(const char* arg, char* tx, size_t cap)
{
    float mm = 0.0f;
    if (strncmp(arg, "LINK ", 5) == 0) {
        if (strcmp(arg + 5, "UP") == 0)        { g_stateIx = 0; netSetState(faceStateName(0)); }
        else if (strcmp(arg + 5, "DOWN") == 0) { g_stateIx = -1; netClearState(); }
        else return snprintf(tx, cap, "ERR SIM LINK UP|DOWN");
        return snprintf(tx, cap, "OK sim link %s", arg + 5);
    }
    if (strncmp(arg, "ESTOP ", 6) == 0) {
        if (strcmp(arg + 6, "ON") == 0)       estopRequest();
        else if (strcmp(arg + 6, "OFF") == 0) estopRearm();
        else return snprintf(tx, cap, "ERR SIM ESTOP ON|OFF");
        return snprintf(tx, cap, "OK sim estop %s", arg + 6);
    }
    if (strncmp(arg, "PERSON ", 7) == 0) {
        if (strcmp(arg + 7, "ON") == 0)       g_person = true;
        else if (strcmp(arg + 7, "OFF") == 0) g_person = false;
        else return snprintf(tx, cap, "ERR SIM PERSON ON|OFF");
        return snprintf(tx, cap, "OK sim person %s", arg + 7);
    }
    if (strncmp(arg, "TOF ", 4) == 0) {
        if (strcmp(arg + 4, "OFF") == 0) { g_tofMm = -1.0f; return snprintf(tx, cap, "OK sim tof off"); }
        if (sscanf(arg + 4, "%f", &mm) != 1 || mm < 0.0f)
            return snprintf(tx, cap, "ERR SIM TOF <mm>|OFF");
        g_tofMm = mm;
        return snprintf(tx, cap, "OK sim tof %d mm", (int)mm);
    }
    return snprintf(tx, cap, "ERR SIM LINK|ESTOP|PERSON|TOF");
}

#endif  // !ESP_PLATFORM
