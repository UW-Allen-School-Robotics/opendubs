// Native (host) test for the pure protocol logic in opendubs_can.h.
// Build + run: g++ -std=c++17 -Wall -Wextra -I.. test_opendubs_can.cpp -o /tmp/test_opendubs_can && /tmp/test_opendubs_can
#include "../opendubs_can.h"
#include <cassert>
#include <cstdio>

using namespace opendubs_can;

static void test_ids() {
  assert(kIdAuthorize == 0x000);
  assert(kIdLiftState == 0x080);
  assert(kIdLiftCmd   == 0x081);
  assert(kIdLiftFault == 0x082);
}

static void test_lift_state_roundtrip() {
  LiftState s{};
  s.heightMm = 633;
  s.inhibit = false; s.atTop = true; s.atBot = false; s.tofOk = true;
  s.relayUpPulse = true; s.relayDnPulse = false;
  s.motion = LiftMotion::UP;
  s.seq = 42;

  uint8_t buf[8] = {};
  uint8_t dlc = encodeLiftState(s, buf);
  assert(dlc == 4);
  assert(buf[0] == 0x02 && buf[1] == 0x79);   // 633 = 0x0279, big-endian
  assert(buf[2] == 0x5A);                     // atTop|tofOk|relayUpPulse|motion(UP=1)<<6
  assert(buf[3] == 42);

  LiftState d = decodeLiftState(buf, dlc);
  assert(d.heightMm == 633);
  assert(d.inhibit == false);
  assert(d.atTop == true);
  assert(d.atBot == false);
  assert(d.tofOk == true);
  assert(d.relayUpPulse == true);
  assert(d.relayDnPulse == false);
  assert(d.motion == LiftMotion::UP);
  assert(d.seq == 42);
}

static void test_lift_state_short_dlc_is_safe() {
  uint8_t buf[8] = {0xAA, 0xBB, 0xCC};
  LiftState d = decodeLiftState(buf, 2);   // short DLC must not be trusted
  assert(d.heightMm == 0);
  assert(d.seq == 0);
}

static void test_lift_cmd_roundtrip() {
  LiftCommand c{LiftCmd::DOWN, 7};
  uint8_t buf[8] = {};
  uint8_t dlc = encodeLiftCmd(c, buf);
  assert(dlc == 2);
  assert(buf[0] == static_cast<uint8_t>(LiftCmd::DOWN));
  assert(buf[1] == 7);

  LiftCommand d = decodeLiftCmd(buf, dlc);
  assert(d.cmd == LiftCmd::DOWN);
  assert(d.seq == 7);
}

static void test_fault_roundtrip() {
  Fault f{FaultCode::TOF_FAULT, 3};
  uint8_t buf[8] = {};
  uint8_t dlc = encodeFault(f, buf);
  assert(dlc == 2);
  Fault d = decodeFault(buf, dlc);
  assert(d.code == FaultCode::TOF_FAULT);
  assert(d.detail == 3);
}

static void test_staleness() {
  assert(isStale(1000, 0, 250) == true);     // never seen
  assert(isStale(1000, 900, 250) == false);  // 100ms ago, within 250ms
  assert(isStale(1000, 700, 250) == true);   // 300ms ago, over 250ms
  assert(isStale(1000, 750, 250) == false);  // exactly at the 250ms boundary — not stale
}

int main() {
  test_ids();
  test_lift_state_roundtrip();
  test_lift_state_short_dlc_is_safe();
  test_lift_cmd_roundtrip();
  test_fault_roundtrip();
  test_staleness();
  std::printf("opendubs_can: all tests passed\n");
  return 0;
}
