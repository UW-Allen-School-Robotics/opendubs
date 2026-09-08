#pragma once
#include <stdint.h>

#define CAN_ID_TOF_FRONT    0x101
#define CAN_ID_LIFT_CMD     0x102
#define CAN_ID_ROBOT_STATE  0x103
#define CAN_ID_PAN_TILT_CMD 0x104
#define CAN_ID_LIFT_STATE   0x105   // StampPLC -> Tab5: what the lift is actually doing

struct __attribute__((packed)) can_frame_udp {
    uint32_t can_id;
    uint8_t  can_dlc;
    uint8_t  data[8];
};

struct __attribute__((packed)) CAN_ToF_Distance {
    uint16_t distance_mm;
    uint8_t  status; // 1 = OK, 0 = Error
};

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

// Reported lift state. The Tab5 asks; the StampPLC acts and says what happened, so the HMI
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
