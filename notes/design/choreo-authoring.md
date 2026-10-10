# Authoring expressive arm motion — what we use, and why

**Status:** decided 2026-10-10 (the user approved D1, via the orchestrator). The first part is built:
IK targets in the format (`software/choreo/ik.py`, plan item 1). Companion notes:
[`choreo-format-v1.md`](choreo-format-v1.md) (the format), [`choreo-teach-and-sync.md`](choreo-teach-and-sync.md)
(teach-in), [`../workplan-choreography.md`](../workplan-choreography.md).

## The recommendation

**Say where the hand goes, not seven joint angles — and keep every other tool an input to that.**

A show's arm cue may name a TARGET (`reach:` a point or a part of the robot, `point_at:` something to
aim at). The targets are solved at authoring time against the twin's own MuJoCo model, and the joint
angles are written into the show beside the target (`solved:`). The validator checks those angles
exactly as it checks hand-written ones — limits, the measured speed caps, the controller's trip, the
head clearance — and the runner plays them. So:

* the robot plays precisely what the twin previewed (no IK on the Thor, nothing solved at run time);
* an edited target with stale angles is refused until it is compiled again;
* every way of producing motion — a number typed in, a photo, a recording — lands in ONE checked form.

## The options we weighed

| Way of making motion | Verdict | Why |
|---|---|---|
| **Joint-space keyframes** (today) | keep, for fine control | Exact and already checked, but a dancer does not think in J1-J7. |
| **IK targets** — `reach`, `point_at` | **built (item 1)** | How choreographers and animators think ("hand to the head", "point at the audience"); one checked form underneath. |
| **Pose from reference images or video** (MediaPipe body pose → hand positions → IK targets) | **built (`pose_ref.py`)** | A photo or clip of a person becomes `reach`/`point_at` cues on the beats; noisy, so it proposes cues an author then keeps or edits. See "Reference images" below. |
| **Teach-in** (move the arm by hand in MANUAL, capture) | designed (`choreo-teach-and-sync.md`) | Truest to the real arm; needs the robot. |
| **Quest teleop recordings** as clips | later, with the Arms lane | Natural, but dense: a recording has to be thinned to keyframes an author can read. |
| **A rigged robot in Blender** (URDF import, animate IK targets, export keyframes) | **deferred** | Excellent curves and easing, but a heavy toolchain and a second model of the robot to keep in step with ours. Revisit if an animator joins. |

## One tool frame

`reach` and `point_at` act on MuJoCo's **`ee_site`** — the one tool frame the Arms lane names (its
0.20) — whose +z axis points out through the fingers. The URDFs' `hand_tcp` frames differ from it (the
flange, or 186 mm out); a pose for one of those would be off by up to ~18 cm, so nothing here uses
them. Anything that ever targets the LIVE arm solver goes through the Arms lane's SSIK adapter instead.

**The twin's arm model is the robot's, checked.** The twin loads openarm_assets at 71d81a8; the robot
pins 31eebb9. The Arms lane verified (2026-10-10, method `arm-kinematic-models.md`) that 71d81a8's arm
chain and `ee_site` are rigidly identical to the V1 URDF and to 31eebb9's model — over 500 random
configurations the tool site agrees to 0.0000 mm and 0.0000° — with `ee_site` at (0, -4.5, 180.1) mm
in the flange frame. The one joint difference, J4's lower limit (-2.9° in the MJCF, 0° on the robot),
never reaches a solve: the solver's bounds come from the robot's `joint_limits.yaml`. NOT checked: the
finger and collision geometry, which 71d81a8 changes — so the collision check below uses the head's
finger geometry, not necessarily the robot's.

## Safe by construction

* A reach to a named part of the robot (`head_screen`, `head`, `lift_platform`) stops **12 cm in
  front of it** (`standoff:` per cue, at least 5 cm) — never on it.
* Every solved move — the pose and the way into it — is checked against the twin's collision model:
  an arm that would come within **30 mm** of the robot's own body is refused, with the parts named.
  (Found by rendering the first version: a reach "to the head screen" put the gripper inside the head.)
* A target the arm cannot meet gets no angles at all, so the validator refuses it rather than
  playing angles that miss.
* The solver keeps to the validator's shoulder rules, not just each joint's limits: past ~135° the
  shoulder needs the elbow folded (the 1.2 m workspace box), and it never comes within the margin of
  the head. A hand raised overhead is reached by folding the elbow, never handed to the validator to
  refuse. (Found by the reference-image prototype: an overhead "Y" was solved at 146° with the arm
  straight.)

## Making it expressive — animation principles, inside the envelope

The arms' speed caps were measured (plan 6.1); motion.py already eases every move (zero speed at
reversals and holds, at most 1.5x the average mid-move). On top of that, as reusable clips rather
than new mechanisms:

* **Anticipation** — a small move the other way just before a big one (a wind-up before a point).
* **Follow-through and overshoot-settle** — past the target and back, within the caps.
* **Arcs** — a mid-keyframe off the straight line, so a hand travels a curve, not a chord.
* **Effort qualities (Laban)** — *sudden / sustained* is timing (the measured caps bound "sudden");
  *direct / indirect* is the path (a straight reach versus an arc); *strong / light* reads as amplitude.
* **Pointing on a beat** — `point_at` lands the aim at the cue's time; the move into it starts at the
  previous keyframe, so leave it a beat or two of travel.

## Targets

A point `[x, y, z]` in metres in the CHASSIS frame (x forward, y left, z up — robot-relative; there is
no localisation yet), or a name: `head_screen`, `head`, `lift_platform`, `left_hand` / `right_hand`
(the other arm's tool, where the show has it at that moment). Not yet: targets inside clips, a frame
picker in the composer, room points that stay fixed while the base moves.

## Reference images

`software/choreo/pose_ref.py` turns a photo or video of a person into proposed arm cues:

    python3 pose_ref.py propose photo.jpg --at bar:9.1 --overlay /tmp/check
    python3 pose_ref.py propose clip.mp4 --at 1.2=bar:9.1 --at 2.0=bar:9.3 --into shows/my-show.yaml

MediaPipe Pose (`pip install mediapipe==0.10.21`; the model ships inside the package, CPU only) finds
the person's shoulders, elbows, wrists and hands in 3-D. Each hand is taken relative to its own
shoulder, in the person's body frame (forward, left, up from their shoulders and hips), so the
camera's angle and the person's facing drop out. The robot copies the DIRECTION from its own shoulder
and the same FRACTION of its reach (0.616 m from shoulder to tool site, measured in the twin; stopping
at 95% because a straight arm is a singular pose). `--mirror` swaps sides for a performer facing the
robot; `--point` proposes `point_at:` along the person's arm instead.

What it deliberately does not do:

* **It does not copy the elbow.** The OpenArm is not a human arm; the solver picks the elbow nearest
  where the show has the arm at that moment. Copy the hand, then judge the elbow in the twin.
* **It does not bend a pose to fit.** The proposals are ordinary IK targets: `ik.py` solves them and
  the validator checks them, so a pose the robot cannot copy is refused with the reason (a hand
  reaching across the body is out of this arm's reach; a hand behind the back is not — its shoulder
  swings back).
* **It does not guess.** A side MediaPipe is not confident it can see is skipped, and said.
* **It does not move anything.** It writes cues; `--overlay` saves each frame with the skeleton it
  found, so the author can check the read before keeping the cues.
