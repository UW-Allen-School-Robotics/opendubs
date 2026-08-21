#!/usr/bin/env python3
# OpenDubs: broadcast an "HB" heartbeat to the StampPLC every 2s so its firmware Ethernet
# watchdog can detect a wedged RX stack (linkStatus() reads up but nothing arrives). Broadcast
# so it is independent of the StampPLC DHCP address.
import socket, time
PORT = 5005
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
while True:
    try:
        s.sendto(b"HB\n", ("255.255.255.255", PORT))
    except OSError:
        pass
    time.sleep(2)
