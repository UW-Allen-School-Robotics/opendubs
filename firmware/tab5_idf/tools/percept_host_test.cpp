// HOST HARNESS for the perception layer. `./build.sh percept` compiles and runs this.
//
// It exists because perception is PURE COMPUTE by construction -- no LVGL, no HAL, no FreeRTOS, no
// driver -- so the identical translation units that run on the panel compile and run on a laptop.
// That makes the arithmetic verifiable without the board, which matters here: this code was written
// with no access to the hardware at all, and "it compiles" is not a result.
//
// What a PASS here means and does not mean is spelled out in app_face/percept/selftest.h. Short
// version: it proves the differencing, the accumulation, the centroid, the gates, the precedence
// and the grading against a synthetic stimulus. It proves nothing about this sensor's noise, this
// lens's field of view, or whether the sign conventions match the real camera -- those are bench
// observations, listed in notes/design/tab5-face-architecture.md.
//
// Not part of the device build: tools/ is never synced into the demo tree.
#include <cstdio>
#include "percept/selftest.h"
#include "percept/frame.h"
#include "percept/motion.h"

static void sink(const char* line)
{
    std::printf("%s\n", line);
}

int main()
{
    std::printf("percept host self-test\n");
    std::printf("  working resolution %dx%d luma (%d B), %dx%d cells of %dx%d px\n",
                kPerceptW, kPerceptH, kPerceptW * kPerceptH,
                kMotionCellsX, kMotionCellsY, kMotionCellW, kMotionCellH);

    const bool ok = perceptSelfTestVerbose(sink);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
