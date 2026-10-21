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
import tcp  # noqa: E402  the arm controllers' own hand point and box

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

    @staticmethod
    def _envelopes(bounds):
        """The validator's shoulder rules, as two sets of joint bounds the solver can keep to: the
        shoulder (J2) never within SHOULDER_MARGIN_DEG of the head, and past ~135 deg only with the
        elbow (J4) folded (the 1.2 m workspace box). Either the shoulder stays under 135 deg with the
        elbow free, or the elbow is folded and the shoulder may go up to the head margin. J2's
        bound is symmetric, so its mirroring on the left does not matter; J4 is not mirrored."""
        pad = 0.5
        head = math.radians(cue.SHOULDER_HEAD_CONTACT_DEG - cue.SHOULDER_MARGIN_DEG - pad)
        ext = math.radians(cue.SHOULDER_EXTENDED_MAX_DEG - pad)
        fold = math.radians(cue.ELBOW_FOLD_MIN_DEG + pad)
        out = []
        for j2, j4_lo in ((ext, None), (head, fold)):
            b = list(bounds)
            b[1] = (max(b[1][0], -j2), min(b[1][1], j2))
            if j4_lo is not None:
                b[3] = (max(b[3][0], j4_lo), b[3][1])
            if all(lo < hi for lo, hi in b):
                out.append(b)
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

    def _elbow_point(self, side):
        """The arm's elbow, as pose_ref.py means it: where joint 4 (the elbow joint) sits."""
        jid = self._jid(side, 4)
        return jid, self.m.jnt_bodyid[jid]

    def _descend(self, mode, site, goal, qadr, dadr, bounds, q_start, q_rest, iters=1500, elbow=None):
        """Damped least squares on the task. Without an elbow goal, a pull toward `q_rest` is
        projected into the task's null space, so it shapes the posture without fighting the target.
        With one — (joint id, body id, world point) — the ELBOW is a second task in that null space
        instead: the hand stays primary, and the elbow gets as close as the arm's spare freedom (its
        swivel) allows. Returns (q, hand error, elbow error in metres or None)."""
        mj, np = self.mj, self.np
        for k, a in enumerate(qadr):
            self.d.qpos[a] = q_start[k]
        jp, jr = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))
        ep, er = np.zeros((3, self.m.nv)), np.zeros((3, self.m.nv))
        err = None
        for _ in range(iters):
            mj.mj_forward(self.m, self.d)
            mj.mj_jacSite(self.m, self.d, jp, jr, site)
            e, which, err = self._error(mode, site, goal)
            J = (jp if which == "p" else jr)[:, dadr]
            Jpinv = J.T @ np.linalg.inv(J @ J.T + 1e-4 * np.eye(3))
            N = np.eye(7) - Jpinv @ J
            q = np.array([self.d.qpos[a] for a in qadr])
            dq = Jpinv @ e
            if elbow is None:
                if (mode == "reach" and err < 2e-4) or (mode == "point_at" and err < 0.1):
                    break
                dq = dq + N @ (0.05 * (q_rest - q))
            else:
                jid, body, egoal = elbow
                pe = self.d.xanchor[jid].copy()
                mj.mj_jac(self.m, self.d, ep, er, pe, body)
                J2 = ep[:, dadr] @ N
                e2 = egoal - pe
                dq = dq + N @ (J2.T @ np.linalg.solve(J2 @ J2.T + 1e-3 * np.eye(3), 0.5 * e2 - ep[:, dadr] @ dq))
                if float(np.abs(dq).max()) < 1e-6:
                    break
            q = q + np.clip(dq, -0.08, 0.08)
            for k, a in enumerate(qadr):
                self.d.qpos[a] = min(max(q[k], bounds[k][0]), bounds[k][1])
        mj.mj_forward(self.m, self.d)
        _, _, err = self._error(mode, site, goal)
        e_err = None if elbow is None else float(np.linalg.norm(elbow[2] - self.d.xanchor[elbow[0]]))
        return np.array([self.d.qpos[a] for a in qadr]), err, e_err

    def solve(self, side: str, mode: str, goal, elbow_goal=None):
        """Move one arm's 7 joints so its tool site reaches `goal` (reach) or its +z axis points at it
        (point_at), starting from the pose last set and staying as near it as the target allows.
        With `elbow_goal` (a world point), the elbow is brought as near it as the hand allows.
        Returns (outward degrees {1..7}, error, elbow error) — metres for reach, degrees for
        point_at; the elbow's in metres, or None without an elbow goal."""
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
        elbow = None
        if elbow_goal is not None:
            jid, body = self._elbow_point(side)
            elbow = (jid, body, np.asarray(elbow_goal, dtype=float))
        best = None
        for env in self._envelopes(bounds):
            mid = np.array([(lo + hi) / 2 for lo, hi in env])
            seeds = [q0, 0.5 * (q0 + mid), mid]
            if elbow is not None:
                # an elbow goal can need a different swivel than any nearby pose: try the upper arm
                # rolled each way too
                for roll in (-1.0, 1.0):
                    s2 = mid.copy()
                    s2[2] = mid[2] + roll * 0.4 * (env[2][1] - env[2][0])
                    seeds.append(s2)
            for seed in seeds:
                seed = np.array([min(max(v, lo), hi) for v, (lo, hi) in zip(seed, env)])
                q, err, e_err = self._descend(mode, site, goal, qadr, dadr, env, seed, q0, elbow=elbow)
                # the hand first; then (with an elbow goal) how near the elbow; then the smallest move
                score = (err > tol, round(e_err, 3) if e_err is not None and err <= tol else 0.0,
                         float(np.abs(q - q0).sum()) if err <= tol else err)
                if best is None or score < best[0]:
                    best = (score, q, err, e_err)
        q, err, e_err = best[1], best[2], best[3]
        for k, a in enumerate(qadr):
            self.d.qpos[a] = q[k]
        mj.mj_forward(self.m, self.d)
        sign = {j: (-1 if side == "left" and j in cue.MIRRORED_JOINTS else 1) for j in range(1, 8)}
        out = {j: round(math.degrees(q[j - 1]) * sign[j], 2) for j in range(1, 8)}
        return out, err, e_err


def _ik_cues(doc: dict) -> list:
    return [(i, c) for i, c in enumerate(doc.get("cues") or [])
            if isinstance(c, dict) and c.get("slot") in SIDE and target_key(c)]


def compile_show(path: str, limits_path: str, write: bool = True, verbose: bool = True, default_planner: str | None = None) -> list:
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
        egoal = kin.point(c["elbow"], side) if "elbow" in c else None
        sol, err, e_err = kin.solve(side, mode, goal, egoal)
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
        cue_planner = c.get("planner") or default_planner
        if mm < CLEAR_MIN_MM and cue_planner in ("cbirrt", "curobo"):
            # Attempt collision-free trajectory routing around obstacles with sim.planner
            try:
                from sim.planner import OpenArmPlanner
                planner = OpenArmPlanner(arm=side, backend=cue_planner)
                traj = planner.plan_to_joint_goal(q_from, q_to, timeout=5.0)
                if traj is not None and traj.min_clearance_mm >= 15.0:
                    mm = traj.min_clearance_mm
                    if verbose:
                        print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> ROUTED with {cue_planner.upper()} (min clearance: {mm:.1f} mm)")
            except Exception as e:
                if verbose:
                    print(f"  [ik.py] Planner {cue_planner} fallback failed: {e}")

        if mm < CLEAR_MIN_MM:
            problems.append(f"cue {i} ({c['slot']} {target_key(c)} at {t:.2f}s): the arm would come within "
                            f"{mm:.0f} mm of the robot ({ga} / {gb}) on the way or at the target "
                            f"(at least {CLEAR_MIN_MM:.0f} mm) — choose another target or a larger standoff")
            if verbose:
                print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> TOO CLOSE ({mm:.0f} mm: {ga} / {gb})")
            continue
        # The arm CONTROLLERS refuse a goal whose hand (hand_tcp) leaves their box (tcp.py): a target
        # whose solution does is refused here, the same way as one out of reach — no angles written
        chain = tcp.load_chain()
        if chain is not None:
            sign = -1 if c["slot"] == "ARM_L" else 1
            signed = [sol[j] * (sign if j in cue.MIRRORED_JOINTS else 1) for j in range(1, 8)]
            ok, z = tcp.hand_z_ok(chain, side, signed)
            if not ok:
                top = tcp.HAND_BOX["max"][2] - tcp.MARGIN_M
                problems.append(f"cue {i} ({c['slot']} {target_key(c)} at {t:.2f}s): the hand would be at z {z:.3f} m "
                                f"at the arm controllers' own point (hand_tcp), outside their box (z <= {top:.2f} m "
                                f"kept) — lower this target by at least {max(z - top, 0) * 100 + 0.5:.0f} cm")
                if verbose:
                    print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> OUTSIDE THE HAND BOX (z {z:.3f} m)")
                continue
        new = dict(c, solved={j: v for j, v in sol.items()}, solved_for=f"{target_key(c)}@{kin.sig}")
        new.pop("elbow_off", None)
        if e_err is not None:
            # how far the elbow ended from its goal: the validator warns above cue.ELBOW_WARN_M, so a
            # shape the arm cannot take is said, never silently bent
            new["elbow_off"] = round(e_err, 3)
        doc["cues"][i] = new
        text = yamledit.apply(text, [{"op": "set", "index": i, "cue": new}])
        if verbose:
            print(f"  {c['slot']} {target_key(c):30s} t={t:6.2f}  -> {sol}   ({err:.4f} {unit} off"
                  + (f"; elbow {e_err * 100:.1f} cm off" if e_err is not None else "") + ")")
    if write:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("compile", "check"))
    ap.add_argument("show")
    ap.add_argument("--limits", default=os.environ.get("OPENDUBS_ARM_LIMITS", cue.DEFAULT_LIMITS))
    ap.add_argument("--planner", choices=("spline", "cbirrt", "curobo"), default=None,
                    help="Planner for obstacle avoidance (default: spline or per-cue planner)")
    a = ap.parse_args()
    if not os.path.isfile(a.limits):
        a.limits = os.path.join(HERE, "tests", "joint_limits_fixture.yaml")
        print(f"ik: limits not found; using the TEST FIXTURE ({a.limits})")
    problems = compile_show(a.show, a.limits, write=a.cmd == "compile", default_planner=a.planner)
    for p in problems:
        print("  PROBLEM:", p)
    print("ok" if not problems else f"{len(problems)} target(s) out of reach")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
