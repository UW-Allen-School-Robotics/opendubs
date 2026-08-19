"""System-health metrics for the OpenDubs kiosk dashboard.

Sources, in order of reliability on the Jetson Thor:
  - psutil            -> CPU%, per-core, memory, swap, disk, net, load, uptime
  - /sys thermal      -> temperatures (thermal_zone*)
  - tegrastats        -> GPU (GR3D) utilization + power rails (the parts psutil can't see)
  - nvpmodel          -> active power mode name

Everything degrades gracefully: if a source is missing, its fields are null
rather than raising, so the dashboard still renders.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

# --------------------------------------------------------------------------- #
# tegrastats: run once as a streaming subprocess, keep the latest parsed line.
# --------------------------------------------------------------------------- #

_RE_RAM = re.compile(r"RAM (\d+)/(\d+)MB")
_RE_SWAP = re.compile(r"SWAP (\d+)/(\d+)MB")
_RE_GR3D = re.compile(r"GR3D\d*_FREQ (\d+)%")
_RE_EMC = re.compile(r"EMC_FREQ (\d+)%")
_RE_TEMP = re.compile(r"(\w+)@([\d.]+)C")
_RE_POWER = re.compile(r"(VDD[A-Z0-9_]*|VIN[A-Z0-9_]*|POM[A-Z0-9_]*) (\d+)mW(?:/(\d+)mW)?")


class TegrastatsReader:
    """Background reader for `tegrastats`. Thread-safe latest-snapshot access."""

    def __init__(self, interval_ms: int = 1000):
        self.interval_ms = interval_ms
        self._latest: dict = {}
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._available = shutil.which("tegrastats") is not None

    def start(self) -> None:
        if not self._available:
            return
        t = threading.Thread(target=self._run, name="tegrastats", daemon=True)
        t.start()

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["tegrastats", "--interval", str(self.interval_ms)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            self._available = False
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            parsed = self._parse(line)
            with self._lock:
                self._latest = parsed

    @staticmethod
    def _parse(line: str) -> dict:
        out: dict = {"gpu_pct": None, "emc_pct": None, "power_mw": {}, "temps_c": {}}
        if m := _RE_GR3D.search(line):
            out["gpu_pct"] = int(m.group(1))
        if m := _RE_EMC.search(line):
            out["emc_pct"] = int(m.group(1))
        for name, val in _RE_TEMP.findall(line):
            out["temps_c"][name.lower()] = float(val)
        for rail, now_mw, _max in _RE_POWER.findall(line):
            out["power_mw"][rail] = int(now_mw)
        return out

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._latest)


# --------------------------------------------------------------------------- #
# Generic readers
# --------------------------------------------------------------------------- #

def _thermal_zones() -> dict:
    temps: dict = {}
    base = Path("/sys/class/thermal")
    if not base.exists():
        return temps
    for zone in sorted(base.glob("thermal_zone*")):
        try:
            name = (zone / "type").read_text().strip()
            milli = int((zone / "temp").read_text().strip())
            temps[name] = round(milli / 1000.0, 1)
        except Exception:
            continue
    return temps


def _nvidia_smi() -> dict:
    """GPU utilization + temperature via nvidia-smi (works on Thor's PCIe GPU;
    tegrastats on Thor does not emit a GR3D utilization token)."""
    out = {"pct": None, "temp_c": None, "mem_pct": None}
    if not shutil.which("nvidia-smi"):
        return out
    try:
        res = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,temperature.gpu,utilization.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip().splitlines()
        if res:
            parts = [p.strip() for p in res[0].split(",")]
            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            out["pct"] = num(parts[0]) if len(parts) > 0 else None
            out["temp_c"] = num(parts[1]) if len(parts) > 1 else None
            out["mem_pct"] = num(parts[2]) if len(parts) > 2 else None
    except Exception:
        pass
    return out


def _nvpmodel() -> str | None:
    if not shutil.which("nvpmodel"):
        return None
    try:
        out = subprocess.run(
            ["nvpmodel", "-q"], capture_output=True, text=True, timeout=2
        ).stdout
        for line in out.splitlines():
            if "NV Power Mode" in line:
                # format: "NV Power Mode: MAXN"
                return line.split(":", 1)[1].strip()
        # fallback: last non-empty line is often the mode name
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None


# nvpmodel is cached — it's slow and rarely changes.
_NVPMODEL_CACHE = {"value": None, "ts": 0.0}


def _nvpmodel_cached() -> str | None:
    now = time.time()
    if now - _NVPMODEL_CACHE["ts"] > 10:
        _NVPMODEL_CACHE["value"] = _nvpmodel()
        _NVPMODEL_CACHE["ts"] = now
    return _NVPMODEL_CACHE["value"]


_LAST_NET = {"ts": 0.0, "sent": 0, "recv": 0}


def _net_rates() -> dict:
    io = psutil.net_io_counters()
    now = time.time()
    rate = {"up_kbps": 0.0, "down_kbps": 0.0}
    if _LAST_NET["ts"]:
        dt = max(now - _LAST_NET["ts"], 1e-3)
        rate["up_kbps"] = round((io.bytes_sent - _LAST_NET["sent"]) / dt / 125.0, 1)
        rate["down_kbps"] = round((io.bytes_recv - _LAST_NET["recv"]) / dt / 125.0, 1)
    _LAST_NET.update(ts=now, sent=io.bytes_sent, recv=io.bytes_recv)
    return rate


def _primary_ip() -> str | None:
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if name == "lo":
                continue
            for a in addrs:
                if a.family.name == "AF_INET" and not a.address.startswith("127."):
                    return a.address
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

_tegra = TegrastatsReader()
_tegra.start()


def collect() -> dict:
    """Return one full system-health snapshot as a JSON-serializable dict."""
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    tg = _tegra.snapshot()

    temps = _thermal_zones()
    if tg.get("temps_c"):
        # tegrastats temps win where present (often more specific names)
        temps = {**temps, **tg["temps_c"]}

    gpu = _nvidia_smi()
    if gpu["pct"] is None:  # fall back to tegrastats GR3D if a board reports it
        gpu["pct"] = tg.get("gpu_pct")
    if gpu["temp_c"] is not None:
        temps["gpu"] = gpu["temp_c"]

    return {
        "ts": time.time(),
        "host": {
            "ip": _primary_ip(),
            "uptime_s": int(time.time() - psutil.boot_time()),
            "power_mode": _nvpmodel_cached(),
        },
        "cpu": {
            "pct": psutil.cpu_percent(interval=None),
            "per_core": psutil.cpu_percent(interval=None, percpu=True),
            "load": list(psutil.getloadavg()),
            "cores": psutil.cpu_count(),
        },
        "gpu": {
            "pct": gpu["pct"],
            "temp_c": gpu["temp_c"],
            "emc_pct": tg.get("emc_pct"),
        },
        "mem": {
            "used_mb": round(vm.used / 1e6),
            "total_mb": round(vm.total / 1e6),
            "pct": vm.percent,
        },
        "swap": {
            "used_mb": round(sw.used / 1e6),
            "total_mb": round(sw.total / 1e6),
            "pct": sw.percent,
        },
        "disk": {
            "used_gb": round(disk.used / 1e9, 1),
            "total_gb": round(disk.total / 1e9, 1),
            "pct": disk.percent,
        },
        "temps_c": temps,
        "power_mw": tg.get("power_mw", {}),
        "net": {**_net_rates(), "ip": _primary_ip()},
    }
