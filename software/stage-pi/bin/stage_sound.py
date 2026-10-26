"""The stage box's sound: ONE selectable output for the show's music and sound (the user, 2026-10-24).
Dialogue stays on the robot's head tablet; this is the music. The outputs:

  hdmi   the screen's own speaker (a projector or a TV), on the HDMI port a screen is on
  jack   the Pi's 3.5 mm jack, to a speaker's aux input. A RASPBERRY PI 5 HAS NO JACK: only the Pi 4 does
  usb    a class-compliant USB audio adapter (e.g. Sabrent AU-MMSA), to a speaker's aux input
  bt     a Bluetooth speaker (e.g. Soundcore Boom 2 Plus), paired to the box from the dashboard
  auto   hdmi if the screen says it has sound (its EDID), else usb if plugged in, else bt if paired and
         connected, else the jack. A building's video wall with no speaker reports none, so auto skips it.

HOW: PipeWire with WirePlumber, Raspberry Pi OS's own sound stack, with bluez5 for Bluetooth. Chromium
plays to PipeWire's DEFAULT sink, and this sets that default. So the output changes live: the page never
reloads, its video and its clock sync are untouched. A test tone mixes in beside the page. And a speaker
that drops out leaves the sound on another sink, not on a dead device. PipeWire runs in the `stage`
user's session (lingering, so it is up before the browser); this runs as root and asks it with wpctl,
pw-dump and pw-play as that user.

WHAT IS KEPT, on the media stick (/srv/stage-media/.state, the box's one writable store, which the media
sync never touches): the chosen output, a volume per output, and the paired speaker (audio.json); and
bluetoothd's pairing keys (stage-media binds .state/bluetooth onto /var/lib/bluetooth). So they survive a
reboot and a re-imaged card. With no stick they last until the next reboot, and the report says so. The
default when nothing is kept: stage.conf's AUDIO.

Typical added latency per output, for the stage page's audio lead (LATENCY_MS): a starting point; the lead
set by ear on the dashboard stays the fine adjustment.
"""
from __future__ import annotations

import json
import math
import os
import pwd
import re
import struct
import subprocess
import threading
import time
import wave

OUTPUTS = ("hdmi", "jack", "usb", "bt")
SETTINGS = ("auto",) + OUTPUTS
AUTO_ORDER = ("hdmi", "usb", "bt", "jack")
LATENCY_MS = {"hdmi": 40, "jack": 20, "usb": 20, "bt": 200}
DEFAULT_VOLUME = 0.7
MAX_VOLUME = 1.0                       # no boost past the sink's own 100 %
USER = "stage"
RECONNECT_EVERY_S = 20
SCAN_S = 15
REFIND_S, REFIND_TRIES = 3, 4            # a speaker BlueZ forgot since the scan: look again, up to ~12 s

lock = threading.Lock()
live = {"sinks": {}, "actual": None, "why": None, "playing": [], "bt": None, "found": [],
        "busy": None, "error": None, "applied": {}, "default": None, "adapter": None}


# ---- settings ----------------------------------------------------------------------------------------

def state_dir(media: str, run: str) -> tuple[str, bool]:
    """Where the settings live, and whether they are kept: the media stick's .state, else /run (lost at
    the next reboot)."""
    if os.path.ismount(media):
        d = os.path.join(media, ".state")
        try:
            os.makedirs(d, exist_ok=True)
            return d, True
        except OSError:
            pass
    return run, False


def load(path: str, default_output: str) -> dict:
    s = {"output": default_output if default_output in SETTINGS else "auto",
         "volumes": {k: DEFAULT_VOLUME for k in OUTPUTS}, "bt": None}
    try:
        with open(path) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return s
    if got.get("output") in SETTINGS:
        s["output"] = got["output"]
    for k, v in (got.get("volumes") or {}).items():
        if k in OUTPUTS and isinstance(v, (int, float)) and not isinstance(v, bool):
            s["volumes"][k] = max(0.0, min(MAX_VOLUME, float(v)))
    bt = got.get("bt")
    if isinstance(bt, dict) and is_addr(bt.get("addr")):
        s["bt"] = {"addr": bt["addr"].upper(), "name": str(bt.get("name") or "")[:64]}
    return s


def save(path: str, s: dict) -> None:
    """Atomically, and synced: the box's power can be pulled at any moment."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass


def is_addr(a) -> bool:
    parts = str(a or "").split(":")
    return len(parts) == 6 and all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


# ---- the screens -------------------------------------------------------------------------------------

def edid_has_audio(edid: bytes) -> bool:
    """Whether a screen says it can play sound: a CEA-861 extension block (tag 2) with its basic-audio bit
    (byte 3, bit 6) or an Audio Data Block (tag 1) in its data block collection."""
    for i in range(1, len(edid) // 128):
        b = edid[i * 128:(i + 1) * 128]
        if b[0] != 0x02 or len(b) < 4:
            continue
        if b[3] & 0x40:
            return True
        end, j = min(b[2], 127), 4
        while j < end:
            tag, n = b[j] >> 5, b[j] & 0x1F
            if tag == 1:
                return True
            j += 1 + n
    return False


def screens(drm: str = "/sys/class/drm") -> list[dict]:
    """Each connected HDMI screen: its port (HDMI-A-1 is the Pi's vc4-hdmi-0) and whether it has sound."""
    out = []
    try:
        names = sorted(n for n in os.listdir(drm) if "-HDMI-A-" in n)
    except OSError:
        return out
    for n in names:
        try:
            with open(f"{drm}/{n}/status") as f:
                if f.read().strip() != "connected":
                    continue
            with open(f"{drm}/{n}/edid", "rb") as f:
                edid = f.read()
        except OSError:
            edid = b""
        port = int(n.rsplit("-", 1)[1])
        out.append({"port": port, "card": f"vc4hdmi{port - 1}", "audio": edid_has_audio(edid)})
    return out


# ---- PipeWire ----------------------------------------------------------------------------------------

def _as_stage(args: list[str], timeout: float = 5, user: str = USER) -> subprocess.CompletedProcess:
    uid = pwd.getpwnam(user).pw_uid
    return subprocess.run(["runuser", "-u", user, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}", *args],
                          capture_output=True, text=True, timeout=timeout)


def card_id(props: dict) -> str:
    """A sink's sound card, letters and digits only: vc4-hdmi-0 and vc4hdmi0 are the same card."""
    card = str(props.get("alsa.card_name") or props.get("api.alsa.card.name") or "")
    return "".join(c for c in card.lower() if c.isalnum())


def kind_of(props: dict) -> str | None:
    """Which of our outputs a PipeWire sink is, from its properties; None for anything else."""
    name = str(props.get("node.name", ""))
    card = str(props.get("alsa.card_name") or props.get("api.alsa.card.name") or "")
    if props.get("device.api") == "bluez5" or name.startswith("bluez_output."):
        return "bt"
    if props.get("device.bus") == "usb" or name.startswith("alsa_output.usb-"):
        return "usb"
    if "hdmi" in card.lower() or ".hdmi" in name:
        return "hdmi"
    if "headphones" in card.lower() or "bcm2835" in card.lower():
        return "jack"
    return None


def parse_dump(dump: list, shown: list[dict]) -> dict:
    """From `pw-dump`: {"sinks": {kind: {id, name, desc, volume}}, "default": node name or None,
    "playing": [{"app", "sink", "kind", "running"}]}. Of the HDMI sinks, the one on a port with a screen."""
    nodes, sinks, links, default = {}, {}, [], None
    hdmi_cards = {s["card"] for s in shown}
    for o in dump if isinstance(dump, list) else []:
        t, info = o.get("type", ""), o.get("info") or {}
        props = info.get("props") or o.get("props") or {}      # a metadata object keeps its props at the top
        if t == "PipeWire:Interface:Node":
            nodes[o.get("id")] = {"props": props, "state": info.get("state")}
            if props.get("media.class") != "Audio/Sink":
                continue
            k = kind_of(props)
            if k is None:
                continue
            if k == "hdmi" and hdmi_cards and card_id(props) not in hdmi_cards:
                continue                                  # the HDMI port with no screen on it
            if k in sinks and k != "hdmi":
                continue
            sinks[k] = {"id": o.get("id"), "name": props.get("node.name"),
                        "desc": props.get("node.description") or props.get("node.nick") or k}
        elif t == "PipeWire:Interface:Link":
            links.append((info.get("output-node-id"), info.get("input-node-id")))
        elif t == "PipeWire:Interface:Metadata" and props.get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") == "default.audio.sink":
                    v = m.get("value")
                    default = (v or {}).get("name") if isinstance(v, dict) else None
    by_id = {s["id"]: k for k, s in sinks.items()}
    playing = []
    for out_id, in_id in sorted(set(links), key=str):
        src = nodes.get(out_id)
        if not src or src["props"].get("media.class") != "Stream/Output/Audio" or in_id not in by_id:
            continue
        playing.append({"app": src["props"].get("application.name") or src["props"].get("node.name") or "?",
                        "kind": by_id[in_id], "running": src["state"] == "running"})
    return {"sinks": sinks, "default": default, "playing": playing}


def choose(setting: str, available: set, hdmi_audio: bool) -> tuple[str | None, str | None]:
    """The output to use, and why it is not the one chosen (None when it is)."""
    if setting in OUTPUTS:
        if setting in available:
            return setting, None
        why = f"{setting} is not available"
    else:
        why = None
    for k in AUTO_ORDER:
        if k == "hdmi" and not hdmi_audio:
            continue                                      # a screen with no speaker (a video wall)
        if k in available:
            return k, why
    return None, why or "no sound output found"


# ---- Bluetooth ---------------------------------------------------------------------------------------

def _btctl(*args: str, timeout: float = 10) -> str:
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def bt_info(addr: str, text: str | None = None) -> dict:
    """`bluetoothctl info ADDR`, as {name, addr, paired, connected, audio, known}. `known`: BlueZ still has
    the device (it drops one it discovered but never paired ~30 s after discovery stops)."""
    text = _btctl("info", addr) if text is None else text
    d = {"addr": addr, "name": None, "paired": False, "connected": False, "audio": False,
         "known": text.lstrip().startswith("Device ") and "not available" not in text}
    for line in text.splitlines():
        k, _, v = line.strip().partition(": ")
        if k in ("Name", "Alias") and not d["name"]:
            d["name"] = v.strip()
        elif k == "Paired":
            d["paired"] = v.strip() == "yes"
        elif k == "Connected":
            d["connected"] = v.strip() == "yes"
        elif (k == "Icon" and v.startswith("audio")) or (k == "UUID" and "Audio Sink" in v):
            d["audio"] = True
    return d


def bt_devices(text: str) -> list[tuple[str, str]]:
    """`bluetoothctl devices`: [(addr, name)]."""
    out = []
    for line in text.splitlines():
        p = line.split(" ", 2)
        if len(p) >= 2 and p[0] == "Device" and is_addr(p[1]):
            out.append((p[1].upper(), p[2].strip() if len(p) > 2 else ""))
    return out


def rfkill_unblock(base: str = "/sys/class/rfkill") -> list[str]:
    """Lift the SOFT block on every Bluetooth radio, through sysfs (no rfkill tool, no package). A fresh
    Raspberry Pi OS keeps Bluetooth soft-blocked, and systemd-rfkill restores that at every boot from the
    root, which the read-only overlay never lets change (found 2026-10-26: Powered: no, PowerState:
    off-blocked, "Failed to set mode: Failed (0x03)" at every pairing). So it is lifted here, at start and
    on every check, whatever put it back. A hard block (a switch) is not ours to lift. Returns what it lifted."""
    done = []
    try:
        radios = sorted(os.listdir(base))
    except OSError:
        return done
    for r in radios:
        try:
            with open(f"{base}/{r}/type") as f:
                if f.read().strip() != "bluetooth":
                    continue
            with open(f"{base}/{r}/soft") as f:
                if f.read().strip() != "1":
                    continue
            with open(f"{base}/{r}/soft", "w") as f:
                f.write("0")
            done.append(r)
        except OSError:
            continue
    return done


def adapter_state(show: str) -> str:
    """`bluetoothctl show` -> "on", "off", "blocked" (rfkill), or "none" (no adapter)."""
    if not show.strip() or "No default controller" in show:
        return "none"
    powered = re.search(r"^\s*Powered:\s*(yes|no)", show, re.M)
    if "off-blocked" in show:
        return "blocked"
    return "on" if powered and powered.group(1) == "yes" else "off"


# ---- the test tone -----------------------------------------------------------------------------------

def write_tone(path: str, rate: int = 48000) -> None:
    """Two short rising beeps, 0.7 s, at a quarter of full scale (-12 dBFS), faded in and out so they
    don't click. Loud enough to hear across a room at the output's own volume, never a blast."""
    frames = bytearray()
    for f, dur in ((660.0, 0.25), (0.0, 0.1), (880.0, 0.35)):
        n = int(rate * dur)
        for i in range(n):
            env = min(1.0, i / (0.01 * rate), (n - i) / (0.02 * rate)) if f else 0.0
            v = int(0.25 * 32767 * env * math.sin(2 * math.pi * f * i / rate))
            frames += struct.pack("<hh", v, v)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


# ---- the controller ----------------------------------------------------------------------------------

class Sound:
    """Applies the setting (every few seconds, so a speaker plugged in or switched on is taken), keeps a
    paired speaker connected, runs the robot's commands, and reports what is actually playing where."""

    def __init__(self, media: str, run: str, default_output: str, robot_busy=lambda: False):
        self.media, self.run, self.robot_busy = media, run, robot_busy
        self.default_output = default_output
        self.dir, self.kept = state_dir(media, run)
        self.settings = load(os.path.join(self.dir, "audio.json"), default_output)
        self.last_reconnect = 0.0
        self.results: dict[str, dict] = {}
        self._tidy()

    def _tidy(self) -> None:
        """One speaker: forget every device BlueZ knows as paired or trusted that is not the kept one, so
        nothing else is retried in the background (a failed pairing used to leave its device trusted)."""
        keep = (self.settings["bt"] or {}).get("addr")
        for kind in ("Paired", "Trusted"):
            for addr, _ in bt_devices(_btctl("devices", kind)):
                if addr != keep:
                    _btctl("remove", addr)

    def _save(self) -> None:
        try:
            save(os.path.join(self.dir, "audio.json"), self.settings)
        except OSError as e:
            live["error"] = f"settings not saved: {e}"[:160]

    # -- the loop --
    def tick(self) -> None:
        if rfkill_unblock():                               # Bluetooth was blocked: lift it, then power it
            _btctl("power", "on")
        live["adapter"] = adapter_state(_btctl("show"))
        if live["adapter"] == "off":
            _btctl("power", "on")
        shown = screens()
        try:
            r = _as_stage(["pw-dump"], timeout=8)
            dump = json.loads(r.stdout or "[]")
        except (OSError, ValueError, subprocess.SubprocessError, KeyError) as e:
            with lock:
                live.update(error=f"PipeWire not answering: {e}"[:160], sinks={}, actual=None)
            return
        p = parse_dump(dump, shown)
        sinks = p["sinks"]
        actual, why = choose(self.settings["output"], set(sinks), any(s["audio"] for s in shown))
        if actual and sinks[actual]["name"] != p["default"]:
            _as_stage(["wpctl", "set-default", str(sinks[actual]["id"])])
        for k, s in sinks.items():                        # a sink that (re)appears gets its kept volume
            want = self.settings["volumes"].get(k, DEFAULT_VOLUME)
            if live["applied"].get(k) != (s["id"], want):
                _as_stage(["wpctl", "set-volume", str(s["id"]), f"{want:.2f}"])
                live["applied"][k] = (s["id"], want)
        self._keep_connected("bt" in sinks)
        with lock:
            live.update(sinks=sinks, actual=actual, why=why, playing=p["playing"], default=p["default"], error=None)

    def _keep_connected(self, connected: bool) -> None:
        bt = self.settings["bt"]
        if not bt:
            live["bt"] = None
            return
        info = bt_info(bt["addr"])
        live["bt"] = {"addr": bt["addr"], "name": info["name"] or bt["name"],
                      "paired": info["paired"], "connected": info["connected"] or connected}
        if (not info["connected"] and info["paired"] and live["busy"] is None
                and self.settings["output"] in ("bt", "auto")
                and time.monotonic() - self.last_reconnect > RECONNECT_EVERY_S):
            self.last_reconnect = time.monotonic()
            _btctl("connect", bt["addr"], timeout=15)

    # -- the robot's commands --
    def command(self, c: dict) -> dict:
        """One command from the robot: {"id", "op", ...}. Returns {"ok", "text"}."""
        op = c.get("op")
        try:
            if op == "output":
                v = c.get("value")
                if v not in SETTINGS:
                    return {"ok": False, "text": f"output must be one of {', '.join(SETTINGS)}"}
                if self.robot_busy():
                    return {"ok": False, "text": "a show is loaded or playing: the output stays as it is"}
                self.settings["output"] = v
                self._save()
                return {"ok": True, "text": f"output: {v}"}
            if op == "volume":
                k, v = c.get("output"), c.get("value")
                if k not in OUTPUTS or not isinstance(v, (int, float)) or isinstance(v, bool):
                    return {"ok": False, "text": "volume needs an output and a value 0..1"}
                self.settings["volumes"][k] = max(0.0, min(MAX_VOLUME, float(v)))
                self._save()
                return {"ok": True, "text": f"{k} volume {round(self.settings['volumes'][k] * 100)} %"}
            if op != "volume" and self.robot_busy():
                return {"ok": False, "text": "a show is loaded or playing: only the volume changes now"}
            if op == "scan":
                return self._scan()
            if op == "pair":
                return self._pair(str(c.get("addr") or "").upper())
            if op == "forget":
                return self._forget()
            if op == "test":
                return self._test()
            return {"ok": False, "text": f"unknown op {op!r}"}
        finally:
            live["busy"] = None

    def _scan(self) -> dict:
        live["busy"] = "scanning"
        _btctl("power", "on")
        _btctl("--timeout", str(SCAN_S), "scan", "on", timeout=SCAN_S + 5)
        found = []
        for addr, name in bt_devices(_btctl("devices")):
            d = bt_info(addr)
            if d["audio"]:
                found.append({"addr": addr, "name": d["name"] or name or addr})
        live["found"] = found
        return {"ok": True, "text": f"{len(found)} speaker(s) found" if found
                else "no speaker found: is it switched on and in pairing mode (its Bluetooth light flashing)?"}

    def _pair(self, addr: str) -> dict:
        if not is_addr(addr):
            return {"ok": False, "text": "pair needs a speaker's address (scan first)"}
        live["busy"] = "pairing"
        old = self.settings["bt"]
        if old and old["addr"] != addr:
            _btctl("remove", old["addr"])                  # one speaker: the new one replaces the old
        # PAIRABLE (BlueZ's "bondable") only for this attempt: without it the speaker pairs, but no link key
        # is kept and it reads Paired: no at once (found 2026-10-26, BlueZ 5.82). Off again whatever happens;
        # the box is never discoverable either way.
        _btctl("pairable", "on")
        try:
            # A person picks from the list after the scan has ended, and BlueZ forgets a device it found but
            # never paired ~30 s after that: then `pair` answers "not available" (2026-10-26). So find it
            # again first, in short rounds, and pair the moment it is back.
            for _ in range(REFIND_TRIES):
                if bt_info(addr)["known"]:
                    break
                _btctl("--timeout", str(REFIND_S), "scan", "on", timeout=REFIND_S + 5)
            else:
                if not bt_info(addr)["known"]:
                    return {"ok": False, "text": "the speaker is not in range any more: put it in pairing mode, "
                                                 "then press PAIR SPEAKER again"}
            _btctl("--agent", "NoInputNoOutput", "--timeout", "25", "pair", addr, timeout=30)
            d = bt_info(addr)
            if not d["paired"]:
                # Not trusted, and forgotten: a device left trusted is one BlueZ keeps trying to reconnect
                # (the Nebula projector, retried every ~40 s after a failed attempt).
                _btctl("remove", addr)
                return {"ok": False, "text": "pairing failed: put the speaker in pairing mode and try again"}
            _btctl("trust", addr)
            _btctl("connect", addr, timeout=20)
            d = bt_info(addr)
        finally:
            _btctl("pairable", "off")
        self.settings["bt"] = {"addr": addr, "name": d["name"] or addr}
        self._save()
        kept = "" if self.kept else " (not kept after a reboot: no media stick)"
        return {"ok": True, "text": f"paired with {d['name'] or addr}" + ("" if d["connected"] else ", not yet connected") + kept}

    def _forget(self) -> dict:
        bt = self.settings["bt"]
        if not bt:
            return {"ok": True, "text": "no speaker paired"}
        _btctl("remove", bt["addr"])
        self.settings["bt"] = None
        self._save()
        return {"ok": True, "text": f"forgot {bt['name'] or bt['addr']}"}

    def _test(self) -> dict:
        if self.robot_busy():
            return {"ok": False, "text": "a show is loaded or playing: no test sound now"}
        with lock:
            actual, sinks = live["actual"], dict(live["sinks"])
        if not actual:
            return {"ok": False, "text": "no sound output to test"}
        live["busy"] = "testing"
        tone = os.path.join(self.run, "tone.wav")
        if not os.path.exists(tone):
            write_tone(tone)
        r = _as_stage(["pw-play", "--target", sinks[actual]["name"], tone], timeout=10)
        if r.returncode != 0:
            return {"ok": False, "text": f"the tone did not play on {actual}: {(r.stderr or '').strip()[:120]}"}
        return {"ok": True, "text": f"played on {actual} ({sinks[actual]['desc']})"}

    # -- the report --
    def report(self) -> dict:
        with lock:
            a = live["actual"]
            return {"setting": self.settings["output"], "actual": a, "why": live["why"],
                    "name": live["sinks"][a]["desc"] if a else None,
                    "latency_ms": LATENCY_MS.get(a), "latency_by_output": LATENCY_MS,
                    "available": sorted(live["sinks"]), "volumes": self.settings["volumes"],
                    "playing": live["playing"], "bt": live["bt"], "found": live["found"],
                    "busy": live["busy"], "kept": self.kept, "error": live["error"], "adapter": live["adapter"]}
