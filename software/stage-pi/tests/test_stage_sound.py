"""The stage box's choice of sound output (bin/stage_sound.py), on a desk: run with
    python3 -m pytest -q software/stage-pi/tests
The atrium's video wall has no speaker, so `auto` must read that from the screen and play elsewhere."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import stage_sound as ss


def _edid(audio_bit=False, audio_block=False):
    base = bytearray(128)
    base[126] = 1                                          # one extension block
    ext = bytearray(128)
    ext[0], ext[1] = 0x02, 3
    dbc = bytes([(1 << 5) | 3, 0x09, 0x07, 0x07]) if audio_block else bytes([(2 << 5) | 1, 0x10])
    ext[2] = 4 + len(dbc)
    ext[3] = 0x40 if audio_bit else 0
    ext[4:4 + len(dbc)] = dbc
    return bytes(base + ext)


def test_a_screen_says_whether_it_has_a_speaker():
    assert ss.edid_has_audio(_edid(audio_bit=True))
    assert ss.edid_has_audio(_edid(audio_block=True))
    assert not ss.edid_has_audio(_edid())                 # a video wall: no basic audio, no audio block
    assert not ss.edid_has_audio(bytes(128))              # no extension at all (a DVI monitor)


def test_auto_skips_a_silent_screen_and_a_missing_choice_falls_back():
    assert ss.choose("auto", {"hdmi", "usb", "bt"}, hdmi_audio=False) == ("usb", None)
    assert ss.choose("auto", {"hdmi", "usb"}, hdmi_audio=True) == ("hdmi", None)
    assert ss.choose("auto", {"bt", "jack"}, hdmi_audio=False) == ("bt", None)
    assert ss.choose("bt", {"usb"}, hdmi_audio=False) == ("usb", "bt is not available")
    assert ss.choose("hdmi", {"hdmi"}, hdmi_audio=False) == ("hdmi", None)   # forced: played as asked
    assert ss.choose("auto", set(), hdmi_audio=False) == (None, "no sound output found")


def _node(i, cls, **props):
    return {"id": i, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {"media.class": cls, **props}}}


def test_pipewire_sinks_are_told_apart_and_the_screen_less_hdmi_port_is_ignored():
    dump = [
        _node(40, "Audio/Sink", **{"node.name": "alsa_output.platform-107c701400.hdmi.hdmi-stereo", "alsa.card_name": "vc4-hdmi-0"}),
        _node(41, "Audio/Sink", **{"node.name": "alsa_output.platform-107c706400.hdmi.hdmi-stereo", "alsa.card_name": "vc4-hdmi-1",
                                    "node.description": "Built-in Audio HDMI 2"}),
        _node(42, "Audio/Sink", **{"node.name": "alsa_output.usb-C-Media_USB_Audio_Device-00.analog-stereo", "node.description": "USB Audio Device"}),
        _node(43, "Audio/Sink", **{"node.name": "bluez_output.AA_BB_CC_DD_EE_FF.1", "device.api": "bluez5", "node.description": "Soundcore Boom 2 Plus"}),
        _node(44, "Audio/Sink", **{"node.name": "auto_null"}),
        _node(50, "Stream/Output/Audio", **{"application.name": "Chromium"}),
        {"id": 60, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 50, "input-node-id": 42}},
        {"id": 0, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
         "metadata": [{"key": "default.audio.sink", "value": {"name": "bluez_output.AA_BB_CC_DD_EE_FF.1"}}]},
    ]
    p = ss.parse_dump(dump, [{"port": 2, "card": "vc4hdmi1", "audio": True}])
    assert {k: s["id"] for k, s in p["sinks"].items()} == {"hdmi": 41, "usb": 42, "bt": 43}
    assert p["default"] == "bluez_output.AA_BB_CC_DD_EE_FF.1"
    assert p["playing"] == [{"app": "Chromium", "kind": "usb", "running": True}]


def test_kept_settings_survive_junk(tmp_path):
    path = tmp_path / "audio.json"
    path.write_text('{"output": "loud", "volumes": {"usb": 3, "bt": true}, "bt": {"addr": "nope"}}')
    s = ss.load(str(path), "bt")
    assert s["output"] == "bt" and s["volumes"]["usb"] == 1.0 and s["volumes"]["bt"] == ss.DEFAULT_VOLUME and s["bt"] is None
    ss.save(str(path), {**s, "bt": {"addr": "aa:bb:cc:dd:ee:ff", "name": "x"}})
    assert ss.load(str(path), "auto")["bt"] == {"addr": "AA:BB:CC:DD:EE:FF", "name": "x"}


def test_a_soft_blocked_bluetooth_radio_is_lifted_and_nothing_else(tmp_path):
    """The box came up with Bluetooth soft-blocked at every boot (2026-10-26): Powered no, pairing failed."""
    for name, kind, soft, hard in (("rfkill0", "bluetooth", "1", "0"), ("rfkill1", "wlan", "1", "0"),
                                   ("rfkill2", "bluetooth", "0", "1")):
        d = tmp_path / name
        d.mkdir()
        (d / "type").write_text(kind + "\n")
        (d / "soft").write_text(soft + "\n")
        (d / "hard").write_text(hard + "\n")
    assert ss.rfkill_unblock(str(tmp_path)) == ["rfkill0"]
    assert (tmp_path / "rfkill0/soft").read_text() == "0"
    assert (tmp_path / "rfkill1/soft").read_text().strip() == "1"          # the Wi-Fi is not ours
    assert ss.rfkill_unblock(str(tmp_path)) == []                           # already lifted: nothing to do
    assert ss.adapter_state("Controller AA:BB\n\tPowered: no\n\tPowerState: off-blocked\n") == "blocked"
    assert ss.adapter_state("Controller AA:BB\n\tPowered: yes\n") == "on"
    assert ss.adapter_state("No default controller available\n") == "none"


def _bt_fake(monkeypatch, paired_after_pair, known=""):
    calls = []
    def btctl(*args, timeout=10):
        calls.append(args)
        return known if args[:1] == ("devices",) else ""
    state = {"paired": False}
    def info(addr, text=None):
        if any(c[-2:] == ("pair", addr) for c in calls):
            state["paired"] = paired_after_pair
        return {"addr": addr, "name": "Boom", "paired": state["paired"], "connected": state["paired"], "audio": True}
    monkeypatch.setattr(ss, "_btctl", btctl)
    monkeypatch.setattr(ss, "bt_info", info)
    return calls


def test_pairing_is_bondable_only_for_the_attempt_and_a_failed_one_is_forgotten(tmp_path, monkeypatch):
    """BlueZ 5.82 kept no link key with pairable off: "Pairing successful", then Paired: no (2026-10-26).
    And a failed attempt left the device trusted, so BlueZ kept retrying it (the Nebula projector)."""
    calls = _bt_fake(monkeypatch, paired_after_pair=False)
    snd = ss.Sound(str(tmp_path / "nomedia"), str(tmp_path), "auto")
    calls.clear()
    assert not snd._pair("00:7F:1D:F8:B1:9D")["ok"]
    assert calls[0] == ("pairable", "on") and calls[-1] == ("pairable", "off")
    assert ("trust", "00:7F:1D:F8:B1:9D") not in calls and ("remove", "00:7F:1D:F8:B1:9D") in calls
    assert snd.settings["bt"] is None
    calls = _bt_fake(monkeypatch, paired_after_pair=True)
    r = snd._pair("00:7F:1D:F8:B1:9D")
    assert r["ok"] and calls[-1] == ("pairable", "off") and ("trust", "00:7F:1D:F8:B1:9D") in calls
    assert snd.settings["bt"]["addr"] == "00:7F:1D:F8:B1:9D"


def test_on_start_only_the_kept_speaker_stays_known(tmp_path, monkeypatch):
    known = "Device 00:7F:1D:4A:AB:6A Nebula\nDevice 00:7F:1D:F8:B1:9D soundcore Boom 2 Plus\n"
    calls = _bt_fake(monkeypatch, paired_after_pair=True, known=known)
    (tmp_path / "audio.json").write_text('{"bt": {"addr": "00:7F:1D:F8:B1:9D", "name": "Boom"}}')
    ss.Sound(str(tmp_path / "nomedia"), str(tmp_path), "auto")
    assert ("remove", "00:7F:1D:4A:AB:6A") in calls and ("remove", "00:7F:1D:F8:B1:9D") not in calls
