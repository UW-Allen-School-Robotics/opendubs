#include "listen.h"
#include "../shared/due.h"
#include <stdio.h>

namespace {

// ---- the tunables, and what each one actually costs if it is wrong --------------------------
//
// NAMED PLACEHOLDERS in the sense pantilt.h uses the term: defensible numbers that can only be
// judged against the robot's own noise floor WITH THE MOTORS RUNNING. The workplan is in capitals
// about this and it is right -- the drive motors, the lift and the ESCs are a metre from these
// microphones, so a figure from a quiet bench is fiction here.

// Consecutive blocks above the floor before speech is declared. Debounce, and the thing that keeps
// a single relay click or a lift start from firing the `Heard` earcon. At a 32 ms block, 3 blocks
// is ~96 ms -- below the "inside a second" promise the earcon makes, with room to spare.
constexpr int kOnsetBlocks = 3;

// How long the level may stay below the floor before the utterance is declared over. THIS IS THE
// TRUNCATION KNOB: shorter cuts a thoughtful pause in half, longer makes the robot slow to answer.
// 600 ms is roughly the pause people leave between clauses, which is the shortest gap that should
// NOT end a turn.
constexpr uint32_t kHangoverMs = 600;

// A press that produces no speech at all still has to resolve, or a held button with nobody
// talking leaves the machine waiting forever and the floor never updates again.
constexpr uint32_t kWaitTimeoutMs = 8000;

// Floor learning is suspended for this long after playback stops, so the room's reverberant tail
// of the robot's own voice is not folded in as the room.
constexpr uint32_t kPlayTailMs = 250;

struct State {
    ListenState st;
    bool        playing;
    uint32_t    quietFloorFrom;  // floor learning is suspended until this time (playback tail)

    // Release and the hangover both end an utterance, but a release can arrive between blocks.
    // Latching it means every event leaves through listenTick() and a caller that only polls
    // cannot miss one.
    bool        pendingEnd;
    bool        pendingNoSpeech;

    int         aboveRun;        // consecutive blocks above the floor
    uint32_t    belowSince;      // when the level last dropped below, for the hangover
    uint32_t    waitingSince;

    VoiceFloor  floor;
    VoiceLevel  peak;            // loudest block of the utterance in progress
    bool        havePeak;
    VoiceDir    lastDir;

    uint32_t    blocks;          // counters, for the status line
    uint32_t    utterances;
    uint32_t    discardedWhilePlaying;
};

State g;

// Ending an utterance is two places (hangover expiry, and release) and was two copies of this
// until the second one forgot to clear havePeak.
VoiceEvent finish()
{
    g.lastDir  = g.havePeak ? voiceDirection(g.peak, g.floor) : VoiceDir::None;
    g.st       = ListenState::Idle;
    g.aboveRun = 0;
    g.havePeak = false;
    ++g.utterances;
    return VoiceEvent::UtteranceEnd;
}

} // namespace

const char* voiceEventName(VoiceEvent e)
{
    switch (e) {
        case VoiceEvent::Heard:        return "HEARD";
        case VoiceEvent::UtteranceEnd: return "UTTERANCE_END";
        case VoiceEvent::NoSpeech:     return "NO_SPEECH";
        default:                       return "NONE";
    }
}

void listenBegin()
{
    g = State{};
    g.st = ListenState::Idle;
    g.lastDir = VoiceDir::None;
    voiceFloorReset(&g.floor);
}

void listenPress(uint32_t nowMs)
{
    if (g.st != ListenState::Idle) return;    // idempotent: a repeated press is not a new utterance
    g.st = ListenState::Waiting;
    g.aboveRun = 0;
    g.havePeak = false;
    g.waitingSince = nowMs;
}

void listenRelease(uint32_t nowMs)
{
    (void)nowMs;
    // The release itself does not emit -- listenTick() does, so that every event leaves through
    // one door and a caller that only polls cannot miss one.
    if (g.st == ListenState::Waiting) {
        g.st = ListenState::Idle;             // resolved as NoSpeech by the tick below
        g.aboveRun = 0;
        g.havePeak = false;
        g.lastDir = VoiceDir::None;
        g.pendingNoSpeech = true;
    } else if (g.st == ListenState::Speech || g.st == ListenState::Trailing) {
        g.pendingEnd = true;
    }
}

void listenSetPlaying(uint32_t nowMs, bool playing)
{
    if (playing == g.playing) return;
    g.playing = playing;
    // Suspend floor learning past the END of playback too, not just during it. The room's
    // reverberant tail of the robot's own voice is still the robot, and folding it in would teach
    // the floor that the bay is louder than it is -- which shows up later as a robot that has
    // gone deaf for no visible reason.
    //
    // It takes `nowMs` rather than reading a clock because nothing in this layer may touch the
    // HAL: shared/clock.h exists for percept and its timestamps are deliberately not comparable
    // with the face app's. The caller already has the right clock, so it passes it.
    if (!playing) g.quietFloorFrom = nowMs + kPlayTailMs;
}

VoiceEvent listenBlock(uint32_t nowMs, const int16_t* samples, int frames)
{
    ++g.blocks;

    // THE HALF-DUPLEX GATE. One branch, as advertised in listen.h.
    if (g.playing) {
        ++g.discardedWhilePlaying;
        return VoiceEvent::None;
    }

    const VoiceLevel v = voiceLevel(samples, frames);

    if (g.st == ListenState::Idle) {
        // The ONLY place the floor is learned: not in a press, and not during playback. See level.h
        // -- a floor that updates during speech chases the speech.
        if (due(nowMs, g.quietFloorFrom)) voiceFloorUpdate(&g.floor, v);
        return listenTick(nowMs);
    }

    const bool above = v.mean() >= voiceFloorThreshold(g.floor);

    if (above && (!g.havePeak || v.mean() > g.peak.mean())) {
        g.peak = v;
        g.havePeak = true;
    }

    switch (g.st) {
        case ListenState::Waiting:
            if (above && ++g.aboveRun >= kOnsetBlocks) {
                g.st = ListenState::Speech;
                return VoiceEvent::Heard;
            }
            if (!above) g.aboveRun = 0;
            break;

        case ListenState::Speech:
            if (!above) {
                g.st = ListenState::Trailing;
                g.belowSince = nowMs;
            }
            break;

        case ListenState::Trailing:
            if (above) g.st = ListenState::Speech;      // it was a pause, not the end
            break;

        default:
            break;
    }

    return listenTick(nowMs);
}

VoiceEvent listenTick(uint32_t nowMs)
{
    if (g.pendingEnd) {
        g.pendingEnd = false;
        return finish();
    }
    if (g.pendingNoSpeech) {
        g.pendingNoSpeech = false;
        return VoiceEvent::NoSpeech;
    }

    if (g.st == ListenState::Trailing && due(nowMs, g.belowSince + kHangoverMs)) {
        return finish();
    }
    if (g.st == ListenState::Waiting && due(nowMs, g.waitingSince + kWaitTimeoutMs)) {
        // A held button with nobody talking has to resolve, or the floor never updates again.
        g.st = ListenState::Idle;
        g.aboveRun = 0;
        g.havePeak = false;
        return VoiceEvent::NoSpeech;
    }
    return VoiceEvent::None;
}

ListenState listenState()        { return g.st; }
VoiceDir    listenDir()          { return g.lastDir; }
const VoiceFloor& listenFloor()  { return g.floor; }

const char* listenStatus()
{
    static char buf[160];
    static const char* kNames[] = {"idle", "waiting", "speech", "trailing"};
    snprintf(buf, sizeof(buf),
             "%s floor=%.0f/%.0f%s thr=%.0f blocks=%lu utt=%lu muted=%lu dir=%s",
             kNames[(int)g.st], g.floor.l, g.floor.r, g.floor.valid ? "" : "(warming)",
             (double)voiceFloorThreshold(g.floor),
             (unsigned long)g.blocks, (unsigned long)g.utterances,
             (unsigned long)g.discardedWhilePlaying, voiceDirName(g.lastDir));
    return buf;
}
