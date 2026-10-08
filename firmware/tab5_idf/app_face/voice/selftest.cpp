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
#include "glance.h"
#include "resample.h"
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
// A 32 ms block in THE DEVICE'S REAL CAPTURE FORMAT: 48 kHz, four interleaved channels,
// [MIC-L, AEC, MIC-R, MIC-HP]. Not the Arduino bench's 16 kHz stereo -- see level.h. The test
// builds what audioRecord() actually hands back, because a test that feeds a format the device
// never produces proves nothing about the device.
//
// Deterministic: a sine at a fixed phase, so the same input every run means a failure is a
// regression and not a dice roll.
constexpr int kFrames = voiceFramesForMs(32);   // 1536 at 48 kHz

int16_t g_blk[kFrames * kVoiceChans];

// THE DECOY, and it is the point of filling these slots at all. The AEC and headset slots are
// loaded with a loud, OPPOSITELY-leaning signal that no microphone produced. If voiceLevel() ever
// reverts to reading the buffer as stride-2 stereo -- the single most plausible regression in this
// file, because it is what the bench did and what every reader will assume -- it picks up this
// decoy instead of MIC-R and the direction cases fail loudly. A zero-filled slot would have let
// that bug pass.
constexpr float kDecoyAmp = 14000.0f;

// `ampL`/`ampR` are int16 peak amplitudes, so the RMS of each channel is amp/sqrt(2).
const int16_t* block(float ampL, float ampR)
{
    for (int i = 0; i < kFrames; ++i) {
        const float ph = 6.2831853f * 440.0f * (float)i / (float)kVoiceRate;
        const float s  = sinf(ph);
        int16_t* f = &g_blk[kVoiceChans * i];
        f[kVoiceMicL]   = (int16_t)(s * ampL);
        f[kVoiceMicR]   = (int16_t)(s * ampR);
        // Powered down on the real board; here, deliberately wrong. See kDecoyAmp.
        f[kVoiceAecRef] = (int16_t)(s * kDecoyAmp);
        f[kVoiceMicHp]  = (int16_t)(-s * kDecoyAmp);
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

bool caseColdBootPress()
{
    // THE CASE THAT CAUGHT A REAL BUG, and it is here because the host test did not model the
    // device faithfully enough to find it on its own: every other case calls warmFloor(), which
    // feeds idle blocks. A REAL ROBOT NEVER DOES THAT. Push-to-talk means nothing is captured
    // while idle -- that is the privacy guarantee -- so no idle block ever arrives, and a floor
    // learned only in Idle is never learned at all.
    //
    // Both cases below therefore use NO idle capture anywhere, which is what the robot does.

    // (a) THE DELIBERATE SPEAKER: presses, gathers themselves, then talks. The pause is the
    //     calibration, and it costs nobody any privacy because the button is already held.
    listenBegin();
    uint32_t now = 1000;
    if (listenFloor().valid) return fail("a fresh floor claims to be valid");
    const uint32_t pressedAt = now;
    listenPress(now);

    // Room noise must NOT read as speech while the floor is un-measured: the threshold fails
    // closed at 4000 precisely so that it cannot.
    int blocks = 0;
    while (!listenFloor().valid && blocks < 60) {
        if (listenBlock(now, block(70.0f, 70.0f), kFrames) != VoiceEvent::None)
            return fail("room noise during cold-boot calibration produced an event");
        now += 32;
        ++blocks;
    }
    if (!listenFloor().valid)
        return fail("the floor never became valid during a press -- it is never learned at all");

    const VoiceEvent e = feed(&now, 9000.0f, 9000.0f, 3);
    if (e != VoiceEvent::Heard)
        return fail("speech after cold-boot calibration produced %s", voiceEventName(e));
    const uint32_t elapsed = now - pressedAt;
    if (elapsed > 1000)
        return fail("cold-boot press took %u ms to say Heard -- the promise is inside a second",
                    (unsigned)elapsed);

    // (b) THE IMPATIENT SPEAKER: presses and talks at once, so there is no quiet head to
    //     calibrate from. Real speech still beats the fails-closed threshold, so they are heard
    //     immediately -- and the floor is then measured from the silence AFTER they stop, which
    //     is why the tail counts as quiet too.
    listenBegin();
    now = 1000;
    listenPress(now);
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::Heard)
        return fail("an immediate speaker was not heard on a cold floor");
    if (listenFloor().valid) return fail("the floor cannot be valid yet -- nothing quiet was fed");
    if (feed(&now, 70.0f, 70.0f, 40) != VoiceEvent::UtteranceEnd)
        return fail("the immediate speaker's utterance did not end");
    if (!listenFloor().valid)
        return fail("the floor was not learned from the tail after an immediate speaker");

    say("  ok   cold boot: a press self-calibrates with NO idle capture (Heard at %u ms); "
        "an immediate speaker is heard anyway and calibrates from the tail", (unsigned)elapsed);
    return true;
}

bool caseDeadMic()
{
    // A DEAD MICROPHONE MUST READ AS SILENCE, NOT AS CONTINUOUS SPEECH.
    //
    // This case exists because the bug was real and the arithmetic made it inevitable: all-zero
    // samples give a measured floor of 0, a threshold of 0, and `level >= 0` is true for every
    // block -- so the robot would fire `Heard` on the first press and never end the utterance.
    // Found by compiling the shim for the desktop twin, whose audioRecord() stub leaves the buffer
    // zeroed, which is precisely what a failed i2s_read looks like on the device.
    listenBegin();
    uint32_t now = 1000;
    listenPress(now);
    for (int i = 0; i < 200; ++i) {
        const VoiceEvent e = listenBlock(now, block(0.0f, 0.0f), kFrames);
        if (e == VoiceEvent::Heard)
            return fail("a DEAD MIC (all zeros) produced Heard at block %d", i);
        if (e == VoiceEvent::UtteranceEnd)
            return fail("a dead mic produced an utterance at block %d", i);
        now += 32;
    }
    if (voiceFloorThreshold(listenFloor()) <= 0.0f)
        return fail("the threshold collapsed to %.1f on a silent input",
                    (double)voiceFloorThreshold(listenFloor()));

    // And the floor must still be usable afterwards: real speech arriving later IS heard.
    if (feed(&now, 9000.0f, 9000.0f, 3) != VoiceEvent::Heard)
        return fail("speech after a silent stretch was not heard");
    say("  ok   dead mic: all-zero capture reads as SILENCE (thr %.0f), and real speech still lands",
        (double)voiceFloorThreshold(listenFloor()));
    return true;
}

bool caseGlanceRefusals()
{
    // THE REFUSALS ARE THE DESIGN, so they are asserted rather than commented.
    glanceBegin();
    uint32_t now = 1000;

    if (glanceTrigger(now, VoiceDir::None))
        return fail("glanced at a NONE bearing -- 'no stimulus' became a direction");
    // A centre bearing must not move the head: the earcon is the whole acknowledgement, and
    // turning toward something you already face reads as a twitch.
    if (glanceTrigger(now, VoiceDir::Centre))
        return fail("glanced at a CENTRE bearing -- the head should not move");
    if (glanceActive(now))
        return fail("a refused trigger left a glance active");

    if (!glanceTrigger(now, VoiceDir::Left))
        return fail("a LEFT bearing did not start a glance");
    if (!glanceActive(now))
        return fail("the glance is not active immediately after starting");
    if (glanceNx() >= 0.0f)
        return fail("a LEFT glance aims nx=%+.2f -- wrong side", (double)glanceNx());
    if (glanceNy() != 0.0f)
        return fail("a glance asked for tilt (%+.2f) from two side-by-side mics", (double)glanceNy());

    glanceBegin();
    if (!glanceTrigger(now, VoiceDir::Right) || glanceNx() <= 0.0f)
        return fail("a RIGHT glance aims nx=%+.2f -- wrong side", (double)glanceNx());
    say("  ok   glance refusals: NONE and CENTRE move nothing; left/right aim opposite, tilt is 0");
    return true;
}

bool caseGlanceEndsItself()
{
    // A GLANCE ENDS BY ITSELF AND CANNOT BE HELD OPEN. That is the entire difference between a
    // glance and a tracker, and it is what lets this take no lease: forgetting to release it is
    // not a failure mode, because nothing can refresh the deadline.
    glanceBegin();
    uint32_t now = 1000;
    if (!glanceTrigger(now, VoiceDir::Left)) return fail("no glance");

    // Hammering the trigger must NOT extend it -- this is the accidental-tracker path.
    // The cap is 3 s of re-triggering, comfortably past any hold worth calling a glance: a loop
    // that stops before the hold would prove nothing, which it did on the first attempt here.
    for (int i = 0; i < 150; ++i) {
        glanceTrigger(now, VoiceDir::Left);
        now += 20;
        if (!glanceActive(now)) break;
    }
    if (glanceActive(now))
        return fail("a glance survived %u ms of re-triggering -- it extends, so it is a tracker",
                    (unsigned)(now - 1000));
    const uint32_t held = now - 1000;
    if (held > 1200)
        return fail("the glance held %u ms -- that is a stare, not a glance", (unsigned)held);
    say("  ok   glance ends itself: %u ms under continuous re-triggering, never extended",
        (unsigned)held);
    return true;
}

bool caseGlanceIsNotATracker()
{
    // THE ONE THAT MATTERS, and it already changed the implementation once.
    //
    // design/hri-interaction-design.md forbids continuous voice tracking and wants "multiple short
    // fixations". Those pull in opposite directions, so this measures the DUTY CYCLE under a
    // speaker who never stops -- and the first version of glance.cpp, with a single 600 ms gap,
    // scored 60%: the head swinging over and back and over again to re-aim at the place it was
    // already aiming. A tic, not a fixation. Hence the settle/change split.
    //
    // (a) A STATIC SPEAKER WHO NEVER STOPS: one glance, then stillness.
    glanceBegin();
    uint32_t now = 1000;
    int steering = 0;
    const int frames = 1500;            // 30 s at 20 ms
    for (int i = 0; i < frames; ++i) {
        glanceTrigger(now, VoiceDir::Left);
        if (glanceActive(now)) ++steering;
        now += 20;
    }
    const float duty = (float)steering / (float)frames;
    if (duty > 0.35f)
        return fail("a static non-stop voice steers the head %.0f%% of the time -- that is a tic",
                    (double)(duty * 100.0f));
    if (duty < 0.10f)
        return fail("the head steered only %.0f%% of the time -- glances are suppressed entirely",
                    (double)(duty * 100.0f));

    // (b) A BEARING THAT CHANGES IS NEW INFORMATION, and must get a prompt glance -- otherwise
    //     someone crossing the room, or a second person speaking, is ignored for 2.5 s.
    glanceBegin();
    now = 1000;
    if (!glanceTrigger(now, VoiceDir::Left)) return fail("no first glance");
    while (glanceActive(now)) now += 20;            // let it end on its own
    const uint32_t endedAt = now;

    // The same bearing is refused through the settle window...
    if (glanceTrigger(now + 1000, VoiceDir::Left))
        return fail("the SAME bearing re-glanced after 1 s -- the settle window is not holding");
    // ...while the other side is taken promptly.
    glanceBegin();
    now = 1000;
    if (!glanceTrigger(now, VoiceDir::Left)) return fail("no first glance");
    while (glanceActive(now)) now += 20;
    if (!glanceTrigger(now + 700, VoiceDir::Right))
        return fail("a CHANGED bearing was refused 700 ms after the last glance");
    if (glanceNx() <= 0.0f)
        return fail("the re-glance did not aim at the new side (nx %+.2f)", (double)glanceNx());
    (void)endedAt;
    say("  ok   not a tracker: a static non-stop voice steers %.0f%% of the time; a CHANGED "
        "bearing re-glances promptly", (double)(duty * 100.0f));
    return true;
}

bool caseGlanceFromListen()
{
    // END TO END over the real state machine: a press, speech from the left, and the bearing that
    // reaches the glance is the ONSET bearing -- because the design's build order says acknowledge
    // inside one second, which is before the peak is known.
    listenBegin();
    glanceBegin();
    uint32_t now = 1000;
    warmFloor(&now);
    listenPress(now);
    if (feed(&now, 9000.0f, 2000.0f, 3) != VoiceEvent::Heard) return fail("no onset");
    if (listenDirNow() != VoiceDir::Left)
        return fail("the onset bearing is %s, expected LEFT", voiceDirName(listenDirNow()));
    if (!glanceTrigger(now, listenDirNow()))
        return fail("the onset bearing did not start a glance");
    if (glanceNx() >= 0.0f) return fail("glanced to the wrong side at onset");

    // A finished utterance must not aim the NEXT glance: a stale bearing pointing the head at
    // where the last person stood is worse than not glancing at all.
    if (feed(&now, 70.0f, 70.0f, 40) != VoiceEvent::UtteranceEnd)
        return fail("the utterance did not end");
    if (listenDirNow() != VoiceDir::None)
        return fail("the live bearing survived the utterance as %s", voiceDirName(listenDirNow()));
    say("  ok   end to end: onset bearing drives the glance, and it is cleared when the clip ends");
    return true;
}

// A tone at `hz` into the device's real 4-channel 48 kHz layout, same amplitude both mics.
void toneBlock(int16_t* buf, int frames, float hz, float amp, int startFrame)
{
    for (int i = 0; i < frames; ++i) {
        const float ph = 6.2831853f * hz * (float)(startFrame + i) / (float)kVoiceRate;
        const int16_t v = (int16_t)(sinf(ph) * amp);
        int16_t* f = &buf[kVoiceChans * i];
        f[kVoiceMicL]   = v;
        f[kVoiceMicR]   = v;
        f[kVoiceAecRef] = (int16_t)(sinf(ph) * kDecoyAmp);
        f[kVoiceMicHp]  = (int16_t)(-sinf(ph) * kDecoyAmp);
    }
}

// RMS of the resampler's output for a sustained tone, in STREAMING blocks, so the block-boundary
// handling is exercised rather than bypassed.
float resampleToneRms(float hz)
{
    static int16_t in[kFrames * kVoiceChans];
    static int16_t out[resampleMaxOut(kFrames)];
    Resampler r;
    resampleReset(&r);
    double acc = 0.0;
    long   n = 0;
    const int blocks = 20;
    for (int b = 0; b < blocks; ++b) {
        toneBlock(in, kFrames, hz, 12000.0f, b * kFrames);
        const int got = resampleBlock(&r, in, kFrames, out, (int)(sizeof(out) / sizeof(out[0])));
        // Skip the first block: the filter is still filling and its gain is not yet steady.
        if (b == 0) continue;
        for (int i = 0; i < got; ++i) { acc += (double)out[i] * out[i]; ++n; }
    }
    return n ? (float)sqrt(acc / (double)n) : 0.0f;
}

bool caseResampleRate()
{
    // 3:1, streaming, with the phase carried across blocks. A block of 1536 frames at 48 kHz must
    // give exactly 512 samples at 16 kHz, EVERY block -- if the phase resets per block the counts
    // stay right while the samples drift, so the count is necessary but not sufficient and the
    // aliasing case below is what really pins it.
    static int16_t in[kFrames * kVoiceChans];
    static int16_t out[resampleMaxOut(kFrames)];
    Resampler r;
    resampleReset(&r);
    for (int b = 0; b < 5; ++b) {
        toneBlock(in, kFrames, 1000.0f, 8000.0f, b * kFrames);
        const int got = resampleBlock(&r, in, kFrames, out, (int)(sizeof(out) / sizeof(out[0])));
        if (got != kFrames / kDecimate)
            return fail("block %d produced %d samples, expected %d", b, got, kFrames / kDecimate);
    }
    // And the mono mix must ignore the AEC/headset decoys, exactly as voiceLevel() must.
    const float pass = resampleToneRms(1000.0f);
    const float want = 12000.0f / 1.41421356f;
    if (pass < want * 0.9f || pass > want * 1.1f)
        return fail("a 1 kHz tone came through at %.0f rms, expected ~%.0f (decoy leakage?)",
                    (double)pass, (double)want);
    say("  ok   resample: %d frames -> %d samples per block, 1 kHz passes at unity",
        kFrames, kFrames / kDecimate);
    return true;
}

bool caseResampleOnset()
{
    // THE FIRST SAMPLE OF A CLIP MUST ALREADY BE RIGHT.
    //
    // This case exists because a mutation test caught the suite not having it: zero-priming the
    // filter history passed every other case, because the RMS helper skips the first block to let
    // the gain settle. But a clip STARTS at the onset of speech, so a filter ramping up from
    // silence over 14 samples puts a click exactly where the first phoneme is -- the one place it
    // cannot be tolerated.
    //
    // A constant input makes the difference stark: primed from the first sample, output[0] is that
    // value immediately; primed from zeros it is value*kFir[last]/sum, which is about 1/1000th.
    static int16_t in[kFrames * kVoiceChans];
    static int16_t out[resampleMaxOut(kFrames)];
    constexpr int16_t kDc = 8000;

    for (int i = 0; i < kFrames; ++i) {
        int16_t* f = &in[kVoiceChans * i];
        f[kVoiceMicL]   = kDc;
        f[kVoiceMicR]   = kDc;
        f[kVoiceAecRef] = (int16_t)-kDecoyAmp;    // decoys again: the mix must ignore them
        f[kVoiceMicHp]  = (int16_t)kDecoyAmp;
    }
    Resampler r;
    resampleReset(&r);
    const int got = resampleBlock(&r, in, kFrames, out, (int)(sizeof(out) / sizeof(out[0])));
    if (got <= 0) return fail("no output at all");
    if (out[0] < kDc * 9 / 10)
        return fail("the first sample of a clip is %d, expected ~%d -- the filter ramps up from "
                    "silence, which is a click on the first phoneme", out[0], (int)kDc);
    say("  ok   onset: the first sample of a clip is already %d (no ramp-up click)", out[0]);
    return true;
}

bool caseResampleAntiAlias()
{
    // THE CASE WITH TEETH, because the bug it guards is invisible by inspection: decimating 3:1
    // with no low-pass FOLDS everything above 8 kHz into the band. A 12 kHz ESC whine would land
    // at 4 kHz, on top of the vowels, and the audio would SOUND fine while transcribing badly.
    //
    // So: measure the stopband rather than trust the comment. 12 kHz folds to 4 kHz and 16 kHz
    // folds to DC; both must come out far below a passband tone of the same amplitude.
    const float ref = resampleToneRms(1000.0f);
    if (ref <= 1.0f) return fail("the reference tone did not survive at all (%.1f)", (double)ref);

    struct Case { float hz; float minAttenDb; const char* folds; };
    const Case cs[] = {
        {12000.0f, 24.0f, "4 kHz, mid-vowel"},
        {16000.0f, 30.0f, "DC"},
        {20000.0f, 24.0f, "4 kHz"},
    };
    for (const auto& c : cs) {
        const float got = resampleToneRms(c.hz);
        const float db  = 20.0f * log10f((got + 1e-6f) / ref);
        if (-db < c.minAttenDb)
            return fail("%.0f kHz attenuated only %.1f dB (needs %.0f) -- it folds to %s",
                        (double)(c.hz / 1000.0f), (double)-db, (double)c.minAttenDb, c.folds);
    }
    const float a12 = 20.0f * log10f((resampleToneRms(12000.0f) + 1e-6f) / ref);
    say("  ok   anti-alias: 12 kHz down %.0f dB before it can fold to 4 kHz", (double)-a12);
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
    say("voice self-test: %d-frame blocks at %d Hz, %d ch [L,AEC,R,HP] (%d ms, %d kB)",
        kFrames, kVoiceRate, kVoiceChans, kFrames * 1000 / kVoiceRate,
        (int)sizeof(g_blk) / 1024);

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
        {caseColdBootPress,    "cold boot press"},
        {caseDeadMic,          "dead mic"},
        {caseGlanceRefusals,   "glance refusals"},
        {caseGlanceEndsItself, "glance ends itself"},
        {caseGlanceIsNotATracker, "glance is not a tracker"},
        {caseGlanceFromListen, "glance from listen"},
        {caseResampleRate,     "resample rate"},
        {caseResampleOnset,    "resample onset"},
        {caseResampleAntiAlias,"anti-alias"},
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
