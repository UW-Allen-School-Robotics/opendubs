#!/usr/bin/env python3
"""Show video on a projector (notes/design/choreo-video.md): two outputs, one schedule.

    python3 video_out.py discover                          # the Cast devices on this network
    python3 video_out.py status [--to cast|hdmi]           # what the runner would report (the tile's data)
    python3 video_out.py play FILE --to cast [--device NAME|IP] [--secs 60]
                                                           # serve FILE, play it, report sync each second
    python3 video_out.py play FILE --to hdmi [--secs 60]   # the same through mpv on the Thor's HDMI
    python3 video_out.py testclip OUT.mp4 [--secs 60]      # a 1080p clip: a big running clock, a beep
                                                           # each second — to SEE and HEAR the real lag

  CAST  — the projector's built-in Chromecast plays a file the Thor serves over HTTP (MediaServer, a
          byte-range server on OPENDUBS_VIDEO_PORT, 8094 — 8093 is the voice service). Discovery is
          mDNS (_googlecast._tcp), or a fixed address (OPENDUBS_CAST_HOST) where multicast does not
          cross the network. Control is the Cast protocol (pychromecast, Default Media Receiver). The
          receiver keeps its OWN clock, so the show starts it early by its measured start latency
          (OPENDUBS_CAST_LEAD_S) and corrects drift with seeks when the error passes CAST_TOL_S —
          never more often than SEEK_MIN_GAP_S, because a Cast seek re-buffers and is itself a visible
          stutter.
  HDMI  — mpv, full screen on the Thor's HDMI out, a long-running service (opendubs-video-hdmi) the
          runner drives over mpv's JSON IPC socket (OPENDUBS_MPV_SOCKET). It runs on the Thor's clock,
          so it starts on the show's time and is held to it by small speed changes (HDMI_TOL_S), with
          a seek only past HDMI_SEEK_S.

Nothing here is a safety function: a projector that drops out never stops the robot (as show audio,
DEC-7). Media files live in the show library (kind `video`), never in the repository.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUTPUTS = ("cast", "hdmi", "off")
SERVE_PORT = int(os.environ.get("OPENDUBS_VIDEO_PORT") or 8094)   # 8093 is opendubs-voice
# Cast: a seek re-buffers (a visible hitch of ~0.3-1 s), so the tolerance is loose and seeks are rare
CAST_TOL_S = 0.25
SEEK_MIN_GAP_S = 8.0
CAST_LEAD_S = float(os.environ.get("OPENDUBS_CAST_LEAD_S") or 0.8)   # per robot: measure with `play`
# HDMI: the Thor's own clock and decoder, so it is held tight
HDMI_TOL_S = 0.04
HDMI_SEEK_S = 0.25
HDMI_NUDGE = 0.03            # the speed change that pulls it back (3 %), released inside tolerance
PRELOAD_S = 6.0              # a video is loaded (paused) this long before it plays
CHECK_EVERY_S = 1.0          # how often a playing video's position is compared with the show's
TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
         ".png": "image/png", ".jpg": "image/jpeg"}
CAST_APP = "CC1AD845"        # the Cast Default Media Receiver


def black_png(w=16, h=9) -> bytes:
    """A tiny black PNG: what `end: black` shows (the receiver scales a still to the screen)."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\x00" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


BLACK = black_png()


def sync_action(err_s: float, since_seek_s: float, tol_s: float, min_gap_s: float) -> bool:
    """Whether a playing video should be seeked back onto the show's time: its error is past the
    tolerance, and the last seek has had time to settle (a seek also re-buffers)."""
    return abs(err_s) > tol_s and since_seek_s >= min_gap_s


def bind_address(spec: str | None = None) -> str:
    """Where the file server listens: OPENDUBS_VIDEO_BIND as an address or an interface name, else the
    robot-LAN interface avahi is limited to (allow-interfaces, as the AirPlay receiver reads it), else
    every interface. Read at start, never written down: the Thor's LAN address is a DHCP lease."""
    spec = (spec if spec is not None else os.environ.get("OPENDUBS_VIDEO_BIND") or "").strip()
    if not spec:
        try:
            for line in open("/etc/avahi/avahi-daemon.conf"):
                m = re.match(r"\s*allow-interfaces\s*=\s*([^,\s#]+)", line)
                if m:
                    spec = m.group(1)
                    break
        except OSError:
            pass
    if not spec or re.match(r"^\d+\.\d+\.\d+\.\d+$", spec):
        return spec
    import subprocess
    try:                                       # an interface name: its first IPv4 address
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", spec], capture_output=True,
                             text=True, timeout=3).stdout
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", out)
        return m.group(1) if m else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def local_ip_toward(host: str) -> str:
    """This machine's address as `host` sees it — the one the projector must fetch the file from."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 8009))
        return s.getsockname()[0]
    finally:
        s.close()


# ---- the Thor serves the files ----------------------------------------------------------------------
class MediaServer:
    """A byte-range HTTP server for the files a run registers, and nothing else (no directory, no
    other path). Cast receivers fetch with Range requests, and seek by them."""

    RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")

    def __init__(self, port: int = SERVE_PORT, bind: str = ""):
        self.files, self.lock = {}, threading.Lock()
        files, lock, rx = self.files, self.lock, self.RANGE_RE

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self._send(head=True)

            def do_GET(self):
                self._send(head=False)

            def _send(self, head):
                if self.path.split("?")[0] == "/black.png":
                    body = BLACK
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    return
                with lock:
                    path = files.get(self.path.split("?")[0])
                if not path or not os.path.isfile(path):
                    self.send_error(404)
                    return
                size = os.path.getsize(path)
                lo, hi, code = 0, size - 1, 200
                m = rx.match(self.headers.get("Range") or "")
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        lo = int(m.group(1))
                        hi = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                    else:                                   # bytes=-N: the last N bytes
                        lo = max(0, size - int(m.group(2)))
                    if lo > hi or lo >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    code = 206
                self.send_response(code)
                self.send_header("Content-Type", TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(hi - lo + 1))
                self.send_header("Access-Control-Allow-Origin", "*")   # the receiver is a web page
                if code == 206:
                    self.send_header("Content-Range", f"bytes {lo}-{hi}/{size}")
                self.end_headers()
                if head:
                    return
                with open(path, "rb") as fh:
                    fh.seek(lo)
                    left = hi - lo + 1
                    try:
                        while left > 0:
                            chunk = fh.read(min(1 << 16, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass                                # the receiver re-requests as it seeks

        self.srv = ThreadingHTTPServer((bind, port), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def register(self, path: str) -> str:
        """Serve `path`; returns the URL path it is served at (by a name derived from it)."""
        key = "/v/" + re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))
        with self.lock:
            self.files[key] = os.path.abspath(path)
        return key

    def url(self, key: str, toward: str) -> str:
        return f"http://{local_ip_toward(toward)}:{self.port}{key}"

    def close(self):
        self.srv.shutdown()


# ---- the outputs ---------------------------------------------------------------------------------
class CastOutput:
    """A Chromecast-capable display (the projector's Google TV), by name or address."""
    kind = "cast"
    tol_s, min_gap_s, nudge = CAST_TOL_S, SEEK_MIN_GAP_S, 0.0

    def __init__(self, name=None, host=None, server: MediaServer | None = None):
        self.name = name or os.environ.get("OPENDUBS_CAST_NAME") or None
        self.host = host or os.environ.get("OPENDUBS_CAST_HOST") or None
        self.server = server
        self.lead_s = CAST_LEAD_S
        self.cast, self.why, self.found_at, self.detail = None, "not looked for yet", 0.0, None
        self._looking = threading.Lock()

    def discover(self, timeout: float = 6.0) -> list:
        """[(friendly name, host, model)] of every Cast device found; remembers the one this output
        is for (OPENDUBS_CAST_NAME, else OPENDUBS_CAST_HOST, else the only one there is)."""
        try:
            import pychromecast
        except ImportError:
            self.why = "this robot can't cast yet: its casting software isn't installed"
            return []
        with self._looking:
            try:
                casts, browser = pychromecast.get_chromecasts(timeout=timeout,
                                                              known_hosts=[self.host] if self.host else None)
            except Exception as e:  # noqa: BLE001  said in plain words; the detail goes to the log
                self.cast, self.detail = None, f"{type(e).__name__}: {e}"
                self.why = "couldn't look for the projector on the robot's Wi-Fi"
                return []
            try:
                (browser.stop_discovery if hasattr(browser, "stop_discovery")
                 else lambda: pychromecast.discovery.stop_discovery(browser))()
            except Exception:  # noqa: BLE001
                pass
            found = [(c.cast_info.friendly_name, c.cast_info.host, c.cast_info.model_name) for c in casts]
            pick = [c for c in casts if (self.name and c.cast_info.friendly_name == self.name)
                    or (not self.name and self.host and c.cast_info.host == self.host)]
            if not pick and not self.name and not self.host and len(casts) == 1:
                pick = casts
            if pick:
                self.cast = pick[0]
                self.cast.wait(timeout=10)
                self.found_at, self.why = time.monotonic(), None
            else:
                self.cast = None
                self.why = ("projector not found on the robot's Wi-Fi — is it on, awake, and on Opendubs2?"
                            if not casts else
                            f"{len(casts)} cast devices found, none is this robot's projector "
                            f"({self.name or self.host})" if (self.name or self.host) else
                            f"{len(casts)} cast devices found — tell the robot which is the projector "
                            "(OPENDUBS_CAST_NAME)")
            return found

    def connected(self) -> bool:
        """Whether the found projector's Cast connection is up (pychromecast keeps it, with a heartbeat)."""
        try:
            return bool(self.cast and self.cast.socket_client.is_connected)
        except Exception:  # noqa: BLE001
            return False

    def status(self) -> dict:
        ci = self.cast.cast_info if self.cast else None
        up = self.connected()
        why = (self.why if self.cast is None else
               "the projector stopped answering — asleep, or off the robot's Wi-Fi" if not up else
               None if self.server else "the robot isn't serving video")
        return {"found": self.cast is not None, "name": ci.friendly_name if ci else self.name,
                "host": ci.host if ci else self.host, "ready": up and self.server is not None, "why": why}

    @property
    def mc(self):
        return self.cast.media_controller

    def load(self, path: str, from_s: float = 0.0):
        """The file, fetched and buffered, paused at `from_s`: so `play` starts it at once."""
        key = self.server.register(path)
        url = self.server.url(key, self.cast.cast_info.host)
        # BUFFERED: a file, seekable and timed (pychromecast's default, LIVE, is for streams)
        self.mc.play_media(url, TYPES.get(os.path.splitext(path)[1].lower(), "video/mp4"),
                           current_time=from_s, autoplay=False, stream_type="BUFFERED")
        self.mc.block_until_active(timeout=10)

    def play(self):
        self.mc.play()

    def pause(self):
        self.mc.pause()

    def seek(self, pos: float):
        self.mc.seek(max(0.0, pos))

    def speed(self, rate: float):
        pass                                          # the Default Media Receiver has no rate control

    def black(self):
        url = f"http://{local_ip_toward(self.cast.cast_info.host)}:{self.server.port}/black.png"
        self.mc.play_media(url, "image/png")

    def stop(self):
        try:
            self.mc.stop()
        except Exception:  # noqa: BLE001
            pass

    def position(self):
        """(seconds into the file, playing?) as the receiver last reported it, extrapolated to now."""
        try:
            self.mc.update_status()               # ask for a fresh report; this one is extrapolated
        except Exception:  # noqa: BLE001
            pass
        st = self.mc.status
        return (st.adjusted_current_time if st else None), bool(st and st.player_state == "PLAYING")


class HdmiOutput:
    """mpv on the Thor's HDMI out, as a service, driven over its JSON IPC socket."""
    kind = "hdmi"
    tol_s, min_gap_s, nudge = HDMI_SEEK_S, 2.0, HDMI_NUDGE
    lead_s = 0.0

    def __init__(self, sock=None):
        self.sock_path = sock or os.environ.get("OPENDUBS_MPV_SOCKET") or "/run/opendubs/mpv.sock"
        self.why, self.detail = None, None

    def _cmd(self, *args, timeout=1.0):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.sock_path)
            s.sendall(json.dumps({"command": list(args), "request_id": 1}).encode() + b"\n")
            buf = b""
            while True:
                buf += s.recv(65536)
                for line in buf.split(b"\n"):
                    if line.strip():
                        msg = json.loads(line)
                        if msg.get("request_id") == 1:
                            if msg.get("error") not in (None, "success"):
                                raise RuntimeError(f"mpv: {msg['error']}")
                            return msg.get("data")
        finally:
            s.close()

    @staticmethod
    def cable():
        """True/False from the kernel's HDMI connector status (connected = a powered display on the
        cable), None where there is no such connector to read."""
        import glob
        seen = None
        for f in glob.glob("/sys/class/drm/card*-HDMI-*/status"):
            try:
                if open(f).read().strip() == "connected":
                    return True
                seen = False
            except OSError:
                pass
        return seen

    def status(self) -> dict:
        present = os.path.exists(self.sock_path)
        ready, self.detail = False, None
        if not present:
            self.why = "the HDMI player isn't running on the robot"
        else:
            try:
                self._cmd("get_property", "idle-active")
                ready, self.why = True, None
            except Exception as e:  # noqa: BLE001
                self.why, self.detail = "the HDMI player isn't answering", f"{type(e).__name__}: {e}"
        if ready and self.cable() is False:
            ready, self.why = False, "no HDMI cable, or the projector is off"
        return {"present": present, "ready": ready, "why": self.why}

    def load(self, path: str, from_s: float = 0.0):
        self._cmd("set_property", "pause", True)
        self._cmd("loadfile", os.path.abspath(path), "replace", f"start={from_s:.3f},pause=yes")

    def play(self):
        self._cmd("set_property", "pause", False)

    def pause(self):
        self._cmd("set_property", "pause", True)

    def seek(self, pos: float):
        self._cmd("seek", max(0.0, pos), "absolute+exact")

    def speed(self, rate: float):
        self._cmd("set_property", "speed", rate)

    def black(self):
        self._cmd("stop")                              # mpv idles on a black window (--idle --force-window)

    def stop(self):
        try:
            self._cmd("stop")
        except Exception:  # noqa: BLE001
            pass

    def position(self):
        pos = self._cmd("get_property", "time-pos")
        paused = self._cmd("get_property", "pause")
        return (float(pos) if pos is not None else None), not paused


# ---- a show's video, on the show's clock ---------------------------------------------------------
class ShowVideo:
    """One output's video cues, played on the show's clock. `items`: [{"t", "end_t", "path",
    "from_s", "end": "black"|"hold", "id"}] in show seconds.

    On the robot it runs in ITS OWN THREAD (begin/go/end), because a Cast load can block for seconds
    and the runner's loop must never wait on a projector: begin() loads the first video while the arms
    travel to their start pose, go(show_clock) starts following the show's clock (negative before the
    show's t0, so a video at t=0 is started early by the output's lead), end(status) stops it — black,
    or for an e-stop a plain stop. prepare() and tick(show_t) are the same steps, called directly
    (tests, the CLI)."""

    def __init__(self, out, items: list, log=None):
        self.out, self.items, self.log = out, sorted(items, key=lambda x: x["t"]), log or (lambda *a, **k: None)
        self.i, self.loaded, self.started, self.done = 0, None, False, False
        self.last_check = self.last_seek = -1e9
        self.err_s, self.seeks, self.nudged, self.failed = None, 0, False, None
        self.position_s = None
        self._clock, self._stop, self._thread = None, threading.Event(), None
        self._end_status = None

    def begin(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"video-{self.out.kind}")
        self._thread.start()

    def go(self, show_clock):
        self._clock = show_clock

    def _loop(self):
        self.prepare()
        while not self._stop.is_set():
            if self._clock is not None:
                self.tick(self._clock())
            self._stop.wait(0.05)
        self._finish(self._end_status or "COMPLETED")

    def _safe(self, what, fn, *a):
        if self.failed:
            return None
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001  video trouble never stops the show
            self.failed = f"{what}: {type(e).__name__}: {e}"
            self.log("video_error", output=self.out.kind, error=self.failed)
            return None

    def prepare(self):
        if self.items:
            it = self.items[0]
            self._safe("load", self.out.load, it["path"], it["from_s"])
            self.loaded = 0

    def tick(self, show_t: float):
        if self.failed or self.i >= len(self.items):
            return
        it = self.items[self.i]
        if self.loaded != self.i and show_t >= it["t"] - PRELOAD_S:
            self._safe("load", self.out.load, it["path"], it["from_s"])
            self.loaded = self.i
        if not self.started and show_t >= it["t"] - self.out.lead_s:
            self._safe("play", self.out.play)
            self.started, self.last_seek = True, show_t
            self.log("video", output=self.out.kind, note=f"{it['id']} playing", show_t=round(show_t, 3))
        if self.started and show_t >= it["end_t"]:
            self._safe("end", self.out.black if it.get("end", "black") == "black" else self.out.pause)
            self.log("video", output=self.out.kind, note=f"{it['id']} ended ({it.get('end', 'black')})",
                     show_t=round(show_t, 3))
            self.i, self.started, self.err_s, self.nudged = self.i + 1, False, None, False
            return
        if self.started and show_t - self.last_check >= CHECK_EVERY_S:
            self.last_check = show_t
            got = self._safe("position", self.out.position)
            if not got or got[0] is None:
                return
            want = it["from_s"] + (show_t - it["t"])
            self.position_s, self.err_s = got[0], got[0] - want
            if sync_action(self.err_s, show_t - self.last_seek, self.out.tol_s, self.out.min_gap_s):
                self._safe("seek", self.out.seek, want + self.out.lead_s)
                self.seeks, self.last_seek = self.seeks + 1, show_t
                self.log("video", output=self.out.kind, note="seek", err_ms=round(self.err_s * 1000), show_t=round(show_t, 3))
            elif self.out.nudge:
                # HDMI: inside the seek band, pull it back with speed; release it once inside the tolerance
                if abs(self.err_s) > HDMI_TOL_S:
                    self._safe("speed", self.out.speed, 1.0 - self.out.nudge if self.err_s > 0 else 1.0 + self.out.nudge)
                    self.nudged = True
                elif self.nudged:
                    self._safe("speed", self.out.speed, 1.0)
                    self.nudged = False

    def end(self, status: str):
        """Any end of the run. Threaded: the thread finishes it (never blocking the caller). A video
        never loaded (the run was refused first) leaves the projector alone."""
        if self._thread is None and self.loaded is None:
            self.done = True
            return
        if self._thread is not None:
            self._end_status = status
            self._stop.set()
            return
        self._finish(status)

    def _finish(self, status: str):
        if not self.done:
            self.done = True
            self.failed = None if status == "ESTOPPED" else self.failed   # always try to blank it
            self._safe("stop", self.out.stop if status == "ESTOPPED" else self.out.black)

    def state(self) -> dict:
        it = self.items[min(self.i, len(self.items) - 1)] if self.items else None
        return {"file": it["id"] if it else None, "output": self.out.kind, "position_s": self.position_s,
                "sync_err_ms": None if self.err_s is None else round(self.err_s * 1000),
                "seeks": self.seeks, "error": self.failed}


def testclip(out: str, secs: int = 60) -> int:
    """A sync test clip (H.264/AAC MP4, 1920x1080, 30 fps): ffmpeg's test pattern with its frame
    counter, a large seconds clock, and a 50 ms beep on every whole second. Played by a show beside
    the robot's own clock (or a phone's stopwatch on the dashboard), it shows the on-screen lag the
    receiver's reported position cannot: the projector's own picture processing. Needs ffmpeg (PATH, or
    imageio-ffmpeg); the clip is show media — keep it out of the repository."""
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            print("needs ffmpeg (or pip install imageio-ffmpeg)")
            return 1
    clock = ("drawtext=text='%{eif\\:t\\:d}.%{eif\\:mod(t*10\\,10)\\:d}':fontsize=360:fontcolor=white:"
             "box=1:boxcolor=black@0.6:x=(w-tw)/2:y=(h-th)/2")
    cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={secs}",
           "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*1000*t)*lt(mod(t\\,1)\\,0.05)':s=48000:d={secs}",
           "-vf", clock, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-preset", "veryfast",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", out]
    r = subprocess.run(cmd)
    if r.returncode != 0:                    # an ffmpeg without drawtext (no freetype): the pattern alone
        cmd[cmd.index("-vf"):cmd.index("-vf") + 2] = []
        r = subprocess.run(cmd)
    print(f"wrote {out}" if r.returncode == 0 else "ffmpeg failed")
    return r.returncode


# ---- the CLI: what to try when the projector arrives --------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    s = sub.add_parser("status")
    s.add_argument("--to", choices=("cast", "hdmi"), default="cast")
    s.add_argument("--device")
    p = sub.add_parser("play")
    p.add_argument("file")
    p.add_argument("--to", choices=("cast", "hdmi"), default="cast")
    p.add_argument("--device", help="the Cast device's name, or its IP address")
    p.add_argument("--secs", type=float, default=60.0)
    p.add_argument("--lead", type=float, help=f"Cast start lead, seconds (default {CAST_LEAD_S:g})")
    tc = sub.add_parser("testclip")
    tc.add_argument("out")
    tc.add_argument("--secs", type=int, default=60)
    a = ap.parse_args()

    if a.cmd == "testclip":
        return testclip(a.out, a.secs)

    def cast_for(dev, server=None):
        is_ip = bool(dev and re.match(r"^\d+\.\d+\.\d+\.\d+$", dev))
        return CastOutput(name=None if is_ip else dev, host=dev if is_ip else None, server=server)

    if a.cmd == "discover":
        found = cast_for(None).discover()
        for n, h, m in found:
            print(f"  {n!r:32s} {h:15s} {m}")
        print(f"{len(found)} Cast device(s)" if found else "no Cast device answered (mDNS) — try --device <ip>")
        return 0
    if a.cmd == "status":
        out = cast_for(a.device) if a.to == "cast" else HdmiOutput()
        if a.to == "cast":
            out.discover()
        print(json.dumps(out.status(), indent=1))
        return 0
    server = MediaServer()
    out = cast_for(a.device, server) if a.to == "cast" else HdmiOutput()
    if a.to == "cast":
        out.discover()
        if not out.cast:
            print(f"no projector: {out.why}")
            return 1
        if a.lead is not None:
            out.lead_s = a.lead
    elif not out.status()["ready"]:
        print(out.why)
        return 1
    log = lambda kind, **kw: print(kind, json.dumps(kw))   # noqa: E731
    secs = a.secs
    sv = ShowVideo(out, [{"t": 3.0, "end_t": 3.0 + secs, "path": a.file, "from_s": 0.0, "end": "black",
                          "id": os.path.basename(a.file)}], log)
    t0 = time.monotonic()
    sv.prepare()
    print(f"loaded {a.file}; it plays 3 s after load, on this computer's clock "
          f"(lead {out.lead_s:g} s); the error is the video's position minus where it should be")
    try:
        while time.monotonic() - t0 < 3.0 + secs + 1.0 and not sv.failed:
            t = time.monotonic() - t0
            sv.tick(t)
            if sv.started and sv.err_s is not None and int(t * 10) % 10 == 0:
                print(f"  t={t:6.1f}s  error {sv.err_s * 1000:+5.0f} ms  seeks {sv.seeks}")
            time.sleep(0.1)
    finally:
        sv.end("COMPLETED")
        server.close()
    print(sv.failed or "done")
    return 0 if not sv.failed else 1


if __name__ == "__main__":
    sys.exit(main())
