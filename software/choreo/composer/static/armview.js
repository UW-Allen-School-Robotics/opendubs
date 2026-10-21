/* OpenArm 3D Visualizer for Show Composer
 * Dual-Arm 3D WebGL preview (Solid Robot + Ghost Target), Trajectory path, and Orbit Controls.
 * Based on OpenArm URDF kinematics with STL mesh loading and choreo coordinate transforms.
 */
import * as THREE from "/vendor/three.module.min.js";
import { STLLoader } from "/vendor/STLLoader.js";

const ASSETS = "/assets/arm/";
const MIRRORED_JOINTS = [1, 2, 3];

function urdfQuat(rpy) {
  const [r, p, y] = rpy;
  const qx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), r);
  const qy = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), p);
  const qz = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), y);
  return new THREE.Quaternion().copy(qz).multiply(qy).multiply(qx);
}

export function choreoToModelRad(side, jointsDeg) {
  const isLeft = side === "left";
  const angles = {};
  for (let j = 1; j <= 7; j++) {
    let deg = Number(jointsDeg && jointsDeg[j] != null ? jointsDeg[j] : 0.0);
    if (isLeft && MIRRORED_JOINTS.includes(j)) {
      deg = -deg;
    }
    angles[`openarm_${side}_joint${j}`] = (deg * Math.PI) / 180.0;
  }
  return angles;
}

export class Robot3DView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ready = false;

    // Resilient WebGL renderer initialization
    let renderer = null;
    let lastErr = null;
    const configs = [
      { antialias: true, alpha: true },
      { antialias: false, alpha: true },
      { antialias: false, alpha: false },
    ];
    for (const cfg of configs) {
      try {
        renderer = new THREE.WebGLRenderer({ canvas, ...cfg });
        if (renderer && renderer.getContext()) break;
      } catch (e) {
        lastErr = e;
      }
    }
    if (!renderer || !renderer.getContext()) {
      throw lastErr || new Error("WebGL context could not be created. Ensure hardware acceleration is enabled.");
    }
    this.renderer = renderer;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    // Scene setup with dark background matching composer theme
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a1017); // Dark navy/slate
    this.scene.up.set(0, 0, 1); // Z-up frame

    this.root = new THREE.Group();
    this.scene.add(this.root);

    // Subtle ground grid on XY plane at Z=0
    const grid = new THREE.GridHelper(1.6, 24, 0x19d1c4, 0x1f2a38);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = 0.0;
    this.scene.add(grid);

    // Subtle coordinate axes indicator at origin
    const axes = new THREE.AxesHelper(0.18);
    this.scene.add(axes);

    // Lighting (diffuse hemisphere + directional lights)
    this.scene.add(new THREE.HemisphereLight(0xe2e8f0, 0x131b26, 1.4));
    const key = new THREE.DirectionalLight(0xffffff, 1.8);
    key.position.set(2.5, -2.0, 3.0);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0x94a3b8, 1.0);
    fill.position.set(-2.5, 2.5, 2.0);
    this.scene.add(fill);

    const back = new THREE.DirectionalLight(0x4ea8ff, 0.5);
    back.position.set(0.0, 3.0, -1.0);
    this.scene.add(back);

    // Camera setup with perspective and orbit target
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 20.0);
    this.orbit = {
      target: new THREE.Vector3(0.0, 0.0, 0.65), // Centered around torso / shoulder level
      radius: 1.55,
      theta: 0.75, // azimuth
      phi: 1.18,   // polar angle from +Z
    };

    // Trajectory visualization group
    this.trajLine = null;
    this.trajMarkers = new THREE.Group();
    this.scene.add(this.trajMarkers);

    // Lambert materials: solid robot arms and subtle ghost target
    this.matSolid = new THREE.MeshLambertMaterial({
      color: 0x9fb4c9,
      side: THREE.DoubleSide,
    });
    this.matGhost = new THREE.MeshLambertMaterial({
      color: 0x19d1c4,
      transparent: true,
      opacity: 0.38,
      depthWrite: false,
      side: THREE.DoubleSide,
    });

    this.solidArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.ghostArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.ghostArm.root.visible = false; // Hidden unless a cue is inspected

    this.root.add(this.solidArm.root);
    this.root.add(this.ghostArm.root);

    this._updateCamera();
    this._setupInteraction();
  }

  async load() {
    const resp = await fetch(ASSETS + "chain.json");
    this.chain = await resp.json();
    const loader = new STLLoader();

    // Cache STL geometries so we download each mesh only once
    const geomCache = {};
    const meshesToLoad = Object.entries(this.chain.links).filter(([, v]) => v.mesh);
    await Promise.all(
      meshesToLoad.map(([_, v]) => {
        return new Promise((resolve) => {
          if (geomCache[v.mesh]) return resolve();
          loader.load(
            ASSETS + v.mesh,
            (geom) => {
              geomCache[v.mesh] = geom;
              resolve();
            },
            undefined,
            () => resolve()
          );
        });
      })
    );

    // Build solid arm and ghost arm trees
    this._buildArmTree(this.solidArm, this.matSolid, geomCache);
    this._buildArmTree(this.ghostArm, this.matGhost, geomCache);

    this.ready = true;
    this.frame();
    this.resize();
    this.render();
    return this;
  }

  _buildArmTree(armObj, material, geomCache) {
    const chain = this.chain;
    for (const name of Object.keys(chain.links)) {
      armObj.links[name] = new THREE.Group();
    }
    armObj.links[chain.root].name = chain.root;
    armObj.root.add(armObj.links[chain.root]);

    const pending = chain.joints.slice();
    let guard = pending.length + 2;
    while (pending.length && guard-- > 0) {
      for (let i = pending.length - 1; i >= 0; i--) {
        const j = pending[i];
        const parent = armObj.links[j.parent];
        if (!parent || !parent.parent) continue;
        const node = new THREE.Group();
        node.position.fromArray(j.xyz);
        node.quaternion.copy(urdfQuat(j.rpy));
        parent.add(node);
        if (j.type !== "fixed") {
          const spin = new THREE.Group();
          node.add(spin);
          spin.add(armObj.links[j.child]);
          armObj.joints[j.name] = {
            node: spin,
            axis: new THREE.Vector3().fromArray(j.axis).normalize(),
            lower: j.lower,
            upper: j.upper,
            value: 0,
            type: j.type,
          };
        } else {
          node.add(armObj.links[j.child]);
        }
        pending.splice(i, 1);
      }
    }

    // Attach STL meshes
    for (const [name, v] of Object.entries(chain.links)) {
      if (!v.mesh || !geomCache[v.mesh]) continue;
      const mesh = new THREE.Mesh(geomCache[v.mesh], material.clone());
      mesh.position.fromArray(v.mesh_xyz || [0, 0, 0]);
      mesh.quaternion.copy(urdfQuat(v.mesh_rpy || [0, 0, 0]));
      if (v.mesh_scale) mesh.scale.fromArray(v.mesh_scale);
      armObj.links[name].add(mesh);
    }
  }

  setJoints(armObj, angles) {
    for (const [name, q] of Object.entries(angles)) {
      const j = armObj.joints[name];
      if (!j || !Number.isFinite(q)) continue;
      j.value = q;
      if (j.type === "prismatic") {
        j.node.position.copy(j.axis).multiplyScalar(q);
      } else {
        j.node.quaternion.setFromAxisAngle(j.axis, q);
      }
    }
    this.render();
  }

  setGripper(arm, openFraction) {
    const fraction = Math.max(0, Math.min(1, Number(openFraction) || 0));
    const displacement = fraction * 0.044; // Finger stroke: 44 mm
    const angles = {
      [`openarm_${arm}_finger_joint1`]: displacement,
      [`openarm_${arm}_finger_joint2`]: displacement,
    };
    this.setJoints(this.solidArm, angles);
  }

  setSolidJoints(angles) {
    this.setJoints(this.solidArm, angles);
  }

  setGhostJoints(angles) {
    this.setJoints(this.ghostArm, angles);
  }

  setGhostVisible(visible) {
    if (this.ghostArm.root.visible !== !!visible) {
      this.ghostArm.root.visible = !!visible;
      this.render();
    }
  }

  setDualArmChoreoPose({ left, right, gripLeft, gripRight }) {
    if (!this.ready) return;
    const angles = {};
    if (left) Object.assign(angles, choreoToModelRad("left", left));
    if (right) Object.assign(angles, choreoToModelRad("right", right));

    if (gripLeft != null) {
      const d = Math.max(0, Math.min(1, Number(gripLeft))) * 0.044;
      angles["openarm_left_finger_joint1"] = d;
      angles["openarm_left_finger_joint2"] = d;
    }
    if (gripRight != null) {
      const d = Math.max(0, Math.min(1, Number(gripRight))) * 0.044;
      angles["openarm_right_finger_joint1"] = d;
      angles["openarm_right_finger_joint2"] = d;
    }
    this.setSolidJoints(angles);
  }

  setGhostCuePose(slot, jointsDeg) {
    if (!this.ready) return;
    const side = slot === "ARM_L" ? "left" : slot === "ARM_R" ? "right" : null;
    if (!side || !jointsDeg) {
      this.setGhostVisible(false);
      return;
    }
    const angles = choreoToModelRad(side, jointsDeg);
    this.setGhostJoints(angles);
    this.setGhostVisible(true);
  }

  setView(viewName) {
    const views = {
      iso: { theta: 0.75, phi: 1.15, radius: 1.55 },
      front: { theta: 0.0, phi: 1.57, radius: 1.45 },
      side: { theta: -Math.PI / 2, phi: 1.57, radius: 1.45 },
      top: { theta: 0.0, phi: 0.05, radius: 1.65 },
    };
    const v = views[viewName] || views.iso;
    this.orbit.theta = v.theta;
    this.orbit.phi = v.phi;
    this.orbit.radius = v.radius;
    this._updateCamera();
    this.render();
  }

  frame() {
    if (!this.ready) return;
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.solidArm.root);
    if (!box.isEmpty()) {
      const c = box.getCenter(new THREE.Vector3());
      this.orbit.target.copy(c);
      const span = box.getSize(new THREE.Vector3()).length();
      this.orbit.radius = Math.max(1.3, span * 1.25);
    }
    this._updateCamera();
  }

  _updateCamera() {
    const { theta, phi, radius, target } = this.orbit;
    const x = target.x + radius * Math.sin(phi) * Math.cos(theta);
    const y = target.y + radius * Math.sin(phi) * Math.sin(theta);
    const z = target.z + radius * Math.cos(phi);

    this.camera.position.set(x, y, z);
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(target);
    this.camera.updateMatrixWorld();
  }

  _setupInteraction() {
    let isDragging = false;
    let isPanning = false;
    let lastX = 0;
    let lastY = 0;

    this.canvas.addEventListener("contextmenu", (e) => e.preventDefault());

    this.canvas.addEventListener("pointerdown", (e) => {
      isDragging = e.button === 0 && !e.shiftKey;
      isPanning = e.button === 2 || (e.button === 0 && e.shiftKey);
      lastX = e.clientX;
      lastY = e.clientY;
      this.canvas.setPointerCapture(e.pointerId);
    });

    this.canvas.addEventListener("pointermove", (e) => {
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;

      if (isDragging) {
        this.orbit.theta -= dx * 0.008;
        this.orbit.phi = Math.max(0.05, Math.min(Math.PI - 0.05, this.orbit.phi - dy * 0.008));
        this._updateCamera();
        this.render();
      } else if (isPanning) {
        const forward = new THREE.Vector3().subVectors(this.orbit.target, this.camera.position).normalize();
        const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 0, 1)).normalize();
        const up = new THREE.Vector3().crossVectors(right, forward).normalize();

        const panScale = this.orbit.radius * 0.0015;
        this.orbit.target.addScaledVector(right, -dx * panScale);
        this.orbit.target.addScaledVector(up, dy * panScale);
        this._updateCamera();
        this.render();
      }
    });

    const stopDrag = () => {
      isDragging = false;
      isPanning = false;
    };
    this.canvas.addEventListener("pointerup", stopDrag);
    this.canvas.addEventListener("pointercancel", stopDrag);

    this.canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const zoom = e.deltaY > 0 ? 1.08 : 0.92;
      this.orbit.radius = Math.max(0.4, Math.min(5.0, this.orbit.radius * zoom));
      this._updateCamera();
      this.render();
    }, { passive: false });

    if (typeof ResizeObserver !== "undefined" && this.canvas.parentElement) {
      new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
    }
    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    const parent = this.canvas.parentElement;
    const w = parent ? parent.clientWidth : (this.canvas.clientWidth || 400);
    const h = parent ? parent.clientHeight : (this.canvas.clientHeight || 300);
    if (w <= 0 || h <= 0) return;

    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, true);
    this._updateCamera();
    this.render();
  }

  render() {
    if (this.ready) {
      this.renderer.render(this.scene, this.camera);
    }
  }
}
