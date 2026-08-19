"""RETIRED (2026-08) — the Pixhawk is eliminated; RC comes from the StampPLC SBUS
and pose from the ROS fused estimate. Nothing imports this module now, so its
background thread does not start. Kept for reference/rollback only.

Live telemetry from the Pixhawk (MAVLink) — RC, GPS, and optical flow.

The Pixhawk 6C (ArduPilot) speaks binary MAVLink. This reads the messages we care
about in a background thread and keeps the latest of each with an age stamp, so
collect() is a cheap in-memory read.

SOURCE: by default this connects to MAVROS's UDP forward (the service sets
OPENDUBS_MAVLINK_DEV=udpin:127.0.0.1:14550) — MAVROS owns the Pixhawk serial port
and republishes /mavros/* for ROS/Foxglove while forwarding raw MAVLink to us.
If OPENDUBS_MAVLINK_DEV is unset it falls back to opening the by-id serial port
directly (exclusive — only valid when MAVROS is NOT running). See ros/README.md.
"""
from __future__ import annotations

import glob
import os
import threading
import time

BAUD = int(os.environ.get("OPENDUBS_MAVLINK_BAUD", "115200"))


def _find_device():
    """Resolve the Pixhawk by its stable by-id path (the ttyACM number shuffles
    across reboots / hub changes)."""
    env = os.environ.get("OPENDUBS_MAVLINK_DEV")
    if env:
        return env
    for pat in ("/dev/serial/by-id/*Pixhawk*if00", "/dev/serial/by-id/*Holybro*if00",
                "/dev/serial/by-id/*ArduPilot*if00", "/dev/serial/by-id/*PX4*if00"):
        m = sorted(glob.glob(pat))
        if m:
            return m[0]
    return "/dev/ttyACM0"

# message id -> desired rate (Hz)
WANT_RATES = {
    65: 50,    # RC_CHANNELS
    33: 5,     # GLOBAL_POSITION_INT
    24: 5,     # GPS_RAW_INT
    100: 10,   # OPTICAL_FLOW
    106: 10,   # OPTICAL_FLOW_RAD
    74: 5,     # VFR_HUD
    1: 2,      # SYS_STATUS (battery voltage/current/remaining)
    147: 1,    # BATTERY_STATUS
}
WANT_TYPES = {
    "RC_CHANNELS", "GLOBAL_POSITION_INT", "GPS_RAW_INT",
    "OPTICAL_FLOW", "OPTICAL_FLOW_RAD", "VFR_HUD",
    "SYS_STATUS", "BATTERY_STATUS",
}


class MavlinkReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._msgs = {}        # type -> (dict fields, monotonic ts)
        self._connected = False
        self._err = ""
        self._device = None
        threading.Thread(target=self._run, name="mavlink", daemon=True).start()

    def _run(self):
        import os
        while True:
            try:
                from pymavlink import mavutil
                dev = _find_device()
                m = mavutil.mavlink_connection(dev, baud=BAUD)
                with self._lock:
                    self._device = os.path.realpath(dev)
                m.wait_heartbeat(timeout=10)
                for msg_id, hz in WANT_RATES.items():
                    try:
                        m.mav.command_long_send(
                            m.target_system, m.target_component,
                            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                            msg_id, int(1_000_000 / hz), 0, 0, 0, 0, 0)
                    except Exception:
                        pass
                # legacy fallback streams
                for stream in ("MAV_DATA_STREAM_RC_CHANNELS", "MAV_DATA_STREAM_POSITION",
                               "MAV_DATA_STREAM_EXTRA1", "MAV_DATA_STREAM_EXTRA2",
                               "MAV_DATA_STREAM_EXTRA3", "MAV_DATA_STREAM_EXTENDED_STATUS"):
                    try:
                        m.mav.request_data_stream_send(
                            m.target_system, m.target_component,
                            getattr(mavutil.mavlink, stream), 10, 1)
                    except Exception:
                        pass
                with self._lock:
                    self._connected = True
                    self._err = ""
                while True:
                    msg = m.recv_match(blocking=True, timeout=5)
                    if msg is None:
                        continue
                    mt = msg.get_type()
                    if mt in WANT_TYPES:
                        d = msg.to_dict()
                        with self._lock:
                            self._msgs[mt] = (d, time.monotonic())
            except Exception as e:
                with self._lock:
                    self._connected = False
                    self._err = str(e)
                time.sleep(2)

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            out = {"connected": self._connected, "err": self._err,
                   "device": self._device, "msgs": {}}
            for mt, (d, ts) in self._msgs.items():
                out["msgs"][mt] = {"age": now - ts, **d}
            return out


# module-level singleton
reader = MavlinkReader()
