#pragma once
#include <stdint.h>

#define CAN_ID_TOF_FRONT    0x101
#define CAN_ID_LIFT_CMD     0x102
#define CAN_ID_ROBOT_STATE  0x103
#define CAN_ID_PAN_TILT_CMD 0x104
#define CAN_ID_LIFT_STATE   0x105   // StamPLC -> Tab5: what the lift is actually doing
#define CAN_ID_TAB5_STATUS  0x106   // Tab5 -> Thor: what the head believes
#define CAN_ID_PAN_TILT_RATE 0x107  // StamPLC -> Tab5: RC stick as a RATE

// Manual pan/tilt is a RATE, not a position, because the source is a spring-centred stick:
// hold to move, release to stop. It also sidesteps the real problem with sending absolute
// angles from a second device — two owners integrating separately diverge, and the head
// jumps whenever ownership changes. The Tab5 owns the position; everyone else nudges it.
struct __attribute__((packed)) CAN_Pan_Tilt_Rate {
    int16_t pan_dps;    // degrees/sec, + = pan right
    int16_t tilt_dps;   // degrees/sec, + = tilt up
};

// The Tab5 has no USB attached in service, so anything not in telemetry is unknowable.
// This is how we can tell "the face is showing the wrong state" from "the face never
// received a state" without walking over and looking at it.
struct __attribute__((packed)) CAN_Tab5_Status {
    uint8_t robot_state;   // index into the Tab5's STATES[]
    uint8_t link_online;   // has it heard from the controller recently
    uint8_t tracking;      // 0 = off, 1 = AI
    uint8_t ui_mode;       // 0 = face, 1 = controller
    int16_t current_pan;   // new: physical pan angle (g_targetPan)
    int16_t current_tilt;  // new: physical tilt angle (g_targetTilt)
};

struct __attribute__((packed)) can_frame_udp {
    uint32_t can_id;
    uint8_t  can_dlc;
    uint8_t  data[8];
};

struct __attribute__((packed)) CAN_ToF_Distance {
    uint16_t distance_mm;
    uint8_t  status; // 1 = OK, 0 = no valid reading
    // Which part answered at boot. Without this, "status 0" is ambiguous between a sensor
    // that is not there and one that is there but cannot see anything — and the difference
    // is a wiring job versus an aiming job. The Tab5 has no USB attached in service, so if
    // it is not in telemetry it is not knowable.
    uint8_t  part;   // 0 = none detected, 1 = VL53L0X, 2 = VL53L1X
};
#define TOF_PART_NONE  0
#define TOF_PART_L0X   1
#define TOF_PART_L1X   2

struct __attribute__((packed)) CAN_Lift_Command {
    uint8_t command; // 0=STOP, 1=UP, 2=DOWN
};

struct __attribute__((packed)) CAN_Robot_State {
    uint8_t state_id;
};

struct __attribute__((packed)) CAN_Pan_Tilt_Command {
    int16_t pan_angle;
    int16_t tilt_angle;
};

// Reported lift state. The Tab5 asks; the StamPLC acts and says what happened, so the HMI
// shows measured height rather than a local guess.
struct __attribute__((packed)) CAN_Lift_State {
    uint16_t height_mm;    // 0xFFFF = unknown (uncalibrated ToF, or no reading)
    uint8_t  motion;       // 0=stop 1=up 2=down
    uint8_t  flags;        // bit0 at-top, bit1 at-bottom, bit2 pinch guard holding
};
#define LIFT_FLAG_AT_TOP    0x01
#define LIFT_FLAG_AT_BOTTOM 0x02
#define LIFT_FLAG_GUARD     0x04
#define LIFT_HEIGHT_UNKNOWN 0xFFFF
