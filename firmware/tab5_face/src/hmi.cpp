#include <M5Unified.h>
#include <cmath>
#include "hmi.h"
#include "lift.h"
#include "ui_shared.h"

// ---------- palette (matches the interactive mockup) ----------
static inline uint16_t C(uint8_t r, uint8_t g, uint8_t b) { return M5.Display.color565(r, g, b); }
#define GROUND  C(10,14,19)
#define PANEL   C(20,27,34)
#define PANEL2  C(15,21,27)
#define HAIR    C(38,49,59)
#define INK     C(232,238,242)
#define INKDIM  C(139,154,166)
#define INKFT   C(120,136,150)     // lifted from the old 86,101,113 for legibility at distance
#define CYAN    C(56,214,240)
#define UPCOL   C(94,217,138)
#define DNCOL   C(240,178,58)
#define REDMID  C(179,29,29)
#define REDDEEP C(74,15,16)
#define REDBRT  C(255,90,90)
#define AMBER   C(255,178,77)

// Font roles — nothing meaningful below ~12pt (panel is ~24" away at knee height).
#define F_LABEL  &fonts::FreeSans9pt7b     // section headers / minor keys
#define F_DATA   &fonts::FreeSans12pt7b    // names, values, status — the readable floor
#define F_WORD   &fonts::FreeSansBold18pt7b
#define F_BIGNUM &fonts::Font7             // 7-seg height digits
#define F_ESTOP  &fonts::FreeSansBold24pt7b

// ---------- layout (1280x720) ----------
static const int W = 1280, H = 720;
static const int TOP = 64;
static const int CHIP[4]  = { 24,  88, 300, 170 };
static const int TAB5[4]  = { 24, 270, 300, 214 };
static const int IOP[4]   = { 24, 498, 300, 190 };
static const int UPB[4]   = { 348,  88, 380, 190 };
static const int RDO[4]   = { 348, 292, 380, 150 };
static const int DNB[4]   = { 348, 456, 380, 190 };
static const int GAUGE[4] = { 662, 304,  52, 126 };
static const int ES_CX = 1090, ES_CY = 372, ES_R = 150;
static const int DP_CX = 834, DP_CY = 372, DP_W = 66, DP_G = 36;
static const int PAN_L[4]  = { DP_CX - DP_W - DP_G, DP_CY - DP_W/2, DP_W, DP_W };
static const int PAN_R[4]  = { DP_CX + DP_G, DP_CY - DP_W/2, DP_W, DP_W };
static const int TILT_U[4] = { DP_CX - DP_W/2, DP_CY - DP_W - DP_G, DP_W, DP_W };
static const int TILT_D[4] = { DP_CX - DP_W/2, DP_CY + DP_G, DP_W, DP_W };
// relay/limit row baselines
static const int IO_ROW0 = 556, IO_DY = 42;

// ---------- change-tracking ----------
static bool     fullRedraw = true;
static uint32_t lastDyn = 0;
static Motion   pMotion = (Motion)255;
static bool     pInhibit = false, pConfirm = false, pAtTop = false, pAtBot = false, pFlash = false;
static bool     pInhibitE = false;
static int      pChip = -2;
static bool     pLink = false;
static HmiHit   pActiveHit = HmiHit::NONE;

static inline bool inRect(int x, int y, const int r[4]) { return x >= r[0] && x < r[0] + r[2] && y >= r[1] && y < r[1] + r[3]; }

static const char* stateDesc(int idx) {
  const char* n = STATES[idx].name;
  if (!strcmp(n, "SAFE_IDLE"))  return "Motion disabled";
  if (!strcmp(n, "MANUAL"))     return "RC teleop";
  if (!strcmp(n, "AUTONOMOUS")) return "Mission active";
  if (!strcmp(n, "CHARGING"))   return "On charger";
  if (!strcmp(n, "DEGRADED"))   return "Reduced function";
  if (!strcmp(n, "E_STOP"))     return "E-stop latched";
  return "Booting";
}

// ---------- primitives ----------
static void fillOct(int cx, int cy, int R, uint16_t c) {
  float px[8], py[8];
  for (int i = 0; i < 8; ++i) { float a = (float)(PI / 8.0 + i * PI / 4.0); px[i] = cx + cosf(a) * R; py[i] = cy + sinf(a) * R; }
  for (int i = 1; i < 7; ++i) M5.Display.fillTriangle(px[0], py[0], px[i], py[i], px[i + 1], py[i + 1], c);
}
static void panel(const int r[4], const char* label) {
  M5.Display.fillRoundRect(r[0], r[1], r[2], r[3], 12, PANEL);
  M5.Display.drawRoundRect(r[0], r[1], r[2], r[3], 12, HAIR);
  if (label) { M5.Display.setFont(F_LABEL); M5.Display.setTextSize(1);
    M5.Display.setTextColor(INKFT); M5.Display.setTextDatum(textdatum_t::top_left);
    M5.Display.drawString(label, r[0] + 16, r[1] + 12); }
}

// ---------- components ----------
static void drawTab5Values() {
  M5.Display.setFont(F_DATA); M5.Display.setTextSize(1); M5.Display.setTextDatum(textdatum_t::middle_right);
  int rx = TAB5[0] + TAB5[2] - 16;
  int y0 = TAB5[1] + 58, y1 = TAB5[1] + 92, y2 = TAB5[1] + 126;
  M5.Display.fillRect(TAB5[0] + 110, TAB5[1] + 42, TAB5[2] - 110 - 12, 100, PANEL);
  M5.Display.setTextColor(INK);                              M5.Display.drawString("Z-AXIS LIFT", rx, y0);
  M5.Display.setTextColor(g_linkOnline ? UPCOL : DNCOL);     M5.Display.drawString(g_linkOnline ? "ONLINE" : "OFFLINE", rx, y1);
  M5.Display.setTextColor(INK);                              M5.Display.drawString(g_ipStr, rx, y2);
}

static void drawChrome() {
  M5.Display.fillScreen(GROUND);
  M5.Display.fillRect(0, 0, W, TOP, C(15, 20, 26));
  M5.Display.drawFastHLine(0, TOP, W, HAIR);
  M5.Display.setFont(&fonts::FreeSansBold12pt7b); M5.Display.setTextSize(1);
  M5.Display.setTextDatum(textdatum_t::middle_left);
  M5.Display.setTextColor(INK);   M5.Display.drawString("OpenDubs", 24, TOP / 2);
  int bx = 24 + M5.Display.textWidth("OpenDubs");
  M5.Display.setTextColor(CYAN);  M5.Display.drawString(" \xC2\xB7 ", bx, TOP / 2); bx += M5.Display.textWidth(" \xC2\xB7 ");
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(INKDIM);
  M5.Display.drawString("Lift Module", bx, TOP / 2);

  // Tab5 identity
  panel(TAB5, "TAB5 - PAYLOAD");
  M5.Display.setFont(F_DATA); M5.Display.setTextDatum(textdatum_t::middle_left); M5.Display.setTextColor(INKFT);
  M5.Display.drawString("MODULE", TAB5[0] + 16, TAB5[1] + 58);
  M5.Display.drawString("LINK",   TAB5[0] + 16, TAB5[1] + 92);
  M5.Display.drawString("IP",     TAB5[0] + 16, TAB5[1] + 126);
  M5.Display.drawString("FRONT ToF", TAB5[0] + 16, TAB5[1] + 160);
  drawTab5Values();

  // Relays & limits — the row NAMES at the readable floor
  panel(IOP, "RELAYS & LIMITS");
  const char* ionames[4] = { "UP RELAY", "DOWN RELAY", "TOP LIMIT", "BOT LIMIT" };
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(INKDIM); M5.Display.setTextDatum(textdatum_t::middle_left);
  for (int i = 0; i < 4; ++i) M5.Display.drawString(ionames[i], IOP[0] + 52, IO_ROW0 + i * IO_DY);

  // readout + gauge frame
  M5.Display.fillRoundRect(RDO[0], RDO[1], RDO[2], RDO[3], 12, PANEL2);
  M5.Display.drawRoundRect(RDO[0], RDO[1], RDO[2], RDO[3], 12, HAIR);
  M5.Display.drawRoundRect(GAUGE[0], GAUGE[1], GAUGE[2], GAUGE[3], 6, HAIR);

  // e-stop note
  M5.Display.setFont(F_LABEL); M5.Display.setTextColor(INKFT); M5.Display.setTextDatum(textdatum_t::top_center);
  M5.Display.drawString("Soft inhibit - drops the authorize-heartbeat.", ES_CX, ES_CY + ES_R + 18);
  M5.Display.setTextColor(DNCOL);
  M5.Display.drawString("NOT the hardware e-stop loop.", ES_CX, ES_CY + ES_R + 40);
}

static void drawChip(int idx) {
  St& s = STATES[idx];
  uint16_t sc = C(s.r, s.g, s.b);
  M5.Display.fillRoundRect(CHIP[0], CHIP[1], CHIP[2], CHIP[3], 12, PANEL);
  M5.Display.drawRoundRect(CHIP[0], CHIP[1], CHIP[2], CHIP[3], 12, HAIR);
  M5.Display.fillRect(CHIP[0] + 1, CHIP[1] + 1, CHIP[2] - 2, 5, sc);
  M5.Display.setFont(F_LABEL); M5.Display.setTextColor(INKFT); M5.Display.setTextDatum(textdatum_t::top_left);
  M5.Display.drawString("ROBOT STATE", CHIP[0] + 16, CHIP[1] + 16);
  int ex = CHIP[0] + 18, ey = CHIP[1] + 44;                 // bigger tiny-eyes
  for (int i = 0; i < 2; ++i) { int x = ex + i * 54;
    M5.Display.fillRoundRect(x, ey, 42, 54, 13, sc);
    M5.Display.fillCircle(x + 12, ey + 15, 5, C(255, 255, 255)); }
  M5.Display.setFont(F_WORD); M5.Display.setTextColor(INK); M5.Display.setTextDatum(textdatum_t::bottom_left);
  M5.Display.drawString(s.label, CHIP[0] + 16, CHIP[1] + CHIP[3] - 40);
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(INKDIM); M5.Display.setTextDatum(textdatum_t::bottom_left);
  M5.Display.drawString(stateDesc(idx), CHIP[0] + 16, CHIP[1] + CHIP[3] - 14);
}

static void drawArrow(const int r[4], bool up, const LiftController::Snapshot& s) {
  bool active   = (up && s.motion == Motion::UP) || (!up && s.motion == Motion::DOWN);
  bool disabled = s.inhibit || (up ? s.atTop : s.atBot);
  uint16_t acc  = up ? UPCOL : DNCOL;
  M5.Display.fillRoundRect(r[0], r[1], r[2], r[3], 14, C(24, 32, 42));
  M5.Display.drawRoundRect(r[0], r[1], r[2], r[3], 14, active ? acc : HAIR);
  if (active) M5.Display.drawRoundRect(r[0] + 1, r[1] + 1, r[2] - 2, r[3] - 2, 13, acc);
  int cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2 - 8, s2 = 52;
  uint16_t gc = disabled ? C(70, 82, 94) : (active ? acc : INKDIM);
  if (up) M5.Display.fillTriangle(cx, cy - s2, cx - s2, cy + s2 * 0.7f, cx + s2, cy + s2 * 0.7f, gc);
  else    M5.Display.fillTriangle(cx, cy + s2, cx - s2, cy - s2 * 0.7f, cx + s2, cy - s2 * 0.7f, gc);
  M5.Display.setFont(&fonts::FreeSansBold12pt7b); M5.Display.setTextSize(1);
  M5.Display.setTextColor(disabled ? C(70, 82, 94) : (active ? acc : INKDIM));
  M5.Display.setTextDatum(textdatum_t::middle_center);
  M5.Display.drawString(up ? "RAISE" : "LOWER", cx, r[1] + r[3] - 28);
}

static void drawEstop(const LiftController::Snapshot& s, const HmiCtx& ctx, bool flashOn) {
  uint16_t fill; const char* lbl; const char* hint;
  if (ctx.estopConfirm)      { fill = AMBER;  lbl = "RELEASE?"; hint = "tap again to release"; }
  else if (s.inhibit)        { fill = flashOn ? REDBRT : REDMID; lbl = "STOPPED"; hint = "inhibited"; }
  else                       { fill = REDMID; lbl = "E-STOP";  hint = "tap to inhibit"; }
  M5.Display.fillRect(ES_CX - ES_R - 2, ES_CY - ES_R - 2, ES_R * 2 + 4, ES_R * 2 + 4, GROUND);
  fillOct(ES_CX, ES_CY, ES_R, ctx.estopConfirm ? C(138, 74, 8) : REDDEEP);
  fillOct(ES_CX, ES_CY, ES_R - 8, fill);
  fillOct(ES_CX, ES_CY, ES_R - 16, ctx.estopConfirm ? AMBER : (s.inhibit ? fill : C(150, 22, 22)));
  M5.Display.setFont(F_ESTOP); M5.Display.setTextSize(1);
  M5.Display.setTextColor(C(255, 235, 235)); M5.Display.setTextDatum(textdatum_t::middle_center);
  M5.Display.drawString(lbl, ES_CX, ES_CY - 8);
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(C(255, 212, 212));
  M5.Display.drawString(hint, ES_CX, ES_CY + 40);
}

static void drawReadout(const LiftController::Snapshot& s) {
  M5.Display.fillRect(RDO[0] + 14, RDO[1] + 16, 300, 68, PANEL2);
  M5.Display.setFont(F_BIGNUM); M5.Display.setTextSize(1);
  M5.Display.setTextColor(INK); M5.Display.setTextDatum(textdatum_t::top_left);
  char buf[8]; snprintf(buf, sizeof buf, "%d", (int)lroundf(s.heightMm));
  M5.Display.drawString(buf, RDO[0] + 18, RDO[1] + 20);
  int wpx = RDO[0] + 18 + M5.Display.textWidth(buf) + 8;
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(INKDIM); M5.Display.setTextDatum(textdatum_t::bottom_left);
  M5.Display.drawString("mm", wpx, RDO[1] + 72);
  // status (no ToF here anymore)
  M5.Display.fillRect(RDO[0] + 14, RDO[1] + RDO[3] - 40, RDO[2] - 92, 34, PANEL2);
  const char* st = s.inhibit ? "INHIBITED" : s.motion == Motion::UP ? "RAISING"
                 : s.motion == Motion::DOWN ? "LOWERING" : "STOPPED";
  M5.Display.setFont(F_DATA); M5.Display.setTextColor(INKDIM);
  M5.Display.setTextDatum(textdatum_t::bottom_left);
  M5.Display.drawString(st, RDO[0] + 18, RDO[1] + RDO[3] - 14);
}

static void drawGauge(const LiftController::Snapshot& s) {
  int ix = GAUGE[0] + 2, iy = GAUGE[1] + 2, iw = GAUGE[2] - 4, ih = GAUGE[3] - 4;
  M5.Display.fillRect(ix, iy, iw, ih, C(11, 17, 22));
  float frac = (s.heightMm - LiftController::MIN_MM) / (LiftController::MAX_MM - LiftController::MIN_MM);
  if (frac < 0) frac = 0; if (frac > 1) frac = 1;
  int fh = (int)(ih * frac);
  if (fh > 0) M5.Display.fillRect(ix, iy + ih - fh, iw, fh, CYAN);
  M5.Display.drawFastHLine(ix, iy + 6, iw, C(60, 72, 84));
  M5.Display.drawFastHLine(ix, iy + ih - 6, iw, C(60, 72, 84));
}

static void drawLamps(const LiftController::Snapshot& s) {
  bool st[4] = { s.relayUpPulse, s.relayDnPulse, s.atTop, s.atBot };
  bool isLim[4] = { false, false, true, true };
  for (int i = 0; i < 4; ++i) { int y = IO_ROW0 + i * IO_DY;
    uint16_t c = !st[i] ? C(28, 39, 50) : (isLim[i] ? C(255, 77, 77) : CYAN);
    M5.Display.fillRoundRect(IOP[0] + 16, y - 11, 22, 22, 5, c);
    M5.Display.drawRoundRect(IOP[0] + 16, y - 11, 22, 22, 5, st[i] ? c : HAIR); }
}

static void drawTopRight(const HmiCtx& ctx) {
  int rx = W - 24;
  M5.Display.fillRect(740, 6, W - 740 - 6, TOP - 12, C(15, 20, 26));
  M5.Display.setTextDatum(textdatum_t::middle_right);
  M5.Display.setFont(F_LABEL);
  char rv[24]; snprintf(rv, sizeof rv, "\xE2\x86\xA9 face in %ds", ctx.revertSecs);
  M5.Display.setTextColor(INKFT); M5.Display.drawString(rv, rx, TOP / 2); rx -= M5.Display.textWidth(rv) + 24;
  M5.Display.setFont(F_DATA);
  char ipl[40]; snprintf(ipl, sizeof ipl, "wlan %s", g_ipStr);
  M5.Display.setTextColor(INKFT); M5.Display.drawString(ipl, rx, TOP / 2); rx -= M5.Display.textWidth(ipl) + 24;
  const char* lk = g_linkOnline ? "LINK ONLINE" : "LINK OFFLINE";
  M5.Display.setTextColor(INKDIM); M5.Display.drawString(lk, rx, TOP / 2); rx -= M5.Display.textWidth(lk) + 14;
  M5.Display.fillCircle(rx, TOP / 2, 6, g_linkOnline ? UPCOL : DNCOL);
}

static void drawFrontToF(const LiftController::Snapshot& s) {
  int rx = TAB5[0] + TAB5[2] - 16;
  int y3 = TAB5[1] + 160;
  M5.Display.fillRect(TAB5[0] + 110, TAB5[1] + 144, TAB5[2] - 110 - 12, 32, PANEL);
  M5.Display.setFont(F_DATA); M5.Display.setTextSize(1); M5.Display.setTextDatum(textdatum_t::middle_right);
  if (s.tofOk) {
    char buf[32]; snprintf(buf, sizeof buf, "%d mm", (int)lroundf(s.tofMm));
    M5.Display.setTextColor(CYAN);
    M5.Display.drawString(buf, rx, y3);
  } else {
    M5.Display.setTextColor(DNCOL);
    M5.Display.drawString("ERR", rx, y3);
  }
  
  // Live servo angles
  extern int16_t g_targetPan;
  extern int16_t g_targetTilt;
  extern bool g_isHoming;
  char angBuf[64];
  if (g_isHoming) {
    snprintf(angBuf, sizeof angBuf, "HOMING... P:%d T:%d", g_targetPan, g_targetTilt);
  } else {
    snprintf(angBuf, sizeof angBuf, "PAN: %d\xB0   TILT: %d\xB0", g_targetPan, g_targetTilt);
  }
  M5.Display.fillRect(TAB5[0] + 16, TAB5[1] + TAB5[3] - 26, 260, 24, PANEL);
  M5.Display.setFont(F_LABEL);
  M5.Display.setTextColor(CYAN);
  M5.Display.setTextDatum(textdatum_t::bottom_left);
  M5.Display.drawString(angBuf, TAB5[0] + 16, TAB5[1] + TAB5[3] - 4);
}

static void drawDPadBtn(const int r[4], const char* label, bool active) {
  uint16_t c = active ? CYAN : PANEL2;
  M5.Display.fillRoundRect(r[0], r[1], r[2], r[3], 12, c);
  M5.Display.drawRoundRect(r[0], r[1], r[2], r[3], 12, HAIR);
  M5.Display.setFont(F_WORD);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(active ? GROUND : INKDIM);
  M5.Display.setTextDatum(textdatum_t::middle_center);
  M5.Display.drawString(label, r[0] + r[2]/2, r[1] + r[3]/2);
}

static void drawDPad(const HmiCtx& ctx) {
  drawDPadBtn(PAN_L, "LT", ctx.activeHit == HmiHit::PAN_LEFT);
  drawDPadBtn(PAN_R, "RT", ctx.activeHit == HmiHit::PAN_RIGHT);
  drawDPadBtn(TILT_U, "UP", ctx.activeHit == HmiHit::TILT_UP);
  drawDPadBtn(TILT_D, "DN", ctx.activeHit == HmiHit::TILT_DOWN);
}

// ---------- public ----------
void hmiInvalidate() { fullRedraw = true; }

HmiHit hmiHitTest(int x, int y) {
  if (inRect(x, y, UPB)) return HmiHit::RAISE;
  if (inRect(x, y, DNB)) return HmiHit::LOWER;
  if (inRect(x, y, PAN_L)) return HmiHit::PAN_LEFT;
  if (inRect(x, y, PAN_R)) return HmiHit::PAN_RIGHT;
  if (inRect(x, y, TILT_U)) return HmiHit::TILT_UP;
  if (inRect(x, y, TILT_D)) return HmiHit::TILT_DOWN;
  int dx = x - ES_CX, dy = y - ES_CY;
  if (dx * dx + dy * dy <= ES_R * ES_R) return HmiHit::ESTOP;
  return HmiHit::NONE;
}

void hmiRender(const HmiCtx& ctx) {
  uint32_t now = millis();
  LiftController::Snapshot s = Lift.snapshot();

  if (fullRedraw) {
    drawChrome(); drawChip(g_robotState); drawArrow(UPB, true, s); drawArrow(DNB, false, s);
    drawEstop(s, ctx, false); drawReadout(s); drawGauge(s); drawLamps(s); drawTopRight(ctx); drawFrontToF(s);
    drawDPad(ctx);
    fullRedraw = false;
    pMotion = s.motion; pInhibit = pInhibitE = s.inhibit; pAtTop = s.atTop; pAtBot = s.atBot;
    pConfirm = ctx.estopConfirm; pFlash = false; pChip = g_robotState; pLink = g_linkOnline;
    pActiveHit = ctx.activeHit;
    lastDyn = now; return;
  }

  if (s.motion != pMotion || s.inhibit != pInhibit || s.atTop != pAtTop || s.atBot != pAtBot) {
    drawArrow(UPB, true, s); drawArrow(DNB, false, s);
    pMotion = s.motion; pInhibit = s.inhibit; pAtTop = s.atTop; pAtBot = s.atBot;
  }
  bool flashOn = s.inhibit ? ((now / 450) & 1) : false;
  if (s.inhibit != pInhibitE || ctx.estopConfirm != pConfirm || flashOn != pFlash) {
    drawEstop(s, ctx, flashOn); pInhibitE = s.inhibit; pConfirm = ctx.estopConfirm; pFlash = flashOn;
  }
  if (g_robotState != pChip) { drawChip(g_robotState); pChip = g_robotState; }
  if (g_linkOnline != pLink) { pLink = g_linkOnline; drawTab5Values(); drawTopRight(ctx); }
  
  if (ctx.activeHit != pActiveHit) {
    drawDPad(ctx);
    pActiveHit = ctx.activeHit;
  }

  if (now - lastDyn >= 50) { lastDyn = now;
    drawReadout(s); drawGauge(s); drawLamps(s); drawTopRight(ctx); drawFrontToF(s); }
}
