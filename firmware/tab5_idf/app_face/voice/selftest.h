// A TEST THAT NEEDS NOBODY AT THE BENCH -- and in this case nobody at the robot either.
//
// CLAUDE.md: "A test that needs a human at the bench is not a test. Nobody is usually there, and
// 'no stimulus' reads identically to 'broken hardware'. Make the firmware produce its own stimulus
// and its own verdict." The voice layer is the sharpest case of that rule in the repo, because the
// FIRST mic result was thrown away for exactly this failure: the only stimulus was a person making
// a noise, the measurement ran while nobody was at the bench, and a quiet room was duly misread as
// a dead chip.
//
// So the stimulus here is synthesised -- sine blocks at chosen per-channel amplitudes, fed through
// the identical level.cpp and listen.cpp that run on the panel.
//
// HOST ONLY, and that is a departure from percept/selftest.h, which also runs on the device behind
// OPENDUBS_DBG_PERCEPT. The reason is that percept's on-device verdict tells you something the host
// cannot: that the camera pipeline is wired up. The voice layer's on-device question is different
// and is already answered better by `listenStatus()` over UDP, which reports the REAL measured
// floor with the motors running -- which is the number that actually matters and which no synthetic
// stimulus can produce. So on the device this translation unit is absent rather than merely quiet,
// and nothing on the boot path calls it.
//
// WHAT A PASS PROVES: the RMS arithmetic, the floor tracker's asymmetry, the direction thresholds
// and their floor gate, the push-to-talk gate, the onset debounce, the hangover, the
// pause-is-not-the-end rule, the release paths, the half-duplex mute, and wrap safety across the
// 32-bit millisecond rollover.
//
// WHAT IT CANNOT PROVE: whether 1.6x over the floor is the right margin for THIS bay with the
// drive motors running, whether 600 ms of hangover truncates the way people actually speak, or
// whether the two channels are the way round we think they are on this board. Those are bench
// observations and they are listed as open in notes/workplan-tab5-audio.md.
#pragma once

typedef void (*VoiceSelfTestSink)(const char* line);

// Prints a line per case through the sink, so a failure says WHICH assertion broke. Returns true
// on PASS. Allocates nothing.
bool voiceSelfTest(VoiceSelfTestSink sink);
