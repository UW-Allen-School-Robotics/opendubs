# Passive listener: never sends. Captures arm_right for 2 s, tallies frames by CAN id,
# and for reply ids 0x11-0x18 shows the d[0] low nibble (motor id), d[2] and status.
import socket, struct, time, collections
F = struct.Struct("=IBBBB64s")
for iface in ("arm_right", "arm_left"):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
    s.setsockopt(socket.SOL_CAN_RAW, 2, 0x1FFFFFFF)
    s.bind((iface,)); s.settimeout(0.05)
    ids = collections.Counter(); last = {}; d2 = collections.defaultdict(collections.Counter); errs = 0
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        try: raw = s.recv(F.size)
        except socket.timeout: continue
        cid, n, *_ , data = F.unpack(raw.ljust(F.size, b"\0"))
        if cid & 0x20000000: errs += 1; continue
        k = cid & 0x7FF; ids[k] += 1; last[k] = data[:8].hex(); 
        if 0x11 <= k <= 0x18: d2[k][data[2]] += 1
    s.close()
    print(iface, "error frames:", errs)
    for k in sorted(ids):
        extra = ""
        if k in d2:
            top = d2[k].most_common(3)
            extra = " d[2]=" + ",".join(f"0x{b:02x}x{c}" for b, c in top) + f" status={int(last[k][0],16)} mid_nibble={int(last[k][1],16)}"
        print(f"  0x{k:03x} n={ids[k]:5d} last={last[k]}{extra}")
