#!/usr/bin/env python3
"""plc_listen.py SECONDS TAGS [COMMAND IP]: print the StamPLC's UDP 5005 broadcast lines whose tag is in TAGS
(comma-separated, e.g. "[ODO],[PPCAL]") for SECONDS; optionally send ONE documented host command (e.g. "PPCAL SHOW")
to the StamPLC first. Reads a raw packet socket, so it runs as root. Used by pinpoint-reset.sh and the mapping
scripts to log [ODO] beside a bag while /odom/raw is not yet deployed."""
# Read-only: print the StamPLC's broadcast lines (UDP 5005) whose tag matches, for N seconds.
# Optionally send ONE host command first (argv[3]) to the StamPLC at argv[4] (documented: UDP 5005 text).
import socket, struct, sys, time
dur, tags = float(sys.argv[1]), tuple(sys.argv[2].split(","))
cmd = sys.argv[3] if len(sys.argv) > 3 else ""
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003)); s.settimeout(0.5)
if cmd:
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto((cmd + "\n").encode(), (sys.argv[4], 5005))
    print(f">> sent: {cmd}", flush=True)
t0 = time.monotonic(); seen = 0
while time.monotonic() - t0 < dur:
    try: pkt, addr = s.recvfrom(65535)
    except socket.timeout: continue
    if pkt[12:14] != b"\x08\x00": continue
    ip = pkt[14:]
    if len(ip) < 28 or ip[9] != 17: continue
    ihl = (ip[0] & 15) * 4
    if struct.unpack("!H", ip[ihl+2:ihl+4])[0] != 5005 or socket.inet_ntoa(ip[16:20]) != "255.255.255.255": continue
    for line in ip[ihl+8:].decode(errors="replace").splitlines():
        if line.startswith(tags):
            print(f"{time.strftime('%T')} {line[:230]}", flush=True); seen += 1
