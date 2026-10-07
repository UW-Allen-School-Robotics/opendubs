#include "selftest.h"

#ifdef ESP_PLATFORM

// DEVICE BUILD: absent, not merely disabled. See selftest.h for why the voice layer differs from
// percept here. This stub exists only so a caller added later still links.
bool voiceSelfTest(VoiceSelfTestSink)
{
    return false;
}

#else

#include "level.h"
#include "listen.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

namespace {

VoiceSelfTestSink g_sink = nullptr;

void say(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void say(const char* fmt, ...)
{
    if (!g_sink) return;
    char line[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    g_sink(line);
}

bool fail(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
bool fail(const char* fmt, ...)
{
    char line[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    say("  FAIL: %s", line);
    return false;
}

// ---- synthetic stimulus ----------------------------------------------------------------------
// A 32 ms block at 16 kHz, matching what bench_mic actually recorded (512 frames). Deterministic:
// a sine at a fixed phase, so the same input every run means a failure is a regression and not a
// dice roll.
constexpr int kRate   = 16000;
constexpr int kFrames = 512;

int16_t g_blk[kFrames * 2];

// `ampL`/`ampR` are int16 peak amplitudes, so the RMS of each channel is amp/sqrt(2).
const int16_t* block(float ampL, float ampR)
{
    for (int i = 0; i < kFrames; ++i) {
        const float ph = 6.2831853f * 440.0f * (float)i / (float)kRate;
        const float s  = sinf(ph);
        g_blk[2 * i]     = (int16_t)(s * ampL);
        g_blk[2 * i + 1] = (int16_t)(s * ampR);
    }
    return g_blk;
}

bool near(float a, float b, float tol)
{
    return fabsf(a - b) <= tol;
}

// Walk the machine forward in 32 ms steps, feeding one block per step, and report the FIRST
// non-None event. Returns None if none fired in `blocks` steps. `now` is advanced in place so a
// test can keep a continuous timeline across calls.
VoiceEvent feed(uint32_t* now, float ampL, float ampR, int blocks)
{
    VoiceEvent first = VoiceEvent::None;
    for (int i = 0; i < blocks; ++i) {
        const VoiceEvent e = listenBlock(*now, block(ampL, ampR), kFrames);
        if (e != VoiceEvent::None && first == VoiceEvent::None) first = e;
        *now += 32;
    }
    return first;
}

// Bring the floor to a believable, quiet value: Idle, not playing, quiet blocks until valid.
// 40 blocks is comfortably past the 24-block warm-up in level.cpp.
void warmFloor(uint32_t* now, float amp = 60.0f)
{
    for (int i = 0; i < 40; ++i) {
        listenBlock(*now, block(amp, amp), kFrames);
        *now += 32;
    }
}

// ---- cases -----------------------------------------------------------------------------------

bool caseRms()
{
    // RMS of a full-scale-ish sine is amp/sqrt(2). This is the one case that checks the arithmetic
    // rather than the policy, and it is first because every other case is read through it.
    const VoiceLevel v = voiceLevel(block(10000.0f, 5000.0f), kFrames);
    const float expL = 10000.0f / 1.41421356f;
    const float expR = 5000.0f / 1.41421356f;
    if (!near(v.l, expL, expL * 0.02f)) return fail("rms L %.0f, expected ~%.0f", v.l, expL);
    if (!near(v.r, expR, expR * 0.02f)) return fail("rms R %.0f, expected ~%.0f", v.r, expR);

    const VoiceLevel z = voiceLevel(block(0.0f, 0.0f), kFrames);
    if (z.mean() > 1.0f) return fail("silence read %.2f, expected ~0", z.mean());

    // A refusal to measure must not look like silence.
    const VoiceLevel n = voiceLevel(nullptr, kFrames);
    if (n.l != 0.0f || n.r != 0.0f) return fail("null block did not return zero");
    say("  ok   rms: sine amp/sqrt2 both channels, silence ~0, null safe");
    return true;
}

bool caseFloorFailsClosed()
{
    VoiceFloor f;
    voiceFloorReset(&f);
    if (f.valid) return fail("a reset floor claims to be valid");

    // UN-MEASURED MUST FAIL CLOSED. An un-measured threshold that failed open would have the robot
    // reacting to its own fans on the first block after boot.
    const float thr = voiceFloorThreshold(f);
    if (thr < 3000.0f) return fail("un-measured threshold %.0f is low enough to trigger", thr);

    const VoiceLevel quiet = voiceLevel(block(90.0f, 90.0f), kFrames);
    if (voiceDirection(quiet, f) != VoiceDir::None)
        return fail("a quiet block got a direction before the floor was measured");

    for (int i = 0; i < 24; ++i) voiceFloorUpdate(&f, quiet);
    if (!f.valid) return fail("floor still invalid after 24 blocks");
    const float measured = voiceFloorThreshold(f);
    if (!near(measured, quiet.mean() * 1.6f, quiet.mean() * 0.2f))
        return fail("measured threshold %.0f, expected ~%.0f", measured, quiet.mean() * 1.6f);
    say("  ok   floor: fails CLOSED un-measured (thr %.0f), then tracks quiet to thr %.0f",
        (double)thr, (double)measured);
    return true;
}

bool caseFloorAsymmetry()
{
    // RISES SLOWLY, FALLS FAST -- the property that lets a sentence not mute itself, and lets the
    // robot regain sensitivity quickly when the drive motors stop. Checked as a RELATION between
    // the two rates, not against a coefficient, so retuning the constants cannot silently invert
    // the design.
    const VoiceLevel quiet = voiceLevel(block(100.0f, 100.0f), kFrames);
    const VoiceLevel loud  = voiceLevel(block(8000.0f, 8000.0f), kFrames);

    VoiceFloor up;
    voiceFloorReset(&up);
    for (int i = 0; i < 30; ++i) voiceFloorUpdate(&up, quiet);
    const float base = up.l;
    for (int i = 0; i < 10; ++i) voiceFloorUpdate(&up, loud);
    const float rose = (up.l - base) / (loud.l - base);

    VoiceFloor down;
    voiceFloorReset(&down);
    for (int i = 0; i < 30; ++i) voiceFloorUpdate(&down, loud);
    const float high = down.l;
    for (int i = 0; i < 10; ++i) voiceFloorUpdate(&down, quiet);
    const float fell = (high - down.l) / (high - quiet.l);

    if (fell <= rose)
        return fail("floor falls (%.2f) no faster than it rises (%.2f) over 10 blocks", fell, rose);
    if (rose > 0.5f)
        return fail("floor rose %.0f%% toward a loud block in 10 blocks -- speech will mute itself",
                    rose * 100.0f);
    say("  ok   floor asymmetry: 10 blocks move it %.0f%% up but %.0f%% down",
        (double)(rose * 100.0f), (double)(fell * 100.0f));
    return true;
}

bool caseDirection()
{
    VoiceFloor f;
    voiceFloorReset(&f);
    const VoiceLevel quiet = voiceLevel(block(80.0f, 80.0f), kFrames);
    for (int i = 0; i < 30; ++i) voiceFloorUpdate(&f, quiet);

    struct Case { float l, r; VoiceDir want; const char* what; };
    const Case cs[] = {
        {6000.0f, 2000.0f, VoiceDir::Left,   "louder left"},
        {2000.0f, 6000.0f, VoiceDir::Right,  "louder right"},
        {4000.0f, 4000.0f, VoiceDir::Centre, "equal"},
        {  80.0f,   80.0f, VoiceDir::None,   "at the floor"},
        {  10.0f, 2000.0f, VoiceDir::Right,  "quiet but asymmetric and above the floor"},
    };
    for (const auto& c : cs) {
        const VoiceDir got = voiceDirection(voiceLevel(block(c.l, c.r), kFrames), f);
        if (got != c.want)
            return fail("direction(%s): wanted %s, got %s",
                        c.what, voiceDirName(c.want), voiceDirName(got));
    }

    // THE RULE THE FIRST MIC RESULT BROKE: two near-zero channels have a ratio too, and it is
    // noise. A lopsided pair BELOW the floor is still NONE, never a direction.
    const VoiceDir sub = voiceDirection(voiceLevel(block(1.0f, 40.0f), kFrames), f);
    if (sub != VoiceDir::None)
        return fail("a lopsided pair below the floor reported %s, not NONE", voiceDirName(sub));
    say("  ok   direction: left/right/centre resolve; below the floor is NONE, not a direction");
    return true;
}

bool casePushToTalkGate()
{
    // PUSH-TO-TALK IS THE PRIVACY GUARANTEE, so it gets an assertion and not just a comment: with
    // no press held, no amount of noise may produce an event.
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    const VoiceEvent e = feed(&now, 9000.0f, 9000.0f, 30);
    if (e != VoiceEvent::None)
        return fail("a shout with no press held produced %s", voiceEventName(e));
    if (listenState() != ListenState::Idle)
        return fail("state left Idle with no press held");
    say("  ok   push-to-talk: 30 loud blocks with no press produced no event");
    return true;
}

bool caseOnsetDebounce()
{
    // One relay click must not fire the `Heard` earcon; three blocks of speech must.
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);

    if (feed(&now, 9000.0f, 9000.0f, 2) != VoiceEvent::None)
        return fail("two loud blocks fired an event -- the onset debounce is not holding");
    if (listenState() != ListenState::Waiting)
        return fail("state is not Waiting after two loud blocks");

    if (feed(&now, 9000.0f, 9000.0f, 1) != VoiceEvent::Heard)
        return fail("the third loud block did not produce Heard");
    if (listenState() != ListenState::Speech)
        return fail("state is not Speech after onset");

    // And Heard fires ONCE per utterance, not per block.
    if (feed(&now, 9000.0f, 9000.0f, 5) != VoiceEvent::None)
        return fail("Heard re-fired during speech");
    say("  ok   onset: 2 blocks silent, the 3rd fires Heard once (~96 ms, inside the promise)");
    return true;
}

bool caseHangoverAndPause()
{
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::Heard) return fail("no onset");

    // A PAUSE IS NOT THE END. Quiet for well under the hangover, then speech again.
    if (feed(&now, 70.0f, 70.0f, 10) != VoiceEvent::None)
        return fail("a 320 ms pause ended the utterance -- the hangover is not holding");
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::None)
        return fail("resuming speech after a pause produced an event");
    if (listenState() != ListenState::Speech)
        return fail("state is not Speech after resuming");

    // Past the hangover, it ends -- exactly once.
    const VoiceEvent e = feed(&now, 70.0f, 70.0f, 30);
    if (e != VoiceEvent::UtteranceEnd)
        return fail("silence past the hangover produced %s, not UtteranceEnd", voiceEventName(e));
    if (listenState() != ListenState::Idle)
        return fail("state is not Idle after the utterance ended");
    if (feed(&now, 70.0f, 70.0f, 10) != VoiceEvent::None)
        return fail("UtteranceEnd fired a second time");
    say("  ok   hangover: a 320 ms pause survives, 600 ms ends the utterance exactly once");
    return true;
}

bool casePeakDirection()
{
    // THE DIRECTION OF AN UTTERANCE IS ITS LOUDEST BLOCK, NOT ITS LAST. The tail of a sentence is
    // its quietest part and its channel ratio is the least trustworthy thing in the clip, so a
    // glance aimed from the last block would be aimed from the worst available evidence.
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 9000.0f, 2000.0f, 4) != VoiceEvent::Heard) return fail("no onset");
    feed(&now, 900.0f, 2600.0f, 3);                 // a quieter tail leaning the other way
    if (feed(&now, 70.0f, 70.0f, 30) != VoiceEvent::UtteranceEnd)
        return fail("the utterance did not end");
    if (listenDir() != VoiceDir::Left)
        return fail("utterance direction is %s; the peak block was LEFT", voiceDirName(listenDir()));
    say("  ok   glance direction: taken from the PEAK block (LEFT), not the quieter tail");
    return true;
}

bool caseRelease()
{
    // Release during speech ends the clip at once -- the person said they were done.
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::Heard) return fail("no onset");
    listenRelease(now);
    if (listenTick(now) != VoiceEvent::UtteranceEnd)
        return fail("release during speech did not end the utterance");

    // Release with nothing ever heard is NO_SPEECH, which must be distinguishable from a clip:
    // a failure to HEAR must not reach the Thor looking like a failure to UNDERSTAND.
    listenBegin();
    now = 1000;
    warmFloor(&now);
    listenPress(now);
    feed(&now, 70.0f, 70.0f, 5);
    listenRelease(now);
    const VoiceEvent e = listenTick(now);
    if (e != VoiceEvent::NoSpeech)
        return fail("release after silence produced %s, not NoSpeech", voiceEventName(e));

    // And a held press with nobody talking must resolve on its own, or the floor never updates
    // again.
    listenBegin();
    now = 1000;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 70.0f, 70.0f, 300) != VoiceEvent::NoSpeech)
        return fail("a press held over the 8 s timeout never resolved");
    say("  ok   release: ends a clip at once; silence gives NO_SPEECH; a stuck press times out");
    return true;
}

bool caseHalfDuplex()
{
    // THE HALF-DUPLEX GATE, which is the price of having no AEC. While the speaker is active, the
    // robot is deaf by design -- so a shout during playback must produce nothing at all.
    listenBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);
    listenSetPlaying(now, true);
    if (feed(&now, 12000.0f, 12000.0f, 30) != VoiceEvent::None)
        return fail("a shout during playback produced an event -- capture is not muted");
    if (listenState() != ListenState::Waiting)
        return fail("state advanced during playback");

    // And the floor must not learn the robot's own voice as the room, during playback OR in its
    // reverberant tail.
    listenBegin();
    now = 1000;
    warmFloor(&now, 60.0f);
    const float before = listenFloor().l;
    listenSetPlaying(now, true);
    feed(&now, 12000.0f, 12000.0f, 40);
    listenSetPlaying(now, false);
    feed(&now, 12000.0f, 12000.0f, 4);          // 128 ms of tail, inside the 250 ms suppression
    const float after = listenFloor().l;
    if (after > before * 1.2f)
        return fail("the floor learned playback: %.0f -> %.0f", before, after);
    say("  ok   half-duplex: deaf while playing, and the floor ignores playback and its tail");
    return true;
}

bool caseWrap()
{
    // WRAP SAFETY. AUDIT.md invariant 5, and cues.cpp shipped this bug twice. Start the hangover
    // just before the 32-bit millisecond rollover at ~49 days of uptime and make it still expire.
    listenBegin();
    uint32_t now = 0xFFFFF000u;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::Heard)
        return fail("no onset across the wrap");
    const VoiceEvent e = feed(&now, 70.0f, 70.0f, 40);
    if (e != VoiceEvent::UtteranceEnd)
        return fail("the hangover did not expire across the millisecond wrap (got %s)",
                    voiceEventName(e));
    say("  ok   wrap: the hangover expires across the 32-bit millisecond rollover");
    return true;
}

} // namespace

bool voiceSelfTest(VoiceSelfTestSink sink)
{
    g_sink = sink;
    say("voice self-test: %d-frame blocks at %d Hz (%d ms)", kFrames, kRate, kFrames * 1000 / kRate);

    struct Case { bool (*fn)(); const char* name; };
    const Case cases[] = {
        {caseRms,              "rms"},
        {caseFloorFailsClosed, "floor fails closed"},
        {caseFloorAsymmetry,   "floor asymmetry"},
        {caseDirection,        "direction"},
        {casePushToTalkGate,   "push-to-talk gate"},
        {caseOnsetDebounce,    "onset debounce"},
        {caseHangoverAndPause, "hangover and pause"},
        {casePeakDirection,    "glance direction"},
        {caseRelease,          "release paths"},
        {caseHalfDuplex,       "half-duplex"},
        {caseWrap,             "wrap safety"},
    };

    bool ok = true;
    for (const auto& c : cases) {
        if (!c.fn()) {
            say("  (stopped at: %s)", c.name);
            ok = false;
            break;
        }
    }
    return ok;
}

#endif
