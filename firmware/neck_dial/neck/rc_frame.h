// The StamPLC's RC pan/tilt frame, parsed as pure compute so the desk can test it without a robot.
//
// THE FORMAT IS THE TAB5'S, byte for byte, because the StamPLC is unchanged and must stay so:
//
//     struct __attribute__((packed)) { uint32_t id; uint8_t dlc; uint8_t d[8]; }   // 13 bytes
//     id = 0x107 (CAN_ID_PAN_TILT_RATE), dlc = 4
//     int16_t pan  deg/s at d[0]  (offset 5)
//     int16_t tilt deg/s at d[2]  (offset 7)        little-endian
//
// It arrives on the SAME UDP port as the text protocol, so the length/id/dlc triple is what tells a
// rate frame from a command. Checking all three matters: a 13-byte text line is possible, and
// acting on one as if it were a rate would move the head.
//
// A RATE, NOT AN ANGLE, and that is load-bearing: the stick is spring-centred, so the HEAD owns the
// position and two devices can never integrate separately and diverge. Silence is also how the
// StamPLC RELEASES the head -- it only sends while someone is actively aiming -- which is why the
// control law expires RC after its lease rather than waiting to be told to stop.
#pragma once
#include <cstdint>
#include <cstddef>

namespace neck {

constexpr uint32_t kRcFrameId  = 0x107;
constexpr size_t   kRcFrameLen = 13;

// true if this packet is a pan/tilt rate frame; pan/tilt are then degrees per second.
bool parseRcFrame(const uint8_t* b, size_t n, float& panDps, float& tiltDps);

}  // namespace neck
