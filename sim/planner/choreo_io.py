#!/usr/bin/env python3
"""Choreo Import, Export, and Persistence Manager for OpenArm Planner Studio."""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
SHOWS_DIR = os.path.join(REPO, "software", "choreo", "shows")
SAVED_CHOREOS_DIR = os.path.join(HERE, "saved_choreos")

# Ensure directory exists
os.makedirs(SAVED_CHOREOS_DIR, exist_ok=True)


def get_default_stow(arm: str = "left") -> List[float]:
    base = [0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0]
    if arm == "right":
        return [-base[0], -base[1], -base[2], base[3], base[4], base[5], base[6]]
    return base


def list_available_choreos() -> List[Dict[str, Any]]:
    """List all available choreos from both user saved directory and built-in show library."""
    items = []

    # 1. User saved choreos in sim/planner/saved_choreos/
    if os.path.isdir(SAVED_CHOREOS_DIR):
        for fpath in sorted(glob.glob(os.path.join(SAVED_CHOREOS_DIR, "*.*"))):
            ext = os.path.splitext(fpath)[1].lower()
            if ext in (".yaml", ".yml", ".json"):
                base = os.path.basename(fpath)
                mtime = os.path.getmtime(fpath)
                items.append({
                    "id": f"user:{base}",
                    "name": base,
                    "filename": base,
                    "type": "user",
                    "path": fpath,
                    "mtime": mtime,
                })

    # 2. Built-in shows from software/choreo/shows/
    if os.path.isdir(SHOWS_DIR):
        for fpath in sorted(glob.glob(os.path.join(SHOWS_DIR, "*.yaml"))):
            base = os.path.basename(fpath)
            show_name = base
            if yaml:
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        doc = yaml.safe_load(fh)
                        if isinstance(doc, dict) and "name" in doc:
                            show_name = f"{doc['name']} ({base})"
                except Exception:
                    pass
            items.append({
                "id": f"builtin:{base}",
                "name": show_name,
                "filename": base,
                "type": "builtin",
                "path": fpath,
                "mtime": os.path.getmtime(fpath),
            })

    return items


def parse_choreo_content(content: str, target_arm: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse a choreo YAML or JSON string into a list of Studio keyframe objects."""
    content = content.strip()
    if not content:
        raise ValueError("Empty choreo content.")

    # Try parsing as JSON first
    if content.startswith("[") or content.startswith("{"):
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return _normalize_keyframes_list(data, target_arm)
            elif isinstance(data, dict):
                if "keyframes" in data and isinstance(data["keyframes"], list):
                    return _normalize_keyframes_list(data["keyframes"], target_arm)
                if target_arm and target_arm in data and isinstance(data[target_arm], list):
                    return _normalize_keyframes_list(data[target_arm], target_arm)
        except Exception:
            pass

    # Parse as YAML
    if not yaml:
        raise RuntimeError("PyYAML is not installed; cannot parse YAML choreo.")

    doc = yaml.safe_load(content)
    if not isinstance(doc, dict):
        if isinstance(doc, list):
            return _normalize_keyframes_list(doc, target_arm)
        raise ValueError("Invalid choreo YAML: root must be a mapping or list.")

    cues = doc.get("cues", [])
    if not isinstance(cues, list):
        raise ValueError("Invalid choreo: 'cues' block is missing or not a list.")

    # Tracking joint states per arm
    arm_state = {
        "left": get_default_stow("left"),
        "right": get_default_stow("right"),
    }

    # Extract all arm cues
    keyframes = []
    seen_times = set()

    for idx, c in enumerate(cues):
        if not isinstance(c, dict):
            continue
        slot = str(c.get("slot", "")).upper()
        if slot not in ("ARM_L", "ARM_R"):
            continue

        cue_arm = "left" if slot == "ARM_L" else "right"
        if target_arm and cue_arm != target_arm.lower():
            # If the user requested only a specific arm, we can optionally skip or mirror
            pass

        t = float(c.get("t", 0.0))
        pose_name = c.get("name", c.get("pose", f"Cue {idx + 1} ({slot})"))
        
        # Build 7-DOF joints
        current_joints = list(arm_state[cue_arm])

        if c.get("pose") in ("rest", "stow"):
            current_joints = get_default_stow(cue_arm)
            pose_name = f"Stow / Rest ({cue_arm.upper()})"
        elif "joints" in c and isinstance(c["joints"], dict):
            j_map = c["joints"]
            # Check if angles are outward sense:
            # In choreo format, Left arm J1, J2, J3 are written in outward sense (positive)
            # and negated by cue.py for the controller.
            is_outward = doc.get("id") != "halloween_arm_sequence"
            for k, val in j_map.items():
                try:
                    j_idx = int(k) - 1
                    if 0 <= j_idx < 7:
                        deg_val = float(val)
                        if is_outward and cue_arm == "left" and j_idx in (0, 1, 2):
                            deg_val = -deg_val
                        current_joints[j_idx] = round(deg_val, 1)
                except (ValueError, TypeError):
                    continue

        arm_state[cue_arm] = list(current_joints)

        # Unique keyframe entry
        key = (round(t, 2), cue_arm)
        if key in seen_times:
            # If multiple cues at same time for same arm, update last
            for kf in keyframes:
                if round(kf["time_s"], 2) == round(t, 2) and kf["arm"] == cue_arm:
                    kf["joints_deg"] = list(current_joints)
                    break
        else:
            seen_times.add(key)
            keyframes.append({
                "name": str(pose_name).strip(),
                "arm": cue_arm,
                "time_s": round(t, 2),
                "joints_deg": list(current_joints),
            })

    # Sort chronologically
    keyframes.sort(key=lambda x: x["time_s"])
    if target_arm:
        target_arm = target_arm.lower()
        matching = [kf for kf in keyframes if kf["arm"] == target_arm]
        if matching:
            return matching
        # If no cues for target_arm, mirror from other arm
        other = [kf for kf in keyframes if kf["arm"] != target_arm]
        if other:
            mirrored = []
            for kf in other:
                j = kf["joints_deg"]
                mirrored.append({
                    "name": f"{kf['name']} (Mirrored)",
                    "arm": target_arm,
                    "time_s": kf["time_s"],
                    "joints_deg": [-j[0], -j[1], -j[2], j[3], j[4], j[5], j[6]],
                })
            return mirrored
    return keyframes


def _normalize_keyframes_list(items: List[Any], target_arm: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        arm = str(it.get("arm", target_arm or "left")).lower()
        if arm not in ("left", "right"):
            arm = "left"
        t = float(it.get("time_s", it.get("t", float(i * 4.0))))
        name = str(it.get("name", f"Pose {i + 1}"))
        joints = it.get("joints_deg", it.get("joints", [0.0] * 7))
        if isinstance(joints, dict):
            j_list = [0.0] * 7
            for k, v in joints.items():
                idx = int(k) - 1
                if 0 <= idx < 7:
                    j_list[idx] = float(v)
            joints = j_list
        elif isinstance(joints, list):
            joints = [round(float(v), 1) for v in joints[:7]]
            while len(joints) < 7:
                joints.append(0.0)

        out.append({
            "name": name,
            "arm": arm,
            "time_s": round(t, 2),
            "joints_deg": joints,
        })
    out.sort(key=lambda x: x["time_s"])
    if target_arm:
        target_arm = target_arm.lower()
        matching = [kf for kf in out if kf["arm"] == target_arm]
        if matching:
            return matching
        other = [kf for kf in out if kf["arm"] != target_arm]
        if other:
            mirrored = []
            for kf in other:
                j = kf["joints_deg"]
                mirrored.append({
                    "name": f"{kf['name']} (Mirrored)",
                    "arm": target_arm,
                    "time_s": kf["time_s"],
                    "joints_deg": [-j[0], -j[1], -j[2], j[3], j[4], j[5], j[6]],
                })
            return mirrored
    return out


def save_user_choreo(name: str, keyframes: List[Dict[str, Any]], format_type: str = "yaml") -> Dict[str, Any]:
    """Save keyframes to sim/planner/saved_choreos/ under the requested name."""
    clean_name = re.sub(r"[^\w\-.]", "_", name.strip())
    if not clean_name:
        clean_name = "custom_choreo"

    if format_type.lower() in ("json", ".json"):
        if not clean_name.endswith(".json"):
            clean_name += ".json"
        dest = os.path.join(SAVED_CHOREOS_DIR, clean_name)
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(keyframes, fh, indent=2)
    else:
        if not (clean_name.endswith(".yaml") or clean_name.endswith(".yml")):
            clean_name += ".yaml"
        dest = os.path.join(SAVED_CHOREOS_DIR, clean_name)
        
        # Build YAML show format
        lines = [
            f"# OpenDubs Choreo: {clean_name}",
            f"id: {os.path.splitext(clean_name)[0]}",
            f"name: {os.path.splitext(clean_name)[0].replace('_', ' ').title()}",
            "slots: [ARM_L, ARM_R, LIGHT, SOUND, FACE]",
            "requires:",
            "  arms: active",
            "cues:",
        ]
        for kf in keyframes:
            t = float(kf.get("time_s", 0.0))
            arm = kf.get("arm", "left").upper()
            slot = f"ARM_{arm[0]}"
            joints = kf.get("joints_deg", [0.0] * 7)
            j_map = {i + 1: round(float(v), 1) for i, v in enumerate(joints)}
            comment = kf.get("name", "").strip()
            if comment:
                lines.append(f"  # {comment}")
            j_str = ", ".join(f"{k}: {v}" for k, v in j_map.items())
            lines.append(f"  - {{ t: {t:.1f}, slot: {slot}, joints: {{ {j_str} }} }}")
        
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    return {
        "success": True,
        "filename": clean_name,
        "path": dest,
        "count": len(keyframes),
    }


def delete_user_choreo(name: str) -> bool:
    """Delete a user choreo from sim/planner/saved_choreos/."""
    base = os.path.basename(name)
    target = os.path.join(SAVED_CHOREOS_DIR, base)
    if os.path.isfile(target):
        os.remove(target)
        return True
    return False


def load_choreo_by_path_or_id(choreo_id: str, target_arm: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load and parse choreo by ID ('user:xxx.yaml' or 'builtin:xxx.yaml' or raw filepath)."""
    if choreo_id.startswith("user:"):
        fpath = os.path.join(SAVED_CHOREOS_DIR, choreo_id[len("user:"):])
    elif choreo_id.startswith("builtin:"):
        fpath = os.path.join(SHOWS_DIR, choreo_id[len("builtin:"):])
    elif os.path.isfile(choreo_id):
        fpath = choreo_id
    else:
        # Fallback search
        candidate = os.path.join(SAVED_CHOREOS_DIR, choreo_id)
        if os.path.isfile(candidate):
            fpath = candidate
        else:
            candidate_show = os.path.join(SHOWS_DIR, choreo_id)
            if os.path.isfile(candidate_show):
                fpath = candidate_show
            else:
                raise FileNotFoundError(f"Choreo not found: {choreo_id}")

    with open(fpath, "r", encoding="utf-8") as fh:
        content = fh.read()

    return parse_choreo_content(content, target_arm=target_arm)
