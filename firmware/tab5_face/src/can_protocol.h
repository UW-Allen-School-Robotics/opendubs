#pragma once
#include <stdint.h>

#define CAN_ID_TOF_FRONT    0x101
#define CAN_ID_LIFT_CMD     0x102
#define CAN_ID_ROBOT_STATE  0x103
#define CAN_ID_PAN_TILT_CMD 0x104

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
