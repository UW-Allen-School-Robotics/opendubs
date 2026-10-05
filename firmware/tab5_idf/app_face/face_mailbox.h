/*
 * face_mailbox — the two POD mailboxes between the panel and everything else.
 *
 * NO LVGL IN THIS FILE, DELIBERATELY. It is the `shared/` tier of
 * notes/design/tab5-face-architecture.md §3: the UI depends on it, `net` depends on it, and it
 * depends on neither. That is what lets the sender be written later without touching a line of
 * UI code, and it is why the technician screen can be built and reviewed before the transport
 * exists at all.
 *
 * Two directions, two different problems:
 *
 *   RobotSnapshot  (robot -> panel).  EVERY VALUE CARRIES ITS AGE. This is the core argument of
 *                  notes/design/tab5-dashboard.md: "a 1280x720 JPEG that stops arriving is
 *                  pixel-identical to a live panel displaying steady values", and a native
 *                  renderer only beats that if it actually keeps the timestamp. So a reading is
 *                  not a float here, it is a float plus the stamp of the frame it arrived in,
 *                  and the UI is structurally unable to draw one without the other.
 *
 *                  Three display states, not two. A value is LIVE, STALE (we had it, it is old,
 *                  here is how old) or NEVER (we have never had it, so there is no number to
 *                  show and we must not show a zero). The third one matters most: a freshly
 *                  booted head with no link would otherwise render "0% SOC, 0.0 V, contactor
 *                  open" — every field a plausible, alarming lie.
 *
 *   UiIntent       (panel -> robot).  The UI NEVER OPENS A SOCKET (architecture note §3 rule 3).
 *                  A touch handler writes an intent here and returns; `net` reads it and sends.
 *                  Two shapes of request, because the two have different failure modes:
 *                    - a LIVE intent with an expiry (the mast jog): value + stamp. A sender that
 *                      dies, a UI that stops writing, a finger that leaves the glass — all three
 *                      are indistinguishable from "stop", which is the whole point.
 *                    - a DISCRETE request (soft stop, fault clear, aux relay, re-arm): a code
 *                      plus a sequence number, so the sender can tell a NEW request from the
 *                      one it already delivered, and can resend until the robot's own state
 *                      says it landed.
 *
 * SEQLOCKS, NOT MUTEXES (architecture note §2.4). Single writer per mailbox; a reader retries
 * while the counter is odd or changed. No reader ever blocks a writer and no writer ever blocks
 * the UI, which is the property that matters on a panel whose render task must not wait for the
 * network. Both structs are larger than the note's "<= 64 bytes" guidance (RobotSnapshot is
 * ~140 B); a seqlock is still correct at that size, it just retries slightly more often under
 * contention, and contention here is one 20 Hz writer against one 30 Hz reader.
 */
#pragma once
#include <atomic>
#include <cstdint>

// ---- freshness groups -------------------------------------------------------------------------
// One stamp per GROUP rather than per field, because a group is what arrives together in one
// frame. Splitting them finer would imply a precision the transport does not have; splitting
// them coarser would let a fresh RC frame vouch for a stale battery reading, which is exactly
// the lie this file exists to prevent.
enum MbGroup : uint8_t {
    MB_MODE = 0,   // robot mode / FSM state
    MB_ESTOP,      // e-stop chain: button, contactor, weld
    MB_BATTERY,    // pack SOC and voltage (RS485 BMS -> StamPLC; see memory: battery-has-rs485-bms)
    MB_RC,         // RC link: live, mode (CH5 authority), deadman (CH6)
    MB_THOR,       // the Thor link itself
    MB_FAULTS,     // fault flags
    MB_NET,        // our own IP / association state
    MB_LIFT,       // Z-axis height, motion, pinch guard
    MB_GROUP_COUNT
};

// ---- fault flags ------------------------------------------------------------------------------
// Lifted from the set the dashboard supervisor already derives
// (software/kiosk-dashboard/backend/status.py `_robot_state`), so the panel cannot invent a
// fault vocabulary the rest of the robot does not use. ui_controls.cpp pairs each with what
// clears it; that pairing is the whole reason the fault screen is worth having.
enum : uint32_t {
    MB_FAULT_CONTACTOR_WELDED = 1u << 0,   // fatal: needs service
    MB_FAULT_PDH_BROWNOUT     = 1u << 1,
    MB_FAULT_PDH_HARDWARE     = 1u << 2,   // fatal: needs service
    MB_FAULT_OVERTEMP         = 1u << 3,
    MB_FAULT_LIFT_SILENT      = 1u << 4,   // a required module stopped reporting
    MB_FAULT_ESTOP_LATCHED    = 1u << 5,
    MB_FAULT_RC_LOST          = 1u << 6,
    MB_FAULT_COUNT_BITS       = 7
};

// ---- RC authority ----------------------------------------------------------------------------
// CH5 is a PHYSICAL switch and it is still the authority (dashboard note, "What actually bounds
// a panel control"). The panel is subject to it exactly as the dashboard is, and when it is OFF
// a panel jog must say WHY rather than appearing broken — so the mode has to be legible here.
enum MbAuthority : uint8_t { MB_AUTH_UNKNOWN = 0, MB_AUTH_OFF, MB_AUTH_MANUAL, MB_AUTH_AUTO };

struct RobotSnapshot {
    // Which groups have EVER been received. Bit n = group n. A clear bit means "no number
    // exists", which renders as "--" and never as zero.
    uint32_t valid;
    uint32_t stamp[MB_GROUP_COUNT];     // ms (the same clock the UI ticks on) per group

    // MB_MODE
    char     stateName[16];             // matches kStates[].name in app_face.cpp / ui_shared.cpp
    int8_t   stateIdx;                  // index into that table, -1 when unmatched

    // MB_ESTOP
    bool     estopButton;               // the physical mushroom is pressed (IN1/DI0)
    bool     contactorEnergized;        // RELAY1/R0 is closed
    bool     contactorWelded;           // commanded open, aux still reads closed
    bool     motionEnabled;

    // MB_BATTERY
    uint8_t  socPct;                    // 0..100
    uint16_t packDv;                    // pack volts x10 (520 = 52.0 V) — integers, no float churn
    int16_t  packDa;                    // pack amps  x10, signed (negative = charging)

    // MB_RC
    bool     rcLive;
    bool     rcDeadman;                 // CH6 held
    uint8_t  rcAuthority;               // MbAuthority

    // MB_FAULTS
    uint32_t faultBits;

    // MB_NET
    char     ip[16];                    // dotted quad, or "" when not associated

    // MB_LIFT
    int16_t  liftHeightMm;              // < 0 = unknown, as LiftController reports it
    int8_t   liftMotion;                // 0 stop, 1 up, 2 down
    bool     liftAtTop, liftAtBot;
    bool     liftGuardHold;             // the StamPLC pinch guard is refusing a descent
};

// Read a consistent copy. Never blocks, always returns (a torn read is retried, not tolerated).
void mbSnapshotGet(RobotSnapshot* out);

// Publish. SINGLE WRITER — whoever owns `net`. It keeps its own staging RobotSnapshot, mutates
// the groups it just parsed, stamps them with mbStamp(), and publishes the whole thing.
void mbSnapshotPublish(const RobotSnapshot& in);

// Stamp one group as having just arrived, on a caller-owned staging struct.
inline void mbStamp(RobotSnapshot* s, MbGroup g, uint32_t nowMs)
{
    s->valid |= (1u << g);
    s->stamp[g] = nowMs;
}

// Age of a group in ms, or MB_AGE_NEVER when it has never arrived. WRAP-SAFE: a signed
// difference, for the same reason AUDIT.md invariant 5 exists — the 32-bit millisecond counter
// wraps at ~49 days and a robot head reaches that.
enum : uint32_t { MB_AGE_NEVER = 0xffffffffu };
inline uint32_t mbAge(const RobotSnapshot& s, MbGroup g, uint32_t nowMs)
{
    if ((s.valid & (1u << g)) == 0) return MB_AGE_NEVER;
    const int32_t d = (int32_t)(nowMs - s.stamp[g]);
    return d < 0 ? 0u : (uint32_t)d;
}

// ---- intents ----------------------------------------------------------------------------------

// Discrete requests. The grade in the comment is the one notes/design/tab5-dashboard.md's table
// assigns, and it is a property of the CONTROL rather than of the wire: the same MB_REQ_SOFT_STOP
// would be grade 1 from a blind physical button and is grade 3 from a touch target, because the
// touchscreen can name the action and require a sustained gesture where a button cannot.
enum MbRequest : uint8_t {
    MB_REQ_NONE = 0,
    MB_REQ_SOFT_STOP,        // grade 3 — de-energize. A SOFTWARE stop, not the e-stop. See below.
    MB_REQ_FAULT_CLEAR,      // grade 1 — nothing moves, nothing is energised
    MB_REQ_AUX_RELAY_ON,     // grade 3 — RELAY4/R3, the only channel free of the lift's legs
    MB_REQ_AUX_RELAY_OFF,    // grade 3
    MB_REQ_ESTOP_REARM,      // grade 4 — a REQUEST only; the panel may never hold the decision
    MB_REQ_COUNT
};

struct UiIntent {
    // GRADE 2 — a live intent with an expiry. -1..1, the same range and meaning as the RC stick.
    float    mast;
    uint32_t mastMs;         // when the UI last ASSERTED it; staleness here means stop

    // GRADE 1/3/4 — discrete. `reqSeq` bumps once per issued request so a sender can tell a new
    // one from a redelivery, and can resend an undelivered one without issuing it twice.
    uint8_t  req;            // MbRequest
    uint16_t reqSeq;

    // Soft state the panel owns outright, no round trip needed.
    bool     mute;           // grade 1
    uint8_t  screen;         // which panel screen is up (0 face, 1 technician, 2 fault)
};

// THE EXPIRY IS THE SAFETY PROPERTY, so it is named once and shared. 250 ms is not a number
// chosen here: it is what `/api/mast/{cmd}` and the StamPLC's `MAST CMD` already enforce
// (dashboard note, "Authority: confirmation and intent"). A panel jog is the same source
// re-published at the same rate, so an operator whose finger slips has already stopped the lift.
enum : uint32_t { MB_MAST_EXPIRY_MS = 250 };

// Republish rate for a held jog. Comfortably inside the expiry, so one dropped datagram is not
// a stop and two are.
enum : uint32_t { MB_MAST_REPUBLISH_MS = 50 };

void mbIntentGet(UiIntent* out);              // for `net`
void mbIntentPublish(const UiIntent& in);     // SINGLE WRITER — the UI task only

// ---- the wire vocabulary, which is NOT invented here ------------------------------------------
//
// These are the command strings the StamPLC's parser ALREADY accepts, read from
// firmware/rc_serial_probe/src/main.cpp. They are recorded here so that whoever writes the
// sender does not re-derive them — and because getting one wrong is a robot moving when it
// should not. The transport (socket, address, rate, retry) is deliberately NOT here: the Tab5
// listens for text commands on UDP 4242 today, and what it should TRANSMIT to, and whether
// via the StamPLC directly or via the Thor, is 1B's question, not this file's.
//
//   MAST CMD <-1..1>   feeds the same setIntent() the RC sticks feed, so the pinch guard, the
//                      limits and the belief correction all apply. Heartbeat-gated at the far
//                      end: stop sending and the lift stops.
//   ESTOP              drops the contactor coil (relay 0) and latches local_estop_active.
//   RELAY <ch> <0|1>   ch 0..3. The panel may only ever use ch 3 — see the refusal below.
//
// THREE REFUSALS, each with its reason, because a sender reading this file is exactly who needs
// them:
//
//  1. NEVER `RELAY 1 x` or `RELAY 2 x`. Those are the scissor lift's DOWN and UP legs, and
//     writing them directly walks past the pinch guard. The dashboard refuses those two channels
//     outright for this reason. The mast moves by MAST CMD or it does not move.
//
//  2. NEVER `RELAY 0 1` to re-arm. It works — the parser clears local_estop_active on it — and
//     that is the hazard: it is the re-energize path with NONE of the supervisor's guard. The
//     real guard (button released, telemetry fresh, contactor not welded, CH5 OFF) lives in
//     status.py `try_estop_clear`, and the dashboard note is explicit that "that guard must stay
//     where it is. The panel may REQUEST a re-arm (grade 4) and must render the blockers the
//     supervisor returns; it may never hold the decision." So MB_REQ_ESTOP_REARM must be
//     delivered to the dashboard's guarded endpoint, not to the StamPLC.
//
//     NOTE FOR 1B, and it is a real gap rather than an oversight here: the StamPLC's parser has
//     no guarded re-arm command at all. There is `ESTOP` to drop and `RELAY 0 1` to raise, and
//     nothing in between. Architecture note open question 12 asks whether the StamPLC accepts a
//     re-arm request and applies its own preconditions; read against the source, the answer is
//     NO — only the dashboard's guarded path exists. Either the head sends to the dashboard API
//     (which has no authentication yet, and that fix has to land first), or the StamPLC gains a
//     guarded `ESTOP CLEAR`. That decision is not this file's to make, which is why the intent
//     stops at a request and no string is offered for it.
//
//  3. NEVER style a soft stop as the e-stop. MB_REQ_SOFT_STOP sends `ESTOP`, which is the same
//     path the dashboard's e-stop button uses and is genuinely useful. But the dashboard note's
//     sharpest correction applies to the AFFORDANCE, not the action: "A big red STOP on glass
//     next to a real mushroom-head e-stop teaches a bystander that the glass is the emergency
//     control" — on a panel that is dark whenever the contactor is already open, because the
//     Tab5 is powered from the e-stop-protected drive branch. ui_controls.cpp labels it as a
//     software stop, does not give it the e-stop's shape or colour, and points at the mushroom.
#define MB_CMD_MAST_FMT   "MAST CMD %.2f"
#define MB_CMD_ESTOP      "ESTOP"
#define MB_CMD_RELAY_FMT  "RELAY %d %d"
#define MB_AUX_RELAY_CH   3              // RELAY4 in human numbering; see memory opendubs-io-numbering
