// ONE PASS OVER THE CAMERA BUFFER, producing every representation anything downstream wants.
//
// PURE COMPUTE. This translation unit reads a pixel buffer and writes pixel buffers. It includes
// no LVGL, no HAL, no driver and no FreeRTOS, so it compiles unchanged on the desktop and is
// exercised by `./build.sh percept` on the host -- which is the only verification available to
// anyone without the board in front of them.
//
// WHY ONE PASS. The source is a 1.84 MB RGB565 frame in PSRAM and the sampling is strided: a loop
// that reads every 4th pixel of every 4th row touches a fresh 128-byte cache line every 16 pixels.
// That access pattern, not the arithmetic, is what makes this cost milliseconds
// (notes/design/tab5-face-architecture.md §4.2), so the number of passes over the source is the
// cost. Three outputs from one pass; each is optional and costs only its own writes.
//
// The eventual replacement is the PPA doing the scaling in hardware (architecture §2.3), at which
// point this file keeps only the luma extraction. It is written so that change is a deletion.
#pragma once
#include <stddef.h>
#include <stdint.h>

// The working resolution. 320x180 is 16:9, the same aspect as the 1280x720 source, so nothing is
// distorted, and its luma plane is 57,600 bytes -- the ~58 KB the perception budget allows for the
// previous frame. 1280/320 and 720/180 are both exactly 4, so the sampling is a clean decimation
// with no accumulating remainder.
constexpr int kPerceptW = 320;
constexpr int kPerceptH = 180;

// Reads `src` (RGB565, srcW x srcH, row stride srcW pixels) and writes, for each of the
// kPerceptW x kPerceptH output pixels:
//   luma     : one byte of BT.601 luma, 0..255. Required (the whole point).
//   preview  : the sampled RGB565 pixel, for a debug canvas. Pass nullptr to skip.
//   rgb888   : r,g,b bytes, the only format the face detector is proven on. Pass nullptr to skip.
//
// Tearing is expected and harmless: the camera task overwrites `src` while we read it, so a sampled
// frame may straddle two captures. For a difference metric that shows up as one frame of extra
// motion, which is indistinguishable from motion.
void frameSample(const uint16_t* src, int srcW, int srcH,
                 uint8_t* luma, uint16_t* preview, uint8_t* rgb888);

// Exposure of the last sampled frame: the darkest, mean and brightest luma. This exists because
// "no face" and "no motion" are the same observation as "the lens is covered" or "capture died",
// and the three are told apart by whether the picture has any range in it. The live face tracker
// reported these for exactly that reason and it was the right call; keep it.
struct FrameStats {
    uint8_t lo, hi;
    uint8_t mean;
};
FrameStats frameLastStats();
