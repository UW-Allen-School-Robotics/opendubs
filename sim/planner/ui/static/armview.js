/* OpenArm 3D Visualizer with Dual-Arm, Ghost Target, and Trajectory Ribbon.
 * Tailored for OpenArm Motion Planner Studio.
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

    this.scene = new THREE.Scene();
    this.scene.up.set(0, 0, 1); // Z-up like ROS / MuJoCo

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    this.root = new THREE.Group();
    this.scene.add(this.root);

    // Grid floor for spatial orientation
    const grid = new THREE.GridHelper(1.2, 24, 0x3d4f66, 0x1a2430);
    grid.rotation.x = Math.PI / 2; // Lie on XY plane (Z=0)
    grid.position.z = -0.001;
    this.scene.add(grid);

    // Subtle coordinate axes at base origin
    const axes = new THREE.AxesHelper(0.15);
    this.scene.add(axes);

    // Lighting
    this.scene.add(new THREE.HemisphereLight(0xdde8f5, 0x121720, 1.4));
    const key = new THREE.DirectionalLight(0xffffff, 1.8);
    key.position.set(2.0, -1.8, 2.5);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0x7090b0, 0.8);
    fill.position.set(-2.0, 2.0, 1.5);
    this.scene.add(fill);

    // Camera setup (Perspective with orbit controls)
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 10.0);
    this.orbit = {
      target: new THREE.Vector3(0.08, 0.0, 0.35),
      radius: 1.35,
      theta: 0.65, // azimuth
      phi: 1.15,   // polar (from Z up)
    };

    // Trajectory visualization line & markers
    this.trajLine = null;
    this.trajMarkers = new THREE.Group();
    this.scene.add(this.trajMarkers);

    // Materials
    this.matSolid = new THREE.MeshStandardMaterial({
      color: 0x98aabf,
      roughness: 0.45,
      metalness: 0.25,
    });
    this.matCollision = new THREE.MeshStandardMaterial({
      color: 0xef4444,
      roughness: 0.3,
      metalness: 0.1,
      emissive: 0x550a0a,
    });
    this.matGhost = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      roughness: 0.35,
      metalness: 0.1,
      transparent: true,
      opacity: 0.40,
      depthWrite: false,
    });

    this.solidArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.ghostArm = { joints: {}, links: {}, root: new THREE.Group() };
    this.root.add(this.solidArm.root);
    this.root.add(this.ghostArm.root);

    this._setupInteraction();
  }

  async load() {
    const resp = await fetch(ASSETS + "chain.json");
    this.chain = await resp.json();
    const loader = new STLLoader();

    // Cache STL geometries so we only download each once
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
    this._updateCamera();
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
      j.node.quaternion.setFromAxisAngle(j.axis, q);
    }
    this.render();
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

  setCollisionState(isCollision) {
    const mat = isCollision ? this.matCollision : this.matSolid;
    this.solidArm.root.traverse((child) => {
      if (child.isMesh) child.material = mat;
    });
    this.render();
  }

  setTrajectoryPath(eePositions) {
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

    if (!eePositions || eePositions.length === 0) {
      this.render();
      return;
    }

    // Create glowing trajectory line
    const points = eePositions.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const material = new THREE.LineBasicMaterial({
      color: 0x0284c7,
      linewidth: 3,
    });
    this.trajLine = new THREE.Line(geometry, material);
    this.scene.add(this.trajLine);

    // Start marker (Slate blue sphere)
    const startGeo = new THREE.SphereGeometry(0.015, 16, 16);
    const startMat = new THREE.MeshBasicMaterial({ color: 0x64748b });
    const startMesh = new THREE.Mesh(startGeo, startMat);
    startMesh.position.copy(points[0]);
    this.trajMarkers.add(startMesh);

    // Goal marker (Sky blue sphere)
    const goalGeo = new THREE.SphereGeometry(0.018, 16, 16);
    const goalMat = new THREE.MeshBasicMaterial({ color: 0x0284c7 });
    const goalMesh = new THREE.Mesh(goalGeo, goalMat);
    goalMesh.position.copy(points[points.length - 1]);
    this.trajMarkers.add(goalMesh);

    this.render();
  }

  setView(viewName) {
    const views = {
      iso: { theta: 0.65, phi: 1.15, radius: 1.35 },
      front: { theta: 0.0, phi: 1.57, radius: 1.3 },
      side: { theta: -Math.PI / 2, phi: 1.57, radius: 1.3 },
      top: { theta: 0.0, phi: 0.05, radius: 1.3 },
    };
    const v = views[viewName] || views.iso;
    this.orbit.theta = v.theta;
    this.orbit.phi = v.phi;
    this.orbit.radius = v.radius;
    this._updateCamera();
    this.render();
  }

  _updateCamera() {
    const { theta, phi, radius, target } = this.orbit;
    // Spherical coordinates with Z-up:
    // x = r * sin(phi) * cos(theta)
    // y = r * sin(phi) * sin(theta)
    // z = r * cos(phi)
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
        // Pan in camera plane
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
      this.orbit.radius = Math.max(0.3, Math.min(4.0, this.orbit.radius * zoom));
      this._updateCamera();
      this.render();
    }, { passive: false });

    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    const w = this.canvas.clientWidth || 640;
    const h = this.canvas.clientHeight || 480;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.render();
  }

  render() {
    if (this.ready) {
      this.renderer.render(this.scene, this.camera);
    }
  }
}
