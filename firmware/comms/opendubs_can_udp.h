#pragma once
// Arduino UDP transport for the OpenDubs payload comms protocol.
// Works with any class deriving Arduino's UDP interface (WiFiUDP on the Tab5,
// EthernetUDP on the StampPLC) — both expose the same beginPacket/write/endPacket/
// parsePacket/read surface, so one implementation serves both firmware targets.
// Wire framing: [CAN_ID:2B big-endian][DLC:1B][payload:0..8B] per UDP datagram
// (docs/interfaces/payload-comms.md "Transport"). The caller owns the UDP object's
// begin(port) — this class only frames/sends/polls.
#include <Udp.h>
#include <cstring>   // std::memcpy — explicit dependency (was previously only picked up
                     // transitively via opendubs_can.h's own include)
#include "opendubs_can.h"

namespace opendubs_can {

class UdpTransport {
public:
  UdpTransport(UDP& udp, IPAddress dest, uint16_t port)
      : udp_(udp), dest_(dest), port_(port) {}

  bool send(uint16_t canId, uint8_t dlc, const uint8_t* payload) {
    if (dlc > 8) return false;
    uint8_t frame[11];
    frame[0] = static_cast<uint8_t>(canId >> 8);
    frame[1] = static_cast<uint8_t>(canId & 0xFF);
    frame[2] = dlc;
    if (dlc) std::memcpy(frame + 3, payload, dlc);
    if (!udp_.beginPacket(dest_, port_)) return false;
    udp_.write(frame, 3 + dlc);
    return udp_.endPacket() == 1;
  }

  bool poll(uint16_t& outCanId, uint8_t& outDlc, uint8_t outPayload[8]) {
    int psize = udp_.parsePacket();
    if (psize < 3) return false;
    uint8_t buf[11];
    int len = udp_.read(buf, sizeof(buf));
    if (len < 3) return false;
    outCanId = static_cast<uint16_t>((buf[0] << 8) | buf[1]);
    uint8_t dlc = buf[2];
    if (dlc > 8 || len < 3 + dlc) return false;
    outDlc = dlc;
    std::memcpy(outPayload, buf + 3, dlc);
    return true;
  }

private:
  UDP&      udp_;
  IPAddress dest_;
  uint16_t  port_;
};

}  // namespace opendubs_can
