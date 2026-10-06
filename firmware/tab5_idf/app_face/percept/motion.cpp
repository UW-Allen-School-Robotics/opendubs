#include "motion.h"
#include "../shared/alloc.h"
#include <string.h>

namespace {

// ---- the four numbers that decide what counts as motion ------------------------------------
// EVERY ONE OF THESE IS A REASONED GUESS, NOT A MEASUREMENT. None has been seen against a real
// scene, because nobody has had the board. They are grouped here so that tuning them is one edit
// in one place, and notes/design/tab5-face-architecture.md records which observation replaces
// which number.

// How far a pixel's luma must move to count as changed. Sensor read noise on this class of sensor
// is a few counts; 16/255 is comfortably above it and comfortably below the change a person walking
// across a mid-contrast background produces.
constexpr int kPixelDelta = 16;

// How much of a cell must be changing before the cell is a candidate at all, as a fraction of its
// 400 pixels. 6% = 24 pixels. Below this it is noise, a flickering highlight, or compression-like
// sensor artefacts.
constexpr int kCellFloorPx = (kMotionCellArea * 6) / 100;

// If more than this fraction of the whole frame changed, the change is not an object in the scene:
// it is the lighting, the camera, or the robot. Discard the frame rather than publish a target
// that is really a description of our own movement.
constexpr float kGlobalChangeFrac = 0.30f;

// Temporal smoothing, alpha = 1/4 per frame, so a cell reaches ~90% of a sustained level in about
// 8 frames (~0.5 s at 15 Hz) and a single-frame spike reaches only a quarter of it. This is what
// makes "a strong, repeated hit earns a sustained look" true of motion as well as of faces: a
// flicker cannot win, a person crossing the room does.
//
// It assumes a roughly constant frame period. The perception loop is a fixed vTaskDelay, so that
// holds; if the loop ever becomes frame-driven and irregular, this becomes a time constant that
// varies with load and should be re-expressed against dt.
constexpr int kHeatShift = 2;   // alpha = 1 / (1 << kHeatShift)

// Heat is stored with 4 fractional bits, so a cell saturated at all 400 pixels is 6400 -- inside
// uint16 with room to spare, and the smoothing stays integer.
constexpr int kHeatFrac = 16;

uint8_t* g_prev = nullptr;
bool g_primed = false;
uint16_t g_heat[kMotionCellsX * kMotionCellsY];
uint16_t g_count[kMotionCellsX * kMotionCellsY];

}  // namespace

bool motionBegin()
{
    if (!g_prev) {
        // 57,600 bytes: over the 16 KB threshold, so a plain malloc would land it in PSRAM anyway
        // -- but relying on that is relying on a Kconfig value staying where it is. bigAlloc names
        // the capability. See shared/alloc.h.
        g_prev = (uint8_t*)bigAlloc((size_t)kPerceptW * kPerceptH);
        if (!g_prev) return false;
    }
    motionReset();
    return true;
}

void motionReset()
{
    g_primed = false;
    memset(g_heat, 0, sizeof(g_heat));
    memset(g_count, 0, sizeof(g_count));
}

const uint16_t* motionHeat()
{
    return g_heat;
}

MotionResult motionUpdate(const uint8_t* luma, bool suppress)
{
    MotionResult r = {};
    r.cellX = -1;
    r.cellY = -1;
    if (!g_prev || !luma) {
        r.gated = true;
        return r;
    }

    const size_t plane = (size_t)kPerceptW * kPerceptH;

    // FIRST FRAME, AND EVERY FRAME WE DO NOT TRUST, TAKE THE SAME PATH: adopt the frame as history
    // and measure nothing. Without the `g_primed` flag the first difference would be against a
    // zeroed plane, i.e. against black, and every pixel would register -- which the global gate
    // below would catch, but only by accident. Being explicit means the first frame is handled by
    // the rule that handles it, not by a side effect.
    if (suppress || !g_primed) {
        memcpy(g_prev, luma, plane);
        g_primed = true;
        memset(g_heat, 0, sizeof(g_heat));
        r.gated = true;
        return r;
    }

    memset(g_count, 0, sizeof(g_count));
    unsigned changed = 0;

    for (int y = 0; y < kPerceptH; ++y) {
        const uint8_t* cur = luma + (size_t)y * kPerceptW;
        uint8_t* prv = g_prev + (size_t)y * kPerceptW;
        uint16_t* crow = g_count + (y / kMotionCellH) * kMotionCellsX;
        for (int x = 0; x < kPerceptW; ++x) {
            const int d = (int)cur[x] - (int)prv[x];
            prv[x] = cur[x];                      // history update folded into the same pass
            if (d > kPixelDelta || d < -kPixelDelta) {
                crow[x / kMotionCellW]++;
                changed++;
            }
        }
    }

    r.changedFrac = (float)changed / (float)plane;

    // THE GLOBAL-CHANGE GATE. A lighting change, an auto-exposure step, the robot driving, the head
    // panning: all of them move most of the frame at once. Publishing the hottest cell of such a
    // frame would be publishing an artefact of our own motion as if it were someone standing there.
    // The whole history is dropped, because the scene we were differencing against no longer
    // exists.
    if (r.changedFrac > kGlobalChangeFrac) {
        memset(g_heat, 0, sizeof(g_heat));
        r.gated = true;
        return r;
    }

    // Smooth, then find the hottest cell.
    int best = -1;
    uint16_t bestHeat = 0;
    for (int i = 0; i < kMotionCellsX * kMotionCellsY; ++i) {
        const int target = (int)g_count[i] * kHeatFrac;
        g_heat[i] = (uint16_t)((int)g_heat[i] - ((int)g_heat[i] >> kHeatShift) + (target >> kHeatShift));
        if (g_heat[i] > bestHeat) {
            bestHeat = g_heat[i];
            best = i;
        }
    }

    const uint16_t floorHeat = (uint16_t)(kCellFloorPx * kHeatFrac);
    if (best < 0 || bestHeat <= floorHeat) return r;   // valid stays false: nothing worth looking at

    const int bx = best % kMotionCellsX;
    const int by = best / kMotionCellsX;
    r.cellX = bx;
    r.cellY = by;
    r.strength = (float)bestHeat / (float)(kHeatFrac * kMotionCellArea);
    if (r.strength > 1.0f) r.strength = 1.0f;

    // SUB-CELL CENTROID over the hottest cell and its eight neighbours, weighted by heat above the
    // floor. Without it the target can only ever be one of 144 positions, and a person walking
    // produces a visible staircase as the hot cell hands over to the next -- the eyes would step
    // rather than track. With it the target moves continuously and a two-cell-wide blob lands
    // between them, where it is.
    float wsum = 0.0f, cxs = 0.0f, cys = 0.0f;
    for (int dy = -1; dy <= 1; ++dy) {
        const int cy = by + dy;
        if (cy < 0 || cy >= kMotionCellsY) continue;
        for (int dx = -1; dx <= 1; ++dx) {
            const int cx = bx + dx;
            if (cx < 0 || cx >= kMotionCellsX) continue;
            const uint16_t h = g_heat[cy * kMotionCellsX + cx];
            if (h <= floorHeat) continue;
            const float w = (float)(h - floorHeat);
            wsum += w;
            cxs += w * ((float)cx + 0.5f);
            cys += w * ((float)cy + 0.5f);
        }
    }
    // wsum cannot be zero here -- the hottest cell itself is above the floor and is inside the
    // window -- but a divisor is never left unguarded in this app after the band's expf divisor
    // was one added activity away from a NaN in a style value.
    if (wsum <= 0.0f) {
        cxs = (float)bx + 0.5f;
        cys = (float)by + 0.5f;
        wsum = 1.0f;
    }

    // NORMALISE EXACTLY AS THE FACE PATH DOES, in the same buffer, with no extra flip.
    //
    // This is the one place a sign error would be invisible and expensive: "the eyes follow me" is
    // not falsifiable by watching eyes that are always moving. The live face tracker settled its
    // own signs by watching a reticle and recorded the answer -- the PPA stage already applies
    // mirror_x, so a second flip here would make two mirrors that cancel into backwards tracking.
    // By normalising the identical way over the identical buffer, motion inherits that verified
    // result instead of needing its own.
    r.nx = (cxs / wsum) / (float)kMotionCellsX * 2.0f - 1.0f;
    r.ny = (cys / wsum) / (float)kMotionCellsY * 2.0f - 1.0f;
    if (r.nx < -1.0f) r.nx = -1.0f; else if (r.nx > 1.0f) r.nx = 1.0f;
    if (r.ny < -1.0f) r.ny = -1.0f; else if (r.ny > 1.0f) r.ny = 1.0f;
    r.valid = true;
    return r;
}
