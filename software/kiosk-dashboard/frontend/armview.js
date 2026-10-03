/* Arm pose view — real OpenArm geometry, driven by /joint_states.
 *
 * Renders the vendored COLLISION meshes (assets/arm/, 0.75 MB) through the kinematic tree in
 * assets/arm/chain.json, which tools/arm_chain.py generates from the vendored URDF. Forward
 * kinematics is done here rather than read from TF, so the view needs no robot_state_publisher,
 * no TF and no xacro at runtime — only joint angles.
 *
 * Why real geometry and not a stick figure: three of the seven joints per arm (3, 5 and 7) rotate
 * about the limb axis. A roll moves no link origin at all — measured: 37 deg on joint 3 moves the
 * end point 0.0 mm — so a skeleton of link origins is BLIND to it, and the robot sat in exactly
 * that state (right arm at j3 -36.6 deg) looking identical to stowed. Asymmetric geometry shows it
 * for free.
 */
import * as THREE from "./vendor/three.module.min.js";
import { STLLoader } from "./vendor/STLLoader.js";

const ASSETS = "assets/arm/";

/* URDF rpy is Rz(yaw) * Ry(pitch) * Rx(roll). Built from explicit quaternions rather than a
 * THREE.Euler order string, because the mapping from URDF's fixed-axis convention to three's
 * intrinsic Euler orders is easy to get subtly wrong and the failure is a silently wrong pose. */
function urdfQuat(rpy) {
  const [r, p, y] = rpy;
  const q = new THREE.Quaternion();
  const qx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), r);
  const qy = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), p);
  const qz = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), y);
  return q.copy(qz).multiply(qy).multiply(qx);
}

export class ArmView {
  constructor(canvas) {
    this.canvas = canvas;
    this.joints = {};          // joint name -> { node, axis, lower, upper, value }
    this.links = {};           // link name  -> THREE.Group
    this.ready = false;

    this.scene = new THREE.Scene();
    // Z-up, like every ROS frame. three defaults to Y-up, and not fixing this is why robot
    // renders so often come out lying on their side.
    this.scene.up.set(0, 0, 1);

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.scene.add(new THREE.HemisphereLight(0xbcd6f0, 0x0b1017, 1.15));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(1.4, -1.1, 1.8);
    this.scene.add(key);

    // Orthographic throughout, including the default iso: a perspective camera makes two arms at
    // different depths look different sizes, which is the opposite of what a pose readout wants.
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -20, 20);
    this.view = "iso";
  }

  async load() {
    const chain = await (await fetch(ASSETS + "chain.json")).json();
    this.chain = chain;   // kept so callers can read _fk_checks and joint limits
    const loader = new STLLoader();

    for (const name of Object.keys(chain.links)) this.links[name] = new THREE.Group();
    this.links[chain.root].name = chain.root;
    this.root.add(this.links[chain.root]);

    // Joints may appear in any order, so resolve by repeated passes rather than assuming the URDF
    // lists a parent before its child.
    const pending = chain.joints.slice();
    let guard = pending.length + 2;
    while (pending.length && guard-- > 0) {
      for (let i = pending.length - 1; i >= 0; i--) {
        const j = pending[i];
        const parent = this.links[j.parent];
        if (!parent || !parent.parent) continue;        // parent not yet attached
        const node = new THREE.Group();
        node.position.fromArray(j.xyz);
        node.quaternion.copy(urdfQuat(j.rpy));
        parent.add(node);
        if (j.type !== "fixed") {
          const spin = new THREE.Group();
          node.add(spin);
          spin.add(this.links[j.child]);
          this.joints[j.name] = {
            node: spin,
            axis: new THREE.Vector3().fromArray(j.axis).normalize(),
            lower: j.lower, upper: j.upper, value: 0,
          };
        } else {
          node.add(this.links[j.child]);
        }
        pending.splice(i, 1);
      }
    }
    if (pending.length) throw new Error("unattached joints: " + pending.map((j) => j.name).join(", "));

    const mat = new THREE.MeshLambertMaterial({ color: 0x9fb4c9 });
    const meshes = Object.entries(chain.links).filter(([, v]) => v.mesh);
    await Promise.all(meshes.map(([name, v]) => new Promise((res) => {
      loader.load(ASSETS + v.mesh, (geom) => {
        const m = new THREE.Mesh(geom, mat);
        m.position.fromArray(v.mesh_xyz || [0, 0, 0]);
        m.quaternion.copy(urdfQuat(v.mesh_rpy || [0, 0, 0]));
        if (v.mesh_scale) m.scale.fromArray(v.mesh_scale);
        this.links[name].add(m);
        res();
      }, undefined, () => res());    // a missing mesh must not block the whole view
    })));

    this.ready = true;
    this.frame();
    this.setView(this.view);
    return this;
  }

  /* angles: { "openarm_left_joint1": radians, ... } — extra names are ignored, so /joint_states
   * can be passed through verbatim including the finger joints. */
  setJoints(angles) {
    for (const [name, q] of Object.entries(angles)) {
      const j = this.joints[name];
      if (!j || !Number.isFinite(q)) continue;
      j.value = q;
      j.node.quaternion.setFromAxisAngle(j.axis, q);
    }
    this.render();
  }

  /* World position of a link origin — used by the test harness to check this FK against the
   * independently-verified Python one in tools/arm_chain.py's docstring. */
  linkWorld(name) {
    const g = this.links[name];
    if (!g) return null;
    this.root.updateMatrixWorld(true);
    return new THREE.Vector3().setFromMatrixPosition(g.matrixWorld);
  }

  setView(v) {
    this.view = v;
    const d = { iso: [1, -1, 0.75], front: [1, 0, 0], side: [0, -1, 0], top: [0, 0, 1] }[v] || [1, -1, 0.75];
    this.dir = new THREE.Vector3().fromArray(d).normalize();
    this.frame();
    this.render();
  }

  /* Fit the camera to the model every time, so the view does not depend on hand-tuned numbers
   * that would rot the moment the URDF changes. */
  frame() {
    if (!this.ready) return;
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.root);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() * 0.5, 0.1);
    this.camera.position.copy(c).addScaledVector(this.dir || new THREE.Vector3(1, -1, 0.75), r * 4);
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(c);
    this.resize(r);
  }

  resize(radius) {
    const w = this.canvas.clientWidth || 400, h = this.canvas.clientHeight || 300;
    this.renderer.setSize(w, h, false);
    const r = radius || 0.6;
    const aspect = w / h;
    this.camera.left = -r * 1.15 * aspect; this.camera.right = r * 1.15 * aspect;
    this.camera.top = r * 1.15; this.camera.bottom = -r * 1.15;
    this.camera.updateProjectionMatrix();
  }

  render() { if (this.ready) this.renderer.render(this.scene, this.camera); }
}
