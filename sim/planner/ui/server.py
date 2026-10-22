#!/usr/bin/env python3
"""OpenArm Motion Planner Studio & Halloween Show Designer.

Web backend providing interactive 3D pose jogging, collision checking,
analytical SSIK inverse kinematics, CBiRRT trajectory planning, and
Halloween choreo export.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
HTTPServer = ThreadingHTTPServer
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLANNER_DIR = os.path.dirname(HERE)
SIM_DIR = os.path.dirname(PLANNER_DIR)
REPO = os.path.dirname(SIM_DIR)
IK_DIR = os.path.join(SIM_DIR, "ik_artifacts")
VENDOR_DIR = os.path.join(REPO, "software", "kiosk-dashboard", "frontend", "vendor")
ARM_ASSETS_DIR = os.path.join(REPO, "description", "openarm")
STATIC_DIR = os.path.join(HERE, "static")
SAVED_KEYFRAMES_PATH = os.path.join(HERE, "saved_keyframes.json")

for p in (REPO, SIM_DIR, IK_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import OpenArmPlanner, get_available_backends
from sim.planner.benchmark import run_benchmark
from sim.planner.hardware_bridge import HARDWARE_BRIDGE, validate_trajectory_safety
from sim.planner.choreo_io import (
    list_available_choreos,
    parse_choreo_content,
    save_user_choreo,
    delete_user_choreo,
    load_choreo_by_path_or_id,
)
import clearance
import openarm_left_ik
import openarm_right_ik

# Built-in presets (joint angles in degrees)
PRESETS_DEG = {
    "zero": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "stow": [0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0],
    "reach_forward": [0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0],
    "high_reach": [0.0, -50.0, 0.0, 45.0, 0.0, 20.0, 0.0],
    "wave_ready": [20.0, -20.0, 30.0, 70.0, -10.0, 20.0, 0.0],
    # Halloween themed presets
    "zombie_reach": [25.0, -15.0, 0.0, 50.0, 0.0, 0.0, 0.0],
    "spooky_wave": [20.0, -40.0, 30.0, 70.0, 0.0, 20.0, 0.0],
    "pumpkin_offer": [10.0, -25.0, 0.0, 75.0, 0.0, 30.0, 0.0],
    "spooky_arms_up": [15.0, -60.0, 10.0, 40.0, -20.0, 30.0, 0.0],
    "cauldron_stir": [0.0, -35.0, 15.0, 55.0, 30.0, 10.0, 0.0],
}

def get_preset_deg(preset_name: str, arm: str = "left") -> list[float]:
    base = PRESETS_DEG.get(preset_name, [0.0] * 7)
    if arm == "right":
        # OpenArm V1 kinematics: J1, J2, J3 are mirrored between left and right arms
        return [-base[0], -base[1], -base[2], base[3], base[4], base[5], base[6]]
    return list(base)


def get_default_sequence(arm: str = "left") -> list[dict]:
    seq = [
        {"name": "Rest / Stow", "time_s": 0.0, "preset": "stow"},
        {"name": "Zombie Reach", "time_s": 4.0, "preset": "zombie_reach"},
        {"name": "Spooky Arms Up", "time_s": 8.0, "preset": "spooky_arms_up"},
        {"name": "Pumpkin Offer", "time_s": 12.0, "preset": "pumpkin_offer"},
        {"name": "Return to Rest", "time_s": 16.0, "preset": "stow"},
    ]
    return [
        {
            "name": item["name"],
            "arm": arm,
            "time_s": item["time_s"],
            "joints_deg": get_preset_deg(item["preset"], arm),
        }
        for item in seq
    ]


DEFAULT_HALLOWEEN_SEQUENCE = get_default_sequence("left")


class PlannerService:
    """Manages thread-safe planner instances and kinematics for both arms."""

    def __init__(self, default_backend: str = "cbirrt"):
        self.lock = threading.Lock()
        self.active_backend = default_backend
        self._planners = {}
        self.cached_benchmark: dict | None = None

    def run_benchmark(self, arm: str = "left", trials: int = 2, backends: list[str] | None = None) -> dict:
        with self.lock:
            res = run_benchmark(backends=backends, arm=arm, trials=trials, verbose=False)
            self.cached_benchmark = res
            return res

    def set_backend(self, backend: str) -> None:
        with self.lock:
            self.active_backend = backend
            for arm, planner in self._planners.items():
                planner.set_backend(backend)

    def get_planner(self, arm: str) -> OpenArmPlanner:
        if arm not in self._planners:
            with self.lock:
                if arm not in self._planners:
                    print(f"[PlannerService] Initializing {arm} arm planner with {self.active_backend}...")
                    self._planners[arm] = OpenArmPlanner(arm=arm, backend=self.active_backend)
        return self._planners[arm]

    def check_collision(self, arm: str, joints_deg: list[float]) -> dict:
        planner = self.get_planner(arm)
        q = np.radians(joints_deg)
        with self.lock:
            valid = planner.collision_checker.is_valid(q)
            clearance_mm = planner.collision_checker.min_clearance()
            all_c = clearance.clearances(planner.model, planner.data, limit_mm=100.0)
            contacts = []
            for dist, g1, g2 in all_c[:5]:
                if dist < 25.0:
                    contacts.append({"dist_mm": round(float(dist), 1), "geom1": g1, "geom2": g2})
        return {
            "valid": bool(valid),
            "collision": not bool(valid),
            "min_clearance_mm": float(clearance_mm) if np.isfinite(clearance_mm) else 100.0,
            "contacts": contacts,
        }

    def compute_fk(self, arm: str, joints_deg: list[float]) -> dict:
        planner = self.get_planner(arm)
        q = np.radians(joints_deg)
        with self.lock:
            T = planner.robot_model.forward_kinematics(q)
        pos = T[:3, 3].tolist()
        rot = T[:3, :3].tolist()
        return {
            "position": pos,
            "rotation_matrix": rot,
            "ee_frame": f"openarm_{arm}_ee_site",
        }

    def solve_ik(self, arm: str, pos_m: list[float], q_seed_deg: list[float] | None = None) -> dict:
        planner = self.get_planner(arm)
        ik_mod = openarm_left_ik if arm == "left" else openarm_right_ik
        # Orientation matching forward reach in the appropriate arm frame
        ref_preset = get_preset_deg("reach_forward", arm)
        T_target = ik_mod.fk(np.radians(ref_preset))
        T_target[:3, 3] = pos_m

        q_seed = np.radians(q_seed_deg) if q_seed_deg is not None else np.zeros(7)
        with self.lock:
            solutions = planner.solve_ik(T_target, q_seed=q_seed)

        if not solutions:
            return {"success": False, "error": "No kinematic solution found for position."}

        # Find best collision-free solution
        valid_sols = []
        with self.lock:
            for s in solutions:
                if planner.collision_checker.is_valid(s):
                    valid_sols.append(s)

        chosen = valid_sols[0] if valid_sols else solutions[0]
        is_collision = len(valid_sols) == 0

        return {
            "success": True,
            "joints_deg": np.degrees(chosen).tolist(),
            "joints_rad": chosen.tolist(),
            "candidate_count": len(solutions),
            "valid_candidate_count": len(valid_sols),
            "has_collision": is_collision,
        }

    def plan(
        self,
        arm: str,
        q_start_deg: list[float],
        q_goal_deg: list[float],
        v_max: float = 1.0,
        a_max: float = 2.0,
        timeout: float = 5.0,
    ) -> dict:
        planner = self.get_planner(arm)
        planner.v_max = v_max
        planner.a_max = a_max
        q_start = np.radians(q_start_deg)
        q_goal = np.radians(q_goal_deg)

        with self.lock:
            if not planner.collision_checker.is_valid(q_start):
                return {"success": False, "error": "Start configuration is in collision."}
            if not planner.collision_checker.is_valid(q_goal):
                return {"success": False, "error": "Goal configuration is in collision."}

            t0 = time.perf_counter()
            traj = planner.plan_to_joint_goal(q_start, q_goal, timeout=timeout)
            wall_time = time.perf_counter() - t0

        if traj is None:
            return {"success": False, "error": f"Planner failed to find collision-free trajectory within {timeout:.1f}s."}

        # Compute EE positions along trajectory for 3D ribbon rendering
        ee_positions = []
        with self.lock:
            for q in traj.positions:
                T = planner.robot_model.forward_kinematics(q)
                ee_positions.append(T[:3, 3].tolist())

        return {
            "success": True,
            "arm": arm,
            "duration": float(traj.duration),
            "times": traj.times.tolist(),
            "positions_deg": np.degrees(traj.positions).tolist(),
            "positions_rad": traj.positions.tolist(),
            "velocities_rad_s": traj.velocities.tolist(),
            "ee_positions": ee_positions,
            "min_clearance_mm": float(traj.min_clearance_mm),
            "planning_time_ms": float(wall_time * 1000.0),
            "waypoints": traj.metadata.get("waypoints", len(traj.times)),
        }


SERVICE = PlannerService()


def is_own_origin(origin: str, host_header: str, server_port: int) -> bool:
    from urllib.parse import urlsplit
    if not origin:
        return True
    o = urlsplit(origin)
    if not o.scheme or not o.hostname:
        return False
    req = urlsplit(f"//{host_header}")
    if not req.hostname or o.hostname.lower() != req.hostname.lower():
        return False
    try:
        o_port = o.port or {"http": 80, "https": 443}.get(o.scheme)
    except ValueError:
        return False
    return o_port == (req.port or server_port) or o_port in {8080, 8443, 8091, 8092, 8095}


class StudioHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler serving the studio app, assets, and REST APIs."""

    def translate_path(self, path: str) -> str:
        # Strip query strings
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/" or path == "/index.html":
            return os.path.join(STATIC_DIR, "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            return os.path.join(STATIC_DIR, rel)
        if path.startswith("/vendor/"):
            rel = path[len("/vendor/"):]
            return os.path.join(VENDOR_DIR, rel)
        if path.startswith("/assets/arm/"):
            rel = path[len("/assets/arm/"):]
            return os.path.join(ARM_ASSETS_DIR, rel)
        return os.path.join(STATIC_DIR, path.lstrip("/"))

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str = "text/plain", status: int = 200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = self.path.split("?")[0]
        if url == "/api/status":
            return self._send_json({
                "status": "ready",
                "arms": ["left", "right"],
                "active_backend": SERVICE.active_backend,
                "available_backends": get_available_backends(),
                "version": "1.0",
            })
        if url == "/api/hardware/status":
            env = HARDWARE_BRIDGE.detect_environment()
            stat = HARDWARE_BRIDGE.get_status()
            stat["environment"] = env
            return self._send_json(stat)
        query_arm = "left"
        if "?" in self.path:
            for part in self.path.split("?", 1)[1].split("&"):
                if part.startswith("arm="):
                    query_arm = part.split("=")[1]

        if url == "/api/presets":
            out = {k: get_preset_deg(k, query_arm) for k in PRESETS_DEG}
            return self._send_json(out)
        if url == "/api/keyframes":
            if os.path.exists(SAVED_KEYFRAMES_PATH):
                try:
                    with open(SAVED_KEYFRAMES_PATH, "r") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and query_arm in data:
                            return self._send_json(data[query_arm])
                        elif isinstance(data, list) and data and data[0].get("arm") == query_arm:
                            return self._send_json(data)
                except Exception:
                    pass
            return self._send_json(get_default_sequence(query_arm))
        if url == "/api/benchmark":
            if SERVICE.cached_benchmark is not None:
                return self._send_json(SERVICE.cached_benchmark)
            res = SERVICE.run_benchmark(arm="left", trials=2)
            return self._send_json(res)

        if url == "/api/choreo/library":
            items = list_available_choreos()
            return self._send_json({"success": True, "choreos": items})

        if url == "/api/choreo/load":
            query_id = None
            query_arm = None
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    if part.startswith("id="):
                        from urllib.parse import unquote
                        query_id = unquote(part.split("=", 1)[1])
                    elif part.startswith("arm="):
                        query_arm = part.split("=", 1)[1]
            if not query_id:
                return self._send_json({"success": False, "error": "Missing choreo id"}, status=400)
            try:
                kfs = load_choreo_by_path_or_id(query_id, target_arm=query_arm)
                return self._send_json({"success": True, "id": query_id, "keyframes": kfs, "count": len(kfs)})
            except Exception as e:
                return self._send_json({"success": False, "error": str(e)}, status=400)

        if url == "/api/gripper":
            stat = HARDWARE_BRIDGE.get_status()
            sim_grip = stat.get("sim_gripper_state", {})
            arm_val = sim_grip.get(query_arm, 1.0)
            return self._send_json({
                "arm": query_arm,
                "open_fraction": arm_val,
                "action": "open" if arm_val >= 0.5 else "close",
                "position_m": round(arm_val * 0.044, 4),
            })

        return super().do_GET()

    def do_POST(self):
        url = self.path.split("?")[0]
        # Origin guard: protect POST endpoints from cross-origin execution
        origin = self.headers.get("Origin")
        if not origin:
            ref = self.headers.get("Referer", "")
            if ref:
                from urllib.parse import urlsplit
                u = urlsplit(ref)
                origin = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None
        host = self.headers.get("Host", "")
        if origin and not is_own_origin(origin, host, self.server.server_port):
            return self._send_json({"error": "Cross-origin request refused"}, status=403)

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            req = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            return self._send_json({"error": f"Invalid JSON: {e}"}, status=400)

        if url == "/api/collision_check":
            arm = req.get("arm", "left")
            joints = req.get("joints", [0] * 7)
            res = SERVICE.check_collision(arm, joints)
            return self._send_json(res)

        if url == "/api/fk":
            arm = req.get("arm", "left")
            joints = req.get("joints", [0] * 7)
            res = SERVICE.compute_fk(arm, joints)
            return self._send_json(res)

        if url == "/api/ik":
            arm = req.get("arm", "left")
            pos = req.get("position", [0.3, 0.2, 0.1])
            q_seed = req.get("q_seed", None)
            res = SERVICE.solve_ik(arm, pos, q_seed_deg=q_seed)
            return self._send_json(res)

        if url == "/api/plan":
            arm = req.get("arm", "left")
            q_start = req.get("q_start", [0] * 7)
            q_goal = req.get("q_goal", [0] * 7)
            v_max = float(req.get("v_max", 1.0))
            a_max = float(req.get("a_max", 2.0))
            timeout = float(req.get("timeout", 5.0))
            res = SERVICE.plan(arm, q_start, q_goal, v_max=v_max, a_max=a_max, timeout=timeout)
            return self._send_json(res)

        if url == "/api/keyframes":
            # Save keyframes list per arm
            keyframes = req.get("keyframes", [])
            arm = req.get("arm", "left")
            data = {}
            if os.path.exists(SAVED_KEYFRAMES_PATH):
                try:
                    with open(SAVED_KEYFRAMES_PATH, "r") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            data = loaded
                except Exception:
                    pass
            data[arm] = keyframes
            with open(SAVED_KEYFRAMES_PATH, "w") as f:
                json.dump(data, f, indent=2)
            return self._send_json({"success": True, "arm": arm, "count": len(keyframes)})

        if url == "/api/backend":
            new_backend = req.get("backend", "cbirrt")
            try:
                SERVICE.set_backend(new_backend)
                return self._send_json({"success": True, "active_backend": SERVICE.active_backend})
            except Exception as e:
                return self._send_json({"success": False, "error": str(e)}, status=400)

        if url == "/api/export_show":
            keyframes = req.get("keyframes", [])
            show_yaml = generate_show_yaml(keyframes)
            return self._send_text(show_yaml, content_type="text/yaml")

        if url == "/api/benchmark":
            arm = req.get("arm", "left")
            trials = int(req.get("trials", 2))
            backends = req.get("backends", None)
            res = SERVICE.run_benchmark(arm=arm, trials=trials, backends=backends)
            return self._send_json(res)

        if url == "/api/client_log":
            level = req.get("level", "info")
            msg = req.get("message", "")
            stack = req.get("stack", "")
            print(f"[CLIENT {level.upper()}] {msg}", flush=True)
            if stack:
                print(f"  Stack: {stack}", flush=True)
            return self._send_json({"ok": True})

        if url == "/api/hardware/preflight":
            arm = req.get("arm", "left")
            times = req.get("times", [])
            positions = req.get("positions_rad", [])
            velocities = req.get("velocities_rad_s", None)
            min_clearance = float(req.get("min_clearance_mm", 30.0))
            res = validate_trajectory_safety(arm, times, positions, velocities, min_clearance)
            return self._send_json(res.to_dict())

        if url == "/api/hardware/execute":
            arm = req.get("arm", "left")
            trajectory = req.get("trajectory", {})
            mode = req.get("mode", "hitl")
            if mode != "hitl":
                return self._send_json({
                    "success": False,
                    "error": (
                        "Hardware execution is disabled on this branch. "
                        "All physical arm motion on the robot goes exclusively through "
                        "the show runner (software/choreo/runner.py)."
                    ),
                }, status=403)
            res = HARDWARE_BRIDGE.execute_trajectory(arm, trajectory, mode="hitl")
            status_code = 200 if res.get("success") else 400
            return self._send_json(res, status=status_code)

        if url == "/api/hardware/stop":
            res = HARDWARE_BRIDGE.stop()
            return self._send_json(res)

        if url == "/api/choreo/import":
            content = req.get("content", "")
            choreo_id = req.get("id", "")
            target_arm = req.get("arm", None)
            try:
                if choreo_id:
                    kfs = load_choreo_by_path_or_id(choreo_id, target_arm=target_arm)
                elif content:
                    kfs = parse_choreo_content(content, target_arm=target_arm)
                else:
                    return self._send_json({"success": False, "error": "No content or choreo id provided"}, status=400)
                return self._send_json({"success": True, "keyframes": kfs, "count": len(kfs)})
            except Exception as e:
                return self._send_json({"success": False, "error": str(e)}, status=400)

        if url == "/api/choreo/save":
            name = req.get("name", "custom_choreo")
            format_type = req.get("format", "yaml")
            keyframes = req.get("keyframes", [])
            try:
                res = save_user_choreo(name, keyframes, format_type=format_type)
                return self._send_json(res)
            except Exception as e:
                return self._send_json({"success": False, "error": str(e)}, status=400)

        if url == "/api/choreo/delete":
            name = req.get("name", "")
            deleted = delete_user_choreo(name)
            return self._send_json({"success": deleted})

        if url == "/api/gripper":
            arm = req.get("arm", "left")
            action = req.get("action", "open")
            res = HARDWARE_BRIDGE.command_gripper(arm, action, mode="hitl")
            return self._send_json(res)

        self.send_error(404, "Endpoint not found")


def generate_show_yaml(keyframes: list[dict]) -> str:
    """Generate YAML in choreo show format for OpenDubs."""
    lines = [
        "# OpenDubs Halloween Arm Show Sequence",
        "# Generated by OpenArm Motion Planner Studio",
        "id: halloween_arm_sequence",
        "name: Halloween Arm Routine",
        "slots: [ARM_L, ARM_R, GRIP_L, GRIP_R, LIGHT, SOUND, FACE]",
        "requires:",
        "  arms: active",
        "cues:",
    ]
    for kf in keyframes:
        t = float(kf.get("time_s", 0.0))
        arm = kf.get("arm", "left").upper()
        slot = f"ARM_{arm[0]}"
        joints = kf.get("joints_deg", [0] * 7)
        # 1-indexed joint map for choreo format
        j_map = {i + 1: round(float(v), 1) for i, v in enumerate(joints)}
        comment = kf.get("name", "").strip()
        if comment:
            lines.append(f"  # {comment}")
        j_str = ", ".join(f"{k}: {v}" for k, v in j_map.items())
        lines.append(f"  - {{ t: {t:.1f}, slot: {slot}, joints: {{ {j_str} }} }}")
        grip = kf.get("gripper")
        if grip in ("open", "close"):
            lines.append(f"  - {{ t: {t:.1f}, slot: GRIP_{arm[0]}, do: {grip} }}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="OpenArm Motion Planner Studio Web Server")
    parser.add_argument("--port", type=int, default=8095, help="Port to listen on (default: 8095)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--backend", default="cbirrt", help="Default motion planning backend (e.g. 'cbirrt', 'curobo') (default: cbirrt)")
    args = parser.parse_args()

    SERVICE.set_backend(args.backend)

    # Pre-warm left planner in background so UI starts instantly
    threading.Thread(target=lambda: SERVICE.get_planner("left"), daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), StudioHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"========================================================")
    print(f" OpenArm Motion Planner Studio")
    print(f" Server running at: {url}")
    print(f" Static root: {STATIC_DIR}")
    print(f" Press Ctrl+C to stop.")
    print(f"========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    main()
