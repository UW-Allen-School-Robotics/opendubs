#!/usr/bin/env python3
"""A FAKE THOR: the other end of Phase B, so the head's transport can be exercised off the robot.

It is deliberately STRICT and deliberately not a mock. A mock written alongside the client agrees
with the client by construction and proves nothing; this validates the Phase C contract as
written in notes/workplan-tab5-audio.md and REJECTS anything that does not meet it. When the real
Thor-side work starts, this file is the specification it has to satisfy -- and the head has
already been tested against it.

What it checks, and every one of these is a mistake the head could plausibly make:
  * the protocol banner and message type
  * every required key present, and `rate`/`ch`/`bits` actually 16000/1/16 -- a head that sent
    48 kHz four-channel audio would otherwise be "working" right up until an ASR heard chipmunks
  * `bytes` == `samples` * 2, and the payload EXACTLY that long: a short read here is the classic
    framing bug, and accepting it would hide it
  * the payload is plausible PCM rather than text -- a base64'd or ASCII payload is caught
  * `seq` strictly increasing, so duplicates and replays are visible

It answers with a REPLY line, and can be told to answer badly (--status, --garbage, --silent) so
the head's failure paths get exercised too, which is the half that normally goes untested.

  ./fake_thor.py --port 9400                  # serve until killed
  ./fake_thor.py --port 9400 --once --json    # one clip, machine-readable, for the test harness
"""
import argparse, json, socket, struct, sys, time

BANNER = "OPENDUBS-VOICE/1"


class Reject(Exception):
    pass


def parse_header(line):
    if not line.startswith(BANNER + " "):
        raise Reject("bad banner: %r" % line[:40])
    rest = line[len(BANNER) + 1:].split(" ")
    kind, kv = rest[0], {}
    for tok in rest[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            kv[k] = v
    return kind, kv


def need(kv, *keys):
    missing = [k for k in keys if k not in kv]
    if missing:
        raise Reject("missing keys: %s" % ",".join(missing))


def check_clip(kv):
    need(kv, "seq", "began", "ended", "dir", "floor", "peak", "rate", "ch", "bits",
         "samples", "bytes")
    if kv["rate"] != "16000":
        raise Reject("rate=%s, contract says 16000 (sending capture rate un-resampled?)" % kv["rate"])
    if kv["ch"] != "1":
        raise Reject("ch=%s, contract says mono (sending the 4-channel buffer?)" % kv["ch"])
    if kv["bits"] != "16":
        raise Reject("bits=%s, contract says 16" % kv["bits"])
    if kv["dir"] not in ("L", "R", "C", "N"):
        raise Reject("dir=%s is not one of L/R/C/N" % kv["dir"])
    n, b = int(kv["samples"]), int(kv["bytes"])
    if b != n * 2:
        raise Reject("bytes=%d but samples=%d (expected %d)" % (b, n, n * 2))
    if int(kv["ended"]) < int(kv["began"]):
        raise Reject("ended=%s precedes began=%s" % (kv["ended"], kv["began"]))
    return b


def check_pcm(payload, kv):
    """Catch a payload that is not actually little-endian int16 PCM."""
    if len(payload) < 2:
        return
    # Text masquerading as audio: base64 and ASCII are overwhelmingly 0x20-0x7e.
    printable = sum(1 for c in payload[:512] if 0x20 <= c <= 0x7E)
    if printable > len(payload[:512]) * 0.95:
        raise Reject("payload looks like TEXT, not PCM (%d/%d printable) -- base64'd audio?"
                     % (printable, len(payload[:512])))
    vals = struct.unpack("<%dh" % (len(payload) // 2), payload[:len(payload) // 2 * 2])
    peak = max(abs(v) for v in vals)
    if peak == 0:
        raise Reject("payload is entirely silent -- a dead capture path would look exactly like this")
    return peak


def serve(args):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(4)
    if not args.json:
        print("fake thor listening on 127.0.0.1:%d" % args.port, flush=True)
    results, last_seq = [], -1

    while True:
        conn, _ = srv.accept()
        conn.settimeout(5.0)
        try:
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    raise Reject("connection closed before a header arrived")
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            kind, kv = parse_header(line.decode("utf-8", "replace"))

            rec = {"kind": kind, "keys": kv}
            if kind == "CLIP":
                want = check_clip(kv)
                # Read EXACTLY `bytes`, and complain if the head sent more or fewer.
                while len(buf) < want:
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise Reject("payload short: got %d of %d bytes" % (len(buf), want))
                    buf += chunk
                if len(buf) > want:
                    raise Reject("payload long: %d bytes after a %d-byte header claim"
                                 % (len(buf), want))
                rec["peak"] = check_pcm(buf, kv)
                rec["ms"] = int(kv["samples"]) * 1000 // 16000
            elif kind == "NOSPEECH":
                need(kv, "seq", "began", "ended", "floor")
                if buf:
                    raise Reject("NOSPEECH carried %d bytes of payload; it must carry none" % len(buf))
            else:
                raise Reject("unknown message type %r" % kind)

            seq = int(kv["seq"])
            if seq <= last_seq:
                raise Reject("seq=%d did not increase (last was %d)" % (seq, last_seq))
            last_seq = seq

            rec["ok"] = True
            if args.silent:
                pass                                  # answer nothing: exercise the head's timeout
            elif args.garbage:
                conn.sendall(b"I am not a protocol\n")
            else:
                face = args.face or "-"
                conn.sendall(("%s REPLY seq=%d status=%s face=%s bytes=0\n"
                              % (BANNER, seq, args.status, face)).encode())
        except Reject as e:
            rec = {"ok": False, "reject": str(e)}
            try:
                conn.sendall(("%s REPLY seq=0 status=declined face=- bytes=0\n" % BANNER).encode())
            except OSError:
                pass
        except (socket.timeout, OSError) as e:
            rec = {"ok": False, "reject": "transport: %s" % e}
        finally:
            conn.close()

        results.append(rec)
        if args.json:
            print(json.dumps(rec), flush=True)
        else:
            if rec.get("ok"):
                k = rec["keys"]
                if rec["kind"] == "CLIP":
                    print("  CLIP  seq=%s %sms dir=%s floor=%s peak=%s payload-peak=%d%s"
                          % (k["seq"], rec["ms"], k["dir"], k["floor"], k["peak"], rec["peak"],
                             "  TRUNCATED" if k.get("truncated") else ""), flush=True)
                else:
                    print("  NOSPEECH seq=%s floor=%s" % (k["seq"], k["floor"]), flush=True)
            else:
                print("  REJECTED: %s" % rec["reject"], flush=True)
        if args.once:
            break
    return 0 if all(r.get("ok") for r in results) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9400)
    ap.add_argument("--once", action="store_true", help="handle one message then exit")
    ap.add_argument("--json", action="store_true", help="one JSON object per message")
    ap.add_argument("--status", default="ok",
                    choices=["ok", "unintelligible", "declined", "busy"])
    ap.add_argument("--face", default="", help="a face name to request in the reply")
    ap.add_argument("--garbage", action="store_true", help="reply with something unparseable")
    ap.add_argument("--silent", action="store_true", help="never reply, to test the head's timeout")
    args = ap.parse_args()
    try:
        return serve(args)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
