/* OpenArm 3D Visualizer for Motion Planner Studio
 * Dual-Arm (Solid + Ghost Target), Trajectory Ribbon, and Orbit Controls.
 * Built on OpenArm URDF kinematics with automatic frame bounding.
 */
import * as THREE from "/vendor/three.module.min.js";
import { STLLoader } from "/vendor/STLLoader.js";

const ASSETS = "/assets/arm/";

function urdfQuat(rpy) {
  const [r, p, y] = rpy;
  const qx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), r);
  const qy = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), p);
  const qz = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), y);
  return new THREE.Quaternion().copy(qz).multiply(qy).multiply(qx);
}

export class PlannerStudioView {
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
      throw (lastErr || new Error("WebGL context could not be created. Ensure hardware acceleration is enabled."));
    }
    this.renderer = renderer;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    // Scene setup with subtle dark slate background
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0f172a); // Slate-900
    this.scene.up.set(0, 0, 1); // Z-up frame

    this.root = new THREE.Group();
    this.scene.add(this.root);

    // Floor grid (on XY plane, Z=0)
    const grid = new THREE.GridHelper(1.6, 24, 0x38bdf8, 0x334155);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = 0.0;
    this.scene.add(grid);

    // Coordinate axes indicator at origin
    const axes = new THREE.AxesHelper(0.2);
    this.scene.add(axes);

    // Lighting (diffuse hemisphere + multiple directional lights)
    this.scene.add(new THREE.HemisphereLight(0xe2e8f0, 0x1e293b, 1.4));
    const key = new THREE.DirectionalLight(0xffffff, 1.8);
    key.position.set(2.5, -2.0, 3.0);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0x94a3b8, 1.0);
    fill.position.set(-2.5, 2.5, 2.0);
    this.scene.add(fill);

    const back = new THREE.DirectionalLight(0x64748b, 0.6);
    back.position.set(0.0, 3.0, -1.0);
    this.scene.add(back);

    // Camera setup (Perspective with orbit controls)
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.05, 20.0);
    this.orbit = {
      target: new THREE.Vector3(0.0, 0.0, 0.65), // Centered around shoulder height
      radius: 1.5,
      theta: 0.75, // azimuth
      phi: 1.20,   // polar angle from +Z
    };

    // Trajectory visualization
    this.trajLine = null;
    this.trajMarkers = new THREE.Group();
    this.scene.add(this.trajMarkers);

    // Double-sided Lambert materials (clean diffuse, no PBR/HDR dependency, reliable on all GPUs)
    this.matSolid = new THREE.MeshLambertMaterial({
      color: 0x9fb4c9,
      side: THREE.DoubleSide,
    });
    this.matCollision = new THREE.MeshLambertMaterial({
      color: 0xef4444,
      emissive: 0x5a1010,
      side: THREE.DoubleSide,
    });
    this.matGhost = new THREE.MeshLambertMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.42,
      depthWrite: false,
      side: THREE.DoubleSide,
    });

    this.solidArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.ghostArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.root.add(this.solidArm.root);
    this.root.add(this.ghostArm.root);

    this._updateCamera();
    this._setupInteraction();
  }

  async load() {
    const resp = await fetch(ASSETS + "chain.json");
    this.chain = await resp.json();
    const loader = new STLLoader();

    // Cache STL geometries so we download each file only once
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

    // Attach meshes
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
    // Finger joint stroke: 0.044 m (44 mm)
    const displacement = fraction * 0.044;
    const angles = {
      [`openarm_${arm}_finger_joint1`]: displacement,
      [`openarm_${arm}_finger_joint2`]: displacement,
    };
    this.setJoints(this.solidArm, angles);
    this.setJoints(this.ghostArm, angles);
  }

  setSolidJoints(angles) {
    this.setJoints(this.solidArm, angles);
  }

  setGhostJoints(angles) {
    this.setJoints(this.ghostArm, angles);
  }

  setGhostVisible(visible) {
    this.ghostArm.root.visible = visible;
    this.render();
  }

  setActiveEditArm(mode) {
    const isGoal = mode === "goal";
    // When editing Goal Target: ghost arm is prominent (opacity 0.75), solid is secondary (opacity 0.60)
    // When editing Start Pose: solid arm is prominent (opacity 1.0), ghost is secondary (opacity 0.28)
    this.ghostArm.root.traverse((child) => {
      if (child.isMesh && child.material) {
        child.material.opacity = isGoal ? 0.75 : 0.28;
        child.material.transparent = true;
        child.material.needsUpdate = true;
      }
    });
    this.solidArm.root.traverse((child) => {
      if (child.isMesh && child.material) {
        child.material.opacity = isGoal ? 0.60 : 1.0;
        child.material.transparent = isGoal;
        child.material.needsUpdate = true;
      }
    });
    this.render();
  }

  setCollisionState(isCollision) {
    const mat = isCollision ? this.matCollision : this.matSolid;
    this.solidArm.root.traverse((child) => {
      if (child.isMesh) child.material = mat;
    });
    this.render();
  }

  _getTipWorldPosition(armObj, arm) {
    const link7 = armObj.links[`openarm_${arm}_link7`];
    if (!link7) return new THREE.Vector3();
    // 0.186m along link7's Z axis reaches the physical fingertip grasp center
    // (matches the OpenDubs impedance controller TCP in software/choreo/tcp.py)
    const pos = new THREE.Vector3(0, 0, 0.186);
    link7.localToWorld(pos);
    return pos;
  }

  _computeTcpPath(arm, trajectoryPositionsRad) {
    if (!this.ready || !trajectoryPositionsRad || trajectoryPositionsRad.length === 0) return [];

    // Save current ghost arm joint angles
    const saved = {};
    for (const [name, j] of Object.entries(this.ghostArm.joints)) {
      saved[name] = j.value;
    }

    const jointPrefix = `openarm_${arm}_joint`;
    const points = [];

    // Downsample waypoints if very dense (smooth ribbon)
    const step = Math.max(1, Math.floor(trajectoryPositionsRad.length / 120));
    for (let k = 0; k < trajectoryPositionsRad.length; k += step) {
      const q = trajectoryPositionsRad[k];
      for (let i = 0; i < 7; i++) {
        const jName = `${jointPrefix}${i + 1}`;
        const j = this.ghostArm.joints[jName];
        if (j) j.node.quaternion.setFromAxisAngle(j.axis, q[i]);
      }
      this.root.updateMatrixWorld(true);
      points.push(this._getTipWorldPosition(this.ghostArm, arm));
    }

    // Always append the final goal configuration waypoint
    if ((trajectoryPositionsRad.length - 1) % step !== 0) {
      const qLast = trajectoryPositionsRad[trajectoryPositionsRad.length - 1];
      for (let i = 0; i < 7; i++) {
        const jName = `${jointPrefix}${i + 1}`;
        const j = this.ghostArm.joints[jName];
        if (j) j.node.quaternion.setFromAxisAngle(j.axis, qLast[i]);
      }
      this.root.updateMatrixWorld(true);
      points.push(this._getTipWorldPosition(this.ghostArm, arm));
    }

    // Restore ghost arm joint angles
    for (const [name, val] of Object.entries(saved)) {
      const j = this.ghostArm.joints[name];
      if (j) j.node.quaternion.setFromAxisAngle(j.axis, val);
    }
    this.root.updateMatrixWorld(true);

    return points;
  }

  getTcpWorldPosition(arm, isGoal = true) {
    if (!this.ready) return null;
    const armObj = isGoal ? this.ghostArm : this.solidArm;
    this.root.updateMatrixWorld(true);
    return this._getTipWorldPosition(armObj, arm);
  }

  setTrajectoryPath(armOrPositions, maybePositions) {
    // Remove existing trajectory path
    if (this.trajLine) {
      this.scene.remove(this.trajLine);
      this.trajLine.geometry.dispose();
      this.trajLine.material.dispose();
      this.trajLine = null;
    }
    while (this.trajMarkers.children.length > 0) {
      const c = this.trajMarkers.children[0];
      this.trajMarkers.remove(c);
      if (c.geometry) c.geometry.dispose();
      if (c.material) c.material.dispose();
    }

    let points = [];
    if (typeof armOrPositions === "string" && Array.isArray(maybePositions)) {
      points = this._computeTcpPath(armOrPositions, maybePositions);
    } else if (Array.isArray(armOrPositions) && armOrPositions.length > 0) {
      if (Array.isArray(armOrPositions[0]) && armOrPositions[0].length === 7) {
        // Array of joint angle vectors, defaults to left arm
        points = this._computeTcpPath("left", armOrPositions);
      } else if (Array.isArray(armOrPositions[0]) && armOrPositions[0].length === 3) {
        // Fallback for direct [x, y, z] vectors
        points = armOrPositions.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      }
    }

    if (!points || points.length < 2) {
      this.render();
      return;
    }

    // Render path ribbon
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const material = new THREE.LineBasicMaterial({
      color: 0x38bdf8,
      linewidth: 3,
    });
    this.trajLine = new THREE.Line(geometry, material);
    this.scene.add(this.trajLine);

    // Goal marker sphere centered directly at the goal TCP
    const goalGeom = new THREE.SphereGeometry(0.02, 16, 16);
    const goalMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const goalMesh = new THREE.Mesh(goalGeom, goalMat);
    goalMesh.position.copy(points[points.length - 1]);
    this.trajMarkers.add(goalMesh);

    this.render();
  }

  setView(viewName) {
    const views = {
      iso: { theta: 0.75, phi: 1.15, radius: 1.6 },
      front: { theta: 0.0, phi: 1.57, radius: 1.5 },
      side: { theta: -Math.PI / 2, phi: 1.57, radius: 1.5 },
      top: { theta: 0.0, phi: 0.05, radius: 1.7 },
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
    // Spherical coordinates with Z-up:
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

    // Automatic container resize observer
    if (typeof ResizeObserver !== "undefined" && this.canvas.parentElement) {
      new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
    }
    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    const parent = this.canvas.parentElement;
    const w = parent ? parent.clientWidth : (this.canvas.clientWidth || 800);
    const h = parent ? parent.clientHeight : (this.canvas.clientHeight || 600);
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
