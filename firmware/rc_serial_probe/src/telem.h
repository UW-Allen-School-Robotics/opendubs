// tprintf() lives in main.cpp and tees to BOTH the USB serial console and the UDP
// telemetry socket. Anything a REMOTE operator needs to see must go through it —
// Serial.print* is USB-only, and most of this robot's bring-up happens over the network.
#pragma once
void tprintf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
