#!/usr/bin/env python3
"""IK targets for shows: an arm cue may say WHERE the hand goes, not seven joint angles (plan item 1;
D1 approved by the user 2026-10-10).

    { bar: "9.1", slot: ARM_R, reach: head_screen }            # the hand to a point
    { bar: "9.1", slot: ARM_R, reach: [0.55, -0.20, 1.10] }    # metres, in the chassis frame
    { bar: "9.3", slot: ARM_L, point_at: right_hand }          # aim the hand at a target

    python3 ik.py compile shows/my-show.yaml     # solve every target; write `solved:` into the show
    python3 ik.py check shows/my-show.yaml       # re-solve and confirm what is written still holds

HOW IT REACHES THE ROBOT. Targets are solved HERE, at authoring time, against the twin's own MuJoCo
model, and the joint angles are written into the show as `solved:` (with `solved_for:`, the target
they were solved for). The validator then checks those angles exactly as it checks hand-written
ones — limits, the measured speed caps, the trip, the head clearance — and the runner on the Thor
plays them, with no IK and no MuJoCo there. So the robot runs precisely what the twin previewed, and
an edited target with stale angles is a validator problem until it is compiled again.

THE TOOL FRAME. One, named by the Arms lane (its 0.20): MuJoCo's `ee_site`, in the gripper; its +z
axis points out through the fingers. `reach` puts that site on the target; `point_at` aims its +z
axis at the target and leaves the hand where the previous pose had it, as far as it can. The URDFs'
`hand_tcp` frames differ from it (the flange, or 186 mm out) — never use those here.

SAFE BY CONSTRUCTION: a reach to a named PART of the robot (head_screen, head, lift_platform) stops
STANDOFF_M in front of it (`standoff:` per cue, at least 0.05 m), never on it; and every solved move —
the pose and the way into it — is checked against the twin's collision model (sim/clearance.py): an
arm that would come within CLEAR_MIN_MM of the robot's own body is refused, with the parts named.

TARGETS: a point [x, y, z] in metres in the CHASSIS frame (x forward, y left, z up; robot-relative —
there is no localisation yet), or a name: `head_screen` (the centre of the fitted head's screen, at
the head's pan/tilt at that moment), `head`, `lift_platform`, `left_hand` / `right_hand` (the other
arm's tool site, where the show has it at that moment). The solver starts from where the arm is at
that moment in the show and stays near it, so the move is the smallest that reaches the target.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "sim")]

import cue  # noqa: E402

TOOL_SITE = "oa_openarm_{side}_ee_site"
JOINT = "oa_openarm_{side}_joint{j}"
SIDE = {"ARM_L": "left", "ARM_R": "right"}
NAMED = ("head_screen", "head", "lift_platform", "left_hand", "right_hand")
REACH_TOL_M = 0.01                 # a reach that ends further than this from its target is refused
STANDOFF_M = 0.12                  # a reach to a named PART of the robot stops this far in front of it
CLEAR_MIN_MM = 30.0                # the arm may not come closer than this to the robot's own body
PATH_STEPS = 12                    # the move into a target is checked at this many poses on the way
POINT_TOL_DEG = 2.0                # a point_at whose axis misses by more than this is refused
POSTURE = 0.05                     # how strongly the solver stays near the starting pose


target_key = cue.ik_target_key


def model_sig(head: str | None = None) -> str:
    """Which model the angles were solved on: our MJCF, the arms' model and the fitted head."""
    import scene
    h = hashlib.sha256()
    for p in (os.path.join(scene.OURS, "opendubs.xml"),):
        with open(p, "rb") as fh:
            h.update(fh.read())
    h.update((head or scene.fitted_head()).encode())
    return h.hexdigest()[:8]


class Kin:
    """The twin's model, posed kinematically, with damped-least-squares IK on one arm's 7 joints."""

    def __init__(self, limits: dict, head: str | None = None):
        import mujoco
        import numpy as np
        import scene
        self.mj, self.np, self.scene = mujoco, np, scene
        self.head = head or scene.fitted_head()
        self.m = scene.build(None, head=self.head)
        self.d = mujoco.MjData(self.m)
        self.sig = model_sig(self.head)
        self.limits = limits

    def _jid(self, side, j):
        return self.mj.mj_name2id(self.m, self.mj.mjtObj.mjOBJ_JOINT, JOINT.format(side=side, j=j))

    def pose(self, smp: dict):
        """Place the whole robot as a sim_preview sample describes it (outward degrees, head, lift)."""
        import clearance
        import sim_preview
        clearance.set_pose(self.m, self.d, sim_preview.joints_arg(smp), smp["lift_mm"],
                           smp["head_pan"] - sim_preview.HEAD_CENTRE_DEG, smp["head_tilt"] - sim_preview.HEAD_CENTRE_DEG)

    def point(self, target, side: str, standoff=None):
        """A target, as a world point, in the pose last set. A named PART of the robot is moved out
        `standoff` metres along its forward axis, so a reach stops in front of it."""
        mj, np = self.mj, self.np
        if isinstance(target, str) and target in ("head_screen", "head", "lift_platform"):
            body = "chassis" if target == "lift_platform" else "head_tilt"
            b = mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_BODY, body)
            fwd = self.d.xmat[b].reshape(3, 3)[:, 0]
            return self._named(target, side) + fwd * (STANDOFF_M if standoff is None else float(standoff))
        return self._named(target, side)

    def _named(self, target, side):
        mj, np = self.mj, self.np
        if isinstance(target, str):
            if target in ("left_hand", "right_hand"):
                other = target.split("_")[0]
                if other == side:
                    raise ValueError(f"an arm cannot target its own hand ({target})")
                return self.d.site_xpos[mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_SITE, TOOL_SITE.format(side=other))].copy()
            if target == "head_screen":
                b = mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_BODY, "head_tilt")
                local = np.array([self.scene.FACE_X, self.scene.FACE_YC, self.scene.FACE_ZC])
                return self.d.xpos[b] + self.d.xmat[b].reshape(3, 3) @ local
            if target in ("head", "lift_platform"):
                body = "head_tilt" if target == "head" else "lift_platform"
                return self.d.xpos[mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_BODY, body)].copy()
            raise ValueError(f"unknown target {target!r} (named: {', '.join(NAMED)}; or [x, y, z] metres)")
        b = mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_BODY, "chassis")
        return self.d.xpos[b] + self.d.xmat[b].reshape(3, 3) @ np.array([float(x) for x in target])

    def clearance_along(self, side, q_from, q_to):
        """The closest self-contact (mm, geom a, geom b) over the move from q_from to q_to (model
        radians), the rest of the robot as last set. The smoothed path between two keyframes stays
        between them joint by joint, so a straight line in joint space samples it fairly."""
        import clearance
        mj, np = self.mj, self.np
        qadr = [self.m.jnt_qposadr[self._jid(side, j)] for j in range(1, 8)]
        keep = self.d.qpos.copy()
        worst = (float("inf"), None, None)
        for k in range(PATH_STEPS + 1):
            q = q_from + (q_to - q_from) * k / PATH_STEPS
            for i, a in enumerate(qadr):
                self.d.qpos[a] = q[i]
            mj.mj_forward(self.m, self.d)
            c = clearance.clearances(self.m, self.d, limit_mm=200.0)
            if c and c[0][0] < worst[0]:
                worst = c[0]
        self.d.qpos[:] = keep
        mj.mj_forward(self.m, self.d)
        return worst

    def _bounds(self, side):
        """Each joint's usable range in the MODEL's radians: the arm software's limits less the
        validator's margin, so a solution the validator would refuse is never offered."""
        out = []
        for j in range(1, 8):
            lo, hi = self.limits[f"openarm_{side}_joint{j}"]
            mlo, mhi = math.radians(math.degrees(lo) + cue.MARGIN_DEG), math.radians(math.degrees(hi) - cue.MARGIN_DEG)
            out.append((mlo, mhi))
        return out

    def _error(self, mode, site, goal):
        """(task error vector, its jacobian rows selector, scalar error) for the pose last set."""
        np = self.np
        if mode == "reach":
            e = goal - self.d.site_xpos[site]
            return e, "p", float(np.linalg.norm(e))
        z = self.d.site_xmat[site].reshape(3, 3)[:, 2]
        v = goal - self.d.site_xpos[site]
        v = v / (np.linalg.norm(v) or 1.0)
        return np.cross(z, v), "r", math.degrees(math.acos(max(-1.0, min(1.0, float(z @ v)))))

    def _descend(self, mode, site, goal, qadr, dadr, bounds, q_start, q_rest, iters=1500):
        """Damped least squares on the task, with the pull toward `q_rest` projected into the task's
        null space, so it shapes the posture without fighting the target."""
        mj, np = self.mj, self.np
        for k, a in enumerate(qadr):
            self.d.qpos[a] = q_start[k]
        jp, jr = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))
        err = None
        for _ in range(iters):
            mj.mj_forward(self.m, self.d)
            mj.mj_jacSite(self.m, self.d, jp, jr, site)
            e, which, err = self._error(mode, site, goal)
            if (mode == "reach" and err < 2e-4) or (mode == "point_at" and err < 0.1):
                break
            J = (jp if which == "p" else jr)[:, dadr]
            Jpinv = J.T @ np.linalg.inv(J @ J.T + 1e-4 * np.eye(3))
            q = np.array([self.d.qpos[a] for a in qadr])
            dq = Jpinv @ e + (np.eye(7) - Jpinv @ J) @ (0.05 * (q_rest - q))
            q = q + np.clip(dq, -0.08, 0.08)
            for k, a in enumerate(qadr):
                self.d.qpos[a] = min(max(q[k], bounds[k][0]), bounds[k][1])
        mj.mj_forward(self.m, self.d)
        _, _, err = self._error(mode, site, goal)
        return np.array([self.d.qpos[a] for a in qadr]), err

    def solve(self, side: str, mode: str, goal):
        """Move one arm's 7 joints so its tool site reaches `goal` (reach) or its +z axis points at it
        (point_at), starting from the pose last set and staying as near it as the target allows.
        Returns (outward degrees {1..7}, error) — metres for reach, degrees for point_at."""
        mj, np = self.mj, self.np
        site = mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_SITE, TOOL_SITE.format(side=side))
        jids = [self._jid(side, j) for j in range(1, 8)]
        qadr = [self.m.jnt_qposadr[i] for i in jids]
        dadr = [self.m.jnt_dofadr[i] for i in jids]
        # half a degree inside the validator's usable range, so rounding never lands on its edge
        bounds = [(lo + math.radians(0.5), hi - math.radians(0.5)) for lo, hi in self._bounds(side)]
        q0 = np.array([self.d.qpos[a] for a in qadr])
        tol = REACH_TOL_M if mode == "reach" else POINT_TOL_DEG
        # Seeds: from where the arm is, and from the middle of each joint's range nudged toward it —
        # the converged answer nearest the starting pose wins (the smallest move).
        mid = np.array([(lo + hi) / 2 for lo, hi in bounds])
        best = None
        for seed in (q0, 0.5 * (q0 + mid), mid):
            q, err = self._descend(mode, site, goal, qadr, dadr, bounds, seed, q0)
            score = (err > tol, float(np.abs(q - q0).sum()) if err <= tol else err)
            if best is None or score < best[0]:
                best = (score, q, err)
        q, err = best[1], best[2]
        for k, a in enumerate(qadr):
            self.d.qpos[a] = q[k]
        mj.mj_forward(self.m, self.d)
        sign = {j: (-1 if side == "left" and j in cue.MIRRORED_JOINTS else 1) for j in range(1, 8)}
        out = {j: round(math.degrees(q[j - 1]) * sign[j], 2) for j in range(1, 8)}
        return out, err


def _ik_cues(doc: dict) -> list:
    return [(i, c) for i, c in enumerate(doc.get("cues") or [])
            if isinstance(c, dict) and c.get("slot") in SIDE and target_key(c)]


def compile_show(path: str, limits_path: str, write: bool = True, verbose: bool = True) -> list:
    """Solve every IK target in the show, in time order, and write `solved` / `solved_for` into it
    (a minimal text edit, so comments and layout survive). Returns the problems (unreachable targets)."""
    import yaml
    import sim_preview
    sys.path.insert(0, os.path.join(HERE, "composer"))
    import yamledit
    limits = cue.load_limits(limits_path)
    kin = Kin(limits)
    text = open(path, encoding="utf-8").read()
    doc = yaml.safe_load(text)
    problems = []
    todo = _ik_cues(doc)
    # each target's time, from the validator's own resolution of bar/beat/t
    times = {}
    events, _, _ = cue.Show(doc, path)._resolve(lambda p: None)
    for e in events:
        src = e.get("src") or {}
        if src.get("clip") is None and src.get("index") is not None:
            times[src["index"]] = e["t"]
    for i, c in sorted(todo, key=lambda ic: times.get(ic[0], 0.0)):
        # the robot as the show has it at that moment, with every earlier target already solved:
        # this cue left out, so the arm is where it would be without it
        trial = dict(doc, cues=[x for k, x in enumerate(doc["cues"]) if k != i])
        tl = cue.Show(trial, path).export(limits, limits_path)
        t = times.get(i, 0.0)
        smp = sim_preview.sample(tl, t)
        kin.pose(smp)
        side = SIDE[c["slot"]]
        mode = "reach" if "reach" in c else "point_at"
        try:
            # a cue's own standoff is at least 5 cm: a reach to the robot never ends ON it
            so = c.get("standoff")
            goal = kin.point(c[mode], side, None if so is None else max(0.05, float(so)))
        except ValueError as e:
            problems.append(f"cue {i} ({c['slot']} at {t:.2f}s): {e}")
            continue
        import numpy as np
        qadr = [kin.m.jnt_qposadr[kin._jid(side, j)] for j in range(1, 8)]
        q_from = np.array([kin.d.qpos[a] for a in qadr])
        sol, err = kin.solve(side, mode, goal)
        q_to = np.array([kin.d.qpos[a] for a in qadr])
        tol, unit = (REACH_TOL_M, "m") if mode == "reach" else (POINT_TOL_DEG, "deg")
        if err is None or err > tol:
            # No angles are written for a target the arm cannot meet: the validator then refuses the
            # cue as uncompiled, rather than passing angles that miss it.
            problems.append(f"cue {i} ({c['slot']} {target_key(c)} at {t:.2f}s): out of reach — "
                            f"the closest the arm gets is {err:.3f} {unit} off (tolerance {tol} {unit})")
            if verbose:
                print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> OUT OF REACH ({err:.4f} {unit} off)")
            continue
        mm, ga, gb = kin.clearance_along(side, q_from, q_to)
        if mm < CLEAR_MIN_MM:
            problems.append(f"cue {i} ({c['slot']} {target_key(c)} at {t:.2f}s): the arm would come within "
                            f"{mm:.0f} mm of the robot ({ga} / {gb}) on the way or at the target "
                            f"(at least {CLEAR_MIN_MM:.0f} mm) — choose another target or a larger standoff")
            if verbose:
                print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> TOO CLOSE ({mm:.0f} mm: {ga} / {gb})")
            continue
        new = dict(c, solved={j: v for j, v in sol.items()}, solved_for=f"{target_key(c)}@{kin.sig}")
        doc["cues"][i] = new
        text = yamledit.apply(text, [{"op": "set", "index": i, "cue": new}])
        if verbose:
            print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> {sol}   ({err:.4f} {unit} off)")
    if write:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("compile", "check"))
    ap.add_argument("show")
    ap.add_argument("--limits", default=os.environ.get("OPENDUBS_ARM_LIMITS", cue.DEFAULT_LIMITS))
    a = ap.parse_args()
    if not os.path.isfile(a.limits):
        a.limits = os.path.join(HERE, "tests", "joint_limits_fixture.yaml")
        print(f"ik: limits not found; using the TEST FIXTURE ({a.limits})")
    problems = compile_show(a.show, a.limits, write=a.cmd == "compile")
    for p in problems:
        print("  PROBLEM:", p)
    print("ok" if not problems else f"{len(problems)} target(s) out of reach")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
