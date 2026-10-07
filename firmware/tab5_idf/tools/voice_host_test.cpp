// HOST HARNESS for the voice layer. `./build.sh voice` compiles and runs this.
//
// Same construction and same justification as tools/percept_host_test.cpp: the layer that decides
// whether someone is speaking is pure compute over int16 samples -- no LVGL, no HAL, no FreeRTOS,
// no driver -- so the identical translation units that run on the panel compile and run on a
// laptop.
//
// That matters more here than it did for percept. At the time this was written the vendor
// M5Tab5-UserDemo tree was not on the development machine at all, so `./build.sh image` and
// `./build.sh desktop` could not run and "it compiles" was not even available as a claim. This is
// the whole verification story for the voice layer until the board is in the loop.
//
// What a PASS proves and does not prove is spelled out in app_face/voice/selftest.h. Short version:
// it proves the arithmetic and every policy decision from the workplan's decisions table. It
// proves nothing about this bay's noise floor with the drive motors running, which is a bench
// measurement the workplan requires before anyone trusts a threshold.
//
// Not part of the device build: tools/ is never synced into the demo tree.
#include <cstdio>
#include "voice/selftest.h"

static void sink(const char* line)
{
    std::printf("%s\n", line);
}

int main()
{
    const bool ok = voiceSelfTest(sink);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
