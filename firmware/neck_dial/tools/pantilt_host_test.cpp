// HOST HARNESS for the neck's control law. `./build.sh pantilt` compiles and runs this.
//
// It exists because the control law is PURE COMPUTE by construction -- no I2C, no NVS, no
// FreeRTOS -- so the identical translation unit that will run on the Dial compiles and runs on a
// laptop. That matters especially here: this was written with the Dial UNPLUGGED, and "it
// compiles" is not a result.
//
// WHAT A PASS MEANS, and what it does not. It proves the arbitration order, the leases (including
// the signed-age GAZE bug that was live on the robot), the hard bounds, the uncommissioned tilt
// fallback, the slew limit, the deadband, and that the head freezes rather than centres when told
// not to move. It proves NOTHING about this 8Servo's channel mapping, this robot's real tilt
// limits, or whether the sign conventions match the servos as wired -- those are bench
// observations and must not be inferred from a green run here.

#include <cstdio>
#include <cmath>
#include "../neck/pantilt_core.h"
#include "../neck/tilt_confirm.h"
#include "../neck/rc_frame.h"
#include <cstring>

using namespace neck;

static int failed = 0;
static void ok(bool cond, const char* what, const char* detail = "")
{
    printf("%s  %s%s%s\n", cond ? "ok  " : "FAIL", what, *detail ? "   " : "", detail);
    if (!cond) failed++;
}

// Run `ms` of ticks at the control period, with one set of inputs whose timestamps follow the clock.
//
// THE CLOCK NEVER STARTS AT ZERO HERE, and that is not fussiness. A timestamp of 0 means "never
// set" to the control law, so a harness that starts at t=0 and refreshes with `if (ms) ms = t`
// writes 0 on its first pass, which silently unsets the lease -- and the guard then keeps it unset
// for ever. The first version of this file did exactly that: the head sat at centre for every run,
// and the bound assertions all PASSED, because a head that never moves is inside every bound.
// Which owners are live is captured BEFORE the loop, for the same reason.
static void run(State& s, In in, const Limits& lim, uint32_t ms, bool keepFresh = true)
{
    const bool touch = in.touchMs != 0, rc = in.rcMs != 0, gaze = in.gazeMs != 0;
    const uint32_t t0 = in.nowMs ? in.nowMs : kPeriodMs;
    for (uint32_t t = t0; t <= t0 + ms; t += kPeriodMs) {
        in.nowMs = t;
        if (keepFresh) {
            if (touch) in.touchMs = t;
            if (rc)    in.rcMs    = t;
            if (gaze)  in.gazeMs  = t;
        }
        tick(s, in, lim);
    }
}

int main()
{
    // ---- 1. the uncommissioned robot gets a NARROW band, not the full sweep -------------------
    {
        Limits none = limitsFrom(false, 0, false, 0);
        ok(!none.known && std::fabs(none.tiltMin - 80.0f) < 1e-6 && std::fabs(none.tiltMax - 100.0f) < 1e-6,
           "an uncommissioned robot gets the narrow fallback band", "80..100");
        Limits bad = limitsFrom(true, 120, true, 60);          // inverted: not credible
        ok(!bad.known, "and so does one whose stored limits are inverted");
        Limits good = limitsFrom(true, 62, true, 118);
        ok(good.known && good.tiltMin == 62.0f && good.tiltMax == 118.0f,
           "a commissioned robot gets its measured limits", "62..118");
    }

    const Limits lim = limitsFrom(true, 62, true, 118);
    const Limits fb  = limitsFrom(false, 0, false, 0);

    // ---- 2. frozen, not centred, when motion is not allowed -----------------------------------
    {
        State s; s.pan = 150; s.tilt = 110; s.lastMs = 0;
        In in; in.nowMs = 0; in.motionAllowed = false;
        in.gazePan = kCentreDeg; in.gazeTilt = kCentreDeg; in.gazeMs = 1; in.gazeLease = kGazeHoldMs;
        run(s, in, lim, 2000);
        ok(s.pan == 150 && s.tilt == 110, "it freezes where it is rather than returning to centre",
           "still 150/110");
        ok(s.owner == Owner::None && !s.moving, "  and owns nothing while held");
    }

    // ---- 3. THE SIGNED-AGE GAZE LEASE ----------------------------------------------------------
    // The bug that was live on the robot: a GAZE stamped a few ms in the FUTURE of the frame it is
    // judged against made `nowMs - gazeMs` a small negative, which as unsigned is ~4.29e9 and never
    // less than the lease. The freshest possible command read as the most expired.
    {
        State s; s.lastMs = 0;
        In in; in.nowMs = 1000; in.motionAllowed = true;
        in.gazePan = 150; in.gazeTilt = 100;
        in.gazeMs = 1005;                 // stamped AFTER this frame's clock
        in.gazeLease = kGazeHoldMs;
        tick(s, in, lim);
        ok(s.owner != Owner::Gaze, "a GAZE stamped in the future does not own the head",
           "owner is not Gaze");
        // and the ordinary case still works
        State s2; s2.lastMs = 0;
        In in2 = in; in2.gazeMs = 990; in2.nowMs = 1000;
        tick(s2, in2, lim);
        ok(s2.owner == Owner::Gaze, "a GAZE 10 ms old does own it");
        State s3; s3.lastMs = 0;
        In in3 = in; in3.gazeMs = 100; in3.nowMs = 1000;    // 900 ms old, past the 600 ms lease
        tick(s3, in3, lim);
        ok(s3.owner != Owner::Gaze, "and an expired one does not");
    }

    // ---- 4. arbitration order: touch > RC > show > tracker ------------------------------------
    {
        In in; in.nowMs = 1000; in.motionAllowed = true;
        in.touchMs = 1000; in.touchPan = 10;
        in.rcMs = 1000;    in.rcPan = -10;
        in.gazeMs = 1000;  in.gazePan = 150;
        in.haveTarget = true; in.nx = 0.9f; in.confidence = 1.0f;
        State s; s.lastMs = 0; tick(s, in, lim);
        ok(s.owner == Owner::Touch, "touch outranks everything");

        In b = in; b.touchMs = 0; State s2; s2.lastMs = 0; tick(s2, b, lim);
        ok(s2.owner == Owner::Rc, "RC outranks a show and the tracker");

        In c = b; c.rcMs = 0; State s3; s3.lastMs = 0; tick(s3, c, lim);
        ok(s3.owner == Owner::Gaze, "a show outranks the tracker");

        In d = c; d.gazeMs = 0; State s4; s4.lastMs = 0; tick(s4, d, lim);
        ok(s4.owner == Owner::Track, "and the tracker has it when nobody else does");
    }

    // ---- 5. the tracker's gates: confidence, and eyes-only -------------------------------------
    {
        In in; in.nowMs = 1000; in.motionAllowed = true; in.haveTarget = true; in.nx = 0.9f;
        in.confidence = 0.4f;                       // below kHeadConfidence
        State s; s.lastMs = 0; tick(s, in, lim);
        ok(s.owner != Owner::Track, "a weak detection never turns the head");

        In e = in; e.confidence = 1.0f; e.nx = 0.1f;   // inside kEyeOnly
        State s2; s2.lastMs = 0; tick(s2, e, lim);
        ok(s2.owner != Owner::Track, "and a small offset is the eyes' business, not the head's");
    }

    // ---- 6. the slew limit, measured ------------------------------------------------------------
    // A target jump must not become a fast head movement near someone standing within reach.
    {
        State s; s.lastMs = 0;
        In in; in.nowMs = 0; in.motionAllowed = true;
        in.gazePan = 180; in.gazeTilt = kCentreDeg; in.gazeMs = 1; in.gazeLease = kGazeHoldMs;
        float worst = 0; float prev = s.pan;
        for (uint32_t t = 0; t <= 1000; t += kPeriodMs) {
            in.nowMs = t; in.gazeMs = t;
            if (tick(s, in, lim)) {
                const float rate = std::fabs(s.pan - prev) / (kPeriodMs / 1000.0f);
                if (t > 0 && rate > worst) worst = rate;
                prev = s.pan;
            }
        }
        char d[80]; snprintf(d, sizeof(d), "peak %.1f deg/s against a %.0f limit", worst, kSlewDps);
        ok(worst <= kSlewDps + 1.0f, "the head never slews faster than the limit", d);
        ok(s.pan > 150, "  and it does get there", "");
    }

    // ---- 7. hard bounds, including the fallback band --------------------------------------------
    {
        State s; s.lastMs = 0;
        In in; in.nowMs = 0; in.motionAllowed = true;
        in.gazePan = 400; in.gazeTilt = 400; in.gazeMs = 1; in.gazeLease = kGazeHoldMs;
        run(s, in, lim, 4000);
        char d[80]; snprintf(d, sizeof(d), "pan %.0f tilt %.0f", s.pan, s.tilt);
        // IT MUST HAVE MOVED, or the bound below proves nothing: a head sitting at centre is
        // inside every limit there is. This is the assertion whose absence hid the harness bug.
        ok(s.pan > kCentreDeg + 10.0f, "a wild target actually drives the head", d);
        ok(s.pan <= kRcPanMax && s.tilt <= lim.tiltMax, "and it is clamped to the bounds", d);

        // the same target against an UNCOMMISSIONED robot must stop in the narrow band
        State s2; s2.lastMs = 0;
        run(s2, in, fb, 4000);
        char d2[80]; snprintf(d2, sizeof(d2), "tilt %.1f, band %.0f..%.0f", s2.tilt, fb.tiltMin, fb.tiltMax);
        ok(s2.tilt > kCentreDeg + 5.0f, "an uncommissioned robot still moves", d2);
        ok(s2.tilt <= fb.tiltMax + 1e-3f, "  but stops inside its narrow band", d2);
        ok(s2.tilt < lim.tiltMax, "  which is tighter than a commissioned one's", "");
    }

    // ---- 8. RC integrates a rate and is bounded, and its lease expires --------------------------
    {
        State s; s.lastMs = 0;
        In in; in.nowMs = 0; in.motionAllowed = true; in.rcMs = 1; in.rcTilt = 200;  // slam it up
        run(s, in, lim, 2000);
        char d[64]; snprintf(d, sizeof(d), "tilt %.1f, max %.0f", s.tilt, lim.tiltMax);
        ok(s.tilt > kCentreDeg + 5.0f, "an RC rate actually moves the head", d);
        ok(s.tilt <= lim.tiltMax + 1e-3f, "  and cannot drive past the tilt limit", d);

        // let go: ownership returns after the lease
        In idle; idle.nowMs = 5000; idle.motionAllowed = true; idle.rcMs = 1;  // stale
        State s2 = s; s2.lastMs = 4000; tick(s2, idle, lim);
        ok(s2.owner != Owner::Rc, "and letting go of the stick hands the head back");
    }

    // ---- 9. the StamPLC's 0x107 rate frame ------------------------------------------------------
    // Byte-for-byte the Tab5's format, because the StamPLC is unchanged and must stay so. It shares
    // a UDP port with the text protocol, so the length/id/dlc triple is the ONLY thing telling a
    // rate frame from a command -- and acting on a text line as if it were a rate would move the
    // head. Every rejection below is therefore a safety check, not tidiness.
    {
        auto frame = [](uint32_t id, uint8_t dlc, int16_t pan, int16_t tilt, uint8_t* b) {
            b[0] = id & 0xff; b[1] = (id >> 8) & 0xff; b[2] = (id >> 16) & 0xff; b[3] = (id >> 24) & 0xff;
            b[4] = dlc;
            std::memcpy(b + 5, &pan, 2); std::memcpy(b + 7, &tilt, 2);
            std::memset(b + 9, 0, 4);
        };
        uint8_t b[13]; float p = -1, t = -1;

        frame(0x107, 4, 60, -30, b);
        ok(parseRcFrame(b, 13, p, t) && p == 60.0f && t == -30.0f,
           "a 0x107 frame gives pan and tilt in deg/s", "60 / -30");

        frame(0x107, 4, -60, 0, b);
        ok(parseRcFrame(b, 13, p, t) && p == -60.0f, "and a negative rate keeps its sign", "-60");

        frame(0x106, 4, 60, 60, b);
        ok(!parseRcFrame(b, 13, p, t), "another CAN id is NOT a pan/tilt rate");
        frame(0x107, 8, 60, 60, b);
        ok(!parseRcFrame(b, 13, p, t), "nor is 0x107 with the wrong dlc");
        ok(!parseRcFrame(b, 12, p, t) && !parseRcFrame(b, 14, p, t), "nor the wrong length");
        ok(!parseRcFrame(nullptr, 13, p, t), "and a null packet is refused rather than read");

        // A 13-BYTE TEXT LINE. This is the case that would move the head for no reason, so it is
        // the one worth being sure about.
        const char* text = "GAZE 120 95\r\n";    // exactly 13 bytes, the rate frame's length
        ok(std::strlen(text) == 13 && !parseRcFrame((const uint8_t*)text, 13, p, t),
           "a 13-byte TEXT command is not mistaken for a rate frame", text);
    }

    // ---- 10. RC through the real control law ------------------------------------------------------
    {
        const Limits lim = limitsFrom(true, 62, true, 118);
        State s; s.lastMs = 0;
        In in; in.nowMs = 0; in.motionAllowed = true;
        in.rcMs = 1; in.rcTilt = 60;                  // full stick up, the StamPLC's 60 deg/s
        run(s, in, lim, 500);
        ok(s.owner == Owner::Rc, "an RC frame takes the head");
        char rd[48]; snprintf(rd, sizeof(rd), "tilt %.1f", s.tilt);
        ok(s.tilt > kCentreDeg + 10.0f, "and moves it at the stick's rate", rd);

        // SILENCE RELEASES IT. The StamPLC only sends while someone is aiming, so the lease
        // expiring is how the operator lets go -- there is no "stop" message to wait for.
        In quiet; quiet.nowMs = 5000; quiet.motionAllowed = true; quiet.rcMs = 1;
        State s2 = s; s2.lastMs = 4000;
        tick(s2, quiet, lim);
        ok(s2.owner != Owner::Rc, "and silence hands it back rather than holding it");
    }

    // ---- 11. the TILT button confirm -------------------------------------------------------------
    // The interlock the maintainers asked for: a TILT over the network waits for somebody at the
    // head to press the button. Tested here rather than on the board because the board has no USB
    // cable, and because the cases that matter are the awkward ones -- a button already held, a
    // press one tick too late -- which are miserable to stage by hand and easy to get wrong.
    {
        Pending p;
        ok(!request(p, 150, 10, 1000, false), "a reversed band is refused, and arms nothing");
        ok(!request(p, -5, 100, 1000, false), "a negative floor is refused");
        ok(!request(p, 0, 200, 1000, false), "a ceiling past 180 is refused");
        ok(!p.waiting, "none of those left anything waiting");

        ok(request(p, 71, 150, 1000, false), "a sane band is accepted");
        ok(p.waiting, "and waits for the press");
        ok(poll(p, 1100, false) == Confirm::None, "nothing happens while the button is untouched");
        ok(poll(p, 1200, true) == Confirm::Applied, "the press applies it");
        ok(!p.waiting, "and clears the request, so one press cannot confirm twice");
        ok(poll(p, 1300, true) == Confirm::None, "a second press does nothing on its own");
    }
    {
        // THE WHOLE POINT OF THE INTERLOCK. A button already held when the request arrives must
        // not confirm it -- otherwise a hand resting on the Dial, or anything leaning on it, turns
        // the interlock into a no-op while still passing a test written with a tidy press.
        Pending p;
        request(p, 71, 150, 1000, true);                 // button ALREADY down
        ok(poll(p, 1100, true) == Confirm::None, "a button already held does NOT confirm");
        ok(poll(p, 1200, true) == Confirm::None, "nor does keeping it held");
        ok(poll(p, 1300, false) == Confirm::None, "releasing it does not confirm either");
        ok(poll(p, 1400, true) == Confirm::Applied, "but a fresh press, after the release, does");
    }
    {
        Pending p;
        request(p, 71, 150, 1000, false);
        ok(poll(p, 1000 + kTiltConfirmMs - 1, false) == Confirm::None, "the window is still open just before the deadline");
        ok(poll(p, 1000 + kTiltConfirmMs, false) == Confirm::Expired, "and closes on it");
        ok(!p.waiting, "an expired request is dropped");
        ok(poll(p, 1000 + kTiltConfirmMs + 5000, true) == Confirm::None, "a press after the window does nothing");
    }
    {
        // A press on the very tick the window shuts is REFUSED rather than raced: expiry is
        // checked first, so there is one answer and not two.
        Pending p;
        request(p, 71, 150, 1000, false);
        ok(poll(p, 1000 + kTiltConfirmMs, true) == Confirm::Expired, "a press exactly on the deadline is too late");
    }
    {
        // LATEST WINS. During a commissioning the operator sends notch after notch; an earlier
        // request must not be confirmable later by a press meant for the current one.
        Pending p;
        request(p, 71, 150, 1000, false);
        request(p, 74, 140, 2000, false);
        ok(p.lo == 74 && p.hi == 140, "a second request replaces the first");
        ok(poll(p, 2100, true) == Confirm::Applied, "and the press confirms the SECOND one");
    }
    {
        Pending p;
        ok(secondsLeft(p, 5000) == 0, "nothing waiting, no countdown");
        request(p, 71, 150, 1000, false);
        ok(secondsLeft(p, 1000) == kTiltConfirmMs / 1000, "the countdown starts at the full window");
        ok(secondsLeft(p, 1000 + kTiltConfirmMs) == 0, "and reaches zero at the deadline");
    }
    {
        // The clock wraps after 49 days. Signed arithmetic throughout, as in the control law --
        // the same unsigned-underflow bug that made the GAZE lease flap would expire every
        // pending request the instant it was made.
        Pending p;
        const uint32_t nearWrap = 0xFFFFFF00u;
        request(p, 71, 150, nearWrap, false);
        ok(poll(p, nearWrap + 100, false) == Confirm::None, "a request made just before the clock wraps does not expire early");
        ok(poll(p, nearWrap + 200, true) == Confirm::Applied, "and can still be confirmed across the wrap");
    }

    printf(failed ? "\n%d FAILED\n" : "\nall pantilt checks passed\n", failed);
    return failed ? 1 : 0;
}
