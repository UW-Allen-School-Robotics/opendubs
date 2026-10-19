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
from http.server import HTTPServer, SimpleHTTPRequestHandler
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

DEFAULT_HALLOWEEN_SEQUENCE = [
    {"name": "Rest / Stow", "arm": "left", "time_s": 0.0, "joints_deg": PRESETS_DEG["stow"]},
    {"name": "Zombie Reach", "arm": "left", "time_s": 4.0, "joints_deg": PRESETS_DEG["zombie_reach"]},
    {"name": "Spooky Arms Up", "arm": "left", "time_s": 8.0, "joints_deg": PRESETS_DEG["spooky_arms_up"]},
    {"name": "Pumpkin Offer", "arm": "left", "time_s": 12.0, "joints_deg": PRESETS_DEG["pumpkin_offer"]},
    {"name": "Return to Rest", "arm": "left", "time_s": 16.0, "joints_deg": PRESETS_DEG["stow"]},
]


class PlannerService:
    """Manages thread-safe planner instances and kinematics for both arms."""

    def __init__(self, default_backend: str = "cbirrt"):
        self.lock = threading.Lock()
        self.active_backend = default_backend
        self._planners = {}

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
        # Orientation matching forward reach
        T_target = ik_mod.fk(np.radians(PRESETS_DEG["reach_forward"]))
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
        if url == "/api/presets":
            return self._send_json(PRESETS_DEG)
        if url == "/api/keyframes":
            if os.path.exists(SAVED_KEYFRAMES_PATH):
                try:
                    with open(SAVED_KEYFRAMES_PATH, "r") as f:
                        return self._send_json(json.load(f))
                except Exception:
                    pass
            return self._send_json(DEFAULT_HALLOWEEN_SEQUENCE)

        return super().do_GET()

    def do_POST(self):
        url = self.path.split("?")[0]
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
            # Save keyframes list
            keyframes = req.get("keyframes", [])
            with open(SAVED_KEYFRAMES_PATH, "w") as f:
                json.dump(keyframes, f, indent=2)
            return self._send_json({"success": True, "count": len(keyframes)})

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

        self.send_error(404, "Endpoint not found")


def generate_show_yaml(keyframes: list[dict]) -> str:
    """Generate YAML in choreo show format for OpenDubs."""
    lines = [
        "# OpenDubs Halloween Arm Show Sequence",
        "# Generated by OpenArm Motion Planner Studio",
        "id: halloween_arm_sequence",
        "name: Halloween Arm Routine",
        "slots: [ARM_L, ARM_R, LIGHT, SOUND, FACE]",
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
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="OpenArm Motion Planner Studio Web Server")
    parser.add_argument("--port", type=int, default=8092, help="Port to listen on (default: 8092)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--backend", default="cbirrt", help="Default motion planning backend (e.g. 'cbirrt', 'curobo') (default: cbirrt)")
    args = parser.parse_args()

    SERVICE.set_backend(args.backend)

    # Pre-warm left planner in background so UI starts instantly
    threading.Thread(target=lambda: SERVICE.get_planner("left"), daemon=True).start()

    server = HTTPServer((args.host, args.port), StudioHandler)
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
