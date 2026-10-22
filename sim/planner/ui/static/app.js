/* OpenArm Motion Planner Studio & Halloween Show Designer
 * Main Application Logic
 */
import { PlannerStudioView } from "./armview.js?v=2.5";

function logClient(level, message, stack = "") {
  try {
    fetch("/api/client_log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level, message, stack }),
    }).catch(() => {});
  } catch (_) {}
}

window.onerror = function (msg, url, lineNo, colNo, error) {
  logClient("error", `${msg} (${url}:${lineNo}:${colNo})`, error ? error.stack : "");
};

window.onunhandledrejection = function (event) {
  const reason = event.reason;
  logClient("unhandled_rejection", String(reason && reason.message ? reason.message : reason), reason ? reason.stack : "");
};

// Joint limits in degrees for OpenArm V1
const JOINT_LIMITS_DEG = [
  [-170, 170], // J1
  [-100, 100], // J2
  [-170, 170], // J3
  [0, 135],    // J4
  [-170, 170], // J5
  [-100, 100], // J6
  [-170, 170], // J7
];

const BASE_PRESETS = {
  stow: [0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0],
  zero: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  reach_forward: [0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0],
  high_reach: [0.0, -50.0, 0.0, 45.0, 0.0, 20.0, 0.0],
  wave_ready: [20.0, -20.0, 30.0, 70.0, -10.0, 20.0, 0.0],
  zombie_reach: [25.0, -15.0, 0.0, 50.0, 0.0, 0.0, 0.0],
  spooky_arms_up: [15.0, -60.0, 10.0, 40.0, -20.0, 30.0, 0.0],
  pumpkin_offer: [10.0, -25.0, 0.0, 75.0, 0.0, 30.0, 0.0],
  spooky_wave: [20.0, -40.0, 30.0, 70.0, 0.0, 20.0, 0.0],
  cauldron_stir: [0.0, -35.0, 15.0, 55.0, 30.0, 10.0, 0.0],
};

function getPresetsForArm(arm) {
  const isRight = arm === "right";
  const mirror = (q) => (isRight ? [-q[0], -q[1], -q[2], q[3], q[4], q[5], q[6]] : [...q]);
  const out = {};
  for (const [k, v] of Object.entries(BASE_PRESETS)) {
    out[k] = mirror(v);
  }
  return out;
}

class StudioApp {
  constructor() {
    this.arm = "left";
    this.targetMode = "goal"; // "goal" or "start"
    const p = getPresetsForArm(this.arm);
    this.qStart = [...p.stow];
    this.qGoal = [...p.zombie_reach];

    this.trajectory = null;
    this.isPlaying = false;
    this.isLooping = true;
    this.playbackSpeed = 1.0;
    this.currentTime = 0.0;
    this.animLastTime = 0;

    this.keyframes = [];
    this.collisionDebounceTimer = null;
    this.gripperState = { left: 1.0, right: 1.0 }; // 1.0 = open, 0.0 = closed
  }

  async init() {
    // 1. Build sliders and bind events FIRST so all buttons work immediately
    this._createJointSliders();
    this._bindEvents();
    this._setTargetMode("goal");
    this._updateGripperUI();

    // 2. Fetch backend status and keyframes in parallel
    this._loadBackendStatus();
    this._loadKeyframes();
    this._fetchGripperStatus();
    this._checkCollisionAndFK();
    this._pollHardwareStatus();

    // 3. Initialize 3D view gracefully without blocking buttons or UI
    const canvas = document.getElementById("arm-canvas");
    try {
      if (!canvas) throw new Error("Canvas element #arm-canvas not found in DOM");
      logClient("info", `Initializing 3D view on canvas (${canvas.clientWidth}x${canvas.clientHeight})...`);
      this.view = await new PlannerStudioView(canvas).load();
      logClient("info", "3D WebGL PlannerStudioView loaded successfully.");
      this._updateArmPosesInView();
      if (this.view.setActiveEditArm) {
        this.view.setActiveEditArm(this.targetMode);
      }
      if (this.view.setGripper) {
        this.view.setGripper(this.arm, this.gripperState[this.arm]);
      }
      requestAnimationFrame((t) => this._playbackLoop(t));
    } catch (err) {
      logClient("error", `3D visualizer init failed: ${err.message || err}`, err.stack || "");
      console.warn("3D WebGL initialization error (controls remain active):", err);
      this._showWebGLErrorBanner(err);
    }
  }

  _showWebGLErrorBanner(err) {
    const container = document.getElementById("canvas-container");
    if (!container) return;
    const existing = container.querySelector(".webgl-fallback-card");
    if (existing) existing.remove();

    const banner = document.createElement("div");
    banner.className = "webgl-fallback-card";
    banner.innerHTML = `
      <div style="font-weight: 600; font-size: 13px; color: #f87171; margin-bottom: 6px;">
        3D Robot Viewport Notice (WebGL)
      </div>
      <div style="font-size: 12px; color: #cbd5e1; line-height: 1.5; margin-bottom: 8px;">
        Hardware WebGL acceleration is currently disabled or unavailable in this browser session.
        <span style="color:#94a3b8; font-size:11px;">(${err.message || err})</span>
      </div>
      <div style="font-size: 11px; color: #94a3b8; background: rgba(0,0,0,0.35); padding: 8px 10px; border-radius: 4px; margin-bottom: 8px; line-height: 1.4;">
        <strong>To enable 3D robot rendering:</strong><br>
        • Chrome/Brave: <code>chrome://settings/system</code> &rarr; toggle <em>"Use graphics acceleration when available"</em> & restart browser.<br>
        • In Chrome, check <code>chrome://gpu</code> to confirm WebGL status.
      </div>
      <div style="font-size: 11px; color: var(--accent-cyan);">
        All motion planning tools (cuRobo, CBiRRT, 7-DOF jogging, SSIK inverse kinematics, MuJoCo collision checking, and show sequence generation) remain 100% active and functional.
      </div>
    `;
    container.appendChild(banner);
  }

  async _loadBackendStatus() {
    try {
      const resp = await fetch("/api/status");
      const data = await resp.json();
      const selector = document.getElementById("backend-selector");
      if (!selector) return;
      selector.innerHTML = "";

      const backends = data.available_backends || {};
      for (const [key, info] of Object.entries(backends)) {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = `${info.name.toUpperCase()} ${info.available ? "" : "(not installed)"}`;
        opt.disabled = !info.available;
        opt.selected = key === data.active_backend;
        selector.appendChild(opt);
      }

      selector.onchange = async () => {
        const chosen = selector.value;
        const res = await fetch("/api/backend", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ backend: chosen }),
        });
        const resData = await res.json();
        if (resData.success) {
          document.getElementById("plan-perf-info").textContent = `Planner: ${chosen.toUpperCase()}`;
        } else {
          alert(`Failed to switch backend: ${resData.error}`);
          this._loadBackendStatus();
        }
      };

      if (data.active_backend) {
        document.getElementById("plan-perf-info").textContent = `Planner: ${data.active_backend.toUpperCase()} + SSIK`;
      }
    } catch (err) {
      console.warn("Failed to load backends:", err);
    }
  }

  _getJointName(idx) {
    return `openarm_${this.arm}_joint${idx + 1}`;
  }

  _createJointSliders() {
    const container = document.getElementById("joint-sliders-container");
    container.innerHTML = "";

    for (let i = 0; i < 7; i++) {
      const row = document.createElement("div");
      row.className = "joint-slider-row";

      const [minDeg, maxDeg] = JOINT_LIMITS_DEG[i];
      row.innerHTML = `
        <span class="joint-label">J${i + 1}</span>
        <input type="range" id="slider-j${i}" min="${minDeg}" max="${maxDeg}" step="0.5" value="0">
        <span class="joint-val" id="val-j${i}">0°</span>
      `;

      const slider = row.querySelector(`#slider-j${i}`);
      slider.addEventListener("input", () => {
        const val = parseFloat(slider.value);
        if (this.targetMode === "goal") {
          this.qGoal[i] = val;
        } else {
          this.qStart[i] = val;
        }
        document.getElementById(`val-j${i}`).textContent = `${val.toFixed(1)}°`;
        this._syncPresetDropdown();
        this._updateArmPosesInView();
        this._scheduleCollisionCheck();
      });

      container.appendChild(row);
    }
  }

  _bindEvents() {
    // Arm selector buttons
    document.getElementById("btn-arm-left").onclick = () => this._setArm("left");
    document.getElementById("btn-arm-right").onclick = () => this._setArm("right");

    // Target mode toggle
    document.getElementById("btn-mode-goal").onclick = () => this._setTargetMode("goal");
    document.getElementById("btn-mode-start").onclick = () => this._setTargetMode("start");

    // Presets dropdown
    document.getElementById("preset-select").addEventListener("change", (e) => {
      this._applySelectedPreset(e.target.value);
    });

    // View buttons
    document.querySelectorAll(".view-btn").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".view-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        if (this.view && this.view.setView) this.view.setView(btn.dataset.view);
      };
    });

    // Solve IK Button (SSIK)
    document.getElementById("btn-solve-ik").onclick = () => this._solveIK();

    // Copy / Swap buttons
    document.getElementById("btn-copy-target-to-start").onclick = () => {
      this.qStart = [...this.qGoal];
      this._syncUIFromState();
      this._updateArmPosesInView();
      this._checkCollisionAndFK();
    };

    document.getElementById("btn-swap-poses").onclick = () => {
      const tmp = [...this.qStart];
      this.qStart = [...this.qGoal];
      this.qGoal = [...tmp];
      this._syncUIFromState();
      this._updateArmPosesInView();
      this._checkCollisionAndFK();
    };

    // Plan Motion Button
    document.getElementById("btn-plan-motion").onclick = () => this._planSingleTrajectory();

    // Playback Controls
    document.getElementById("btn-play-pause").onclick = () => this._togglePlayPause();
    document.getElementById("btn-loop-toggle").onclick = () => {
      this.isLooping = !this.isLooping;
      document.getElementById("btn-loop-toggle").style.borderColor = this.isLooping
        ? "var(--accent-toxic)"
        : "var(--border-color)";
    };
    document.getElementById("playback-speed-select").onchange = (e) => {
      this.playbackSpeed = parseFloat(e.target.value);
    };

    const scrubber = document.getElementById("timeline-scrubber");
    scrubber.addEventListener("input", () => {
      this.currentTime = parseFloat(scrubber.value);
      this._applyTrajectoryAtTime(this.currentTime);
    });

    // Keyframe choreographer buttons
    document.getElementById("btn-add-keyframe").onclick = () => this._addKeyframe();
    document.getElementById("btn-plan-show-sequence").onclick = () => this._planShowSequence();
    document.getElementById("btn-export-show-yaml").onclick = () => this._openYamlModal();
    document.getElementById("btn-close-modal").onclick = () => this._closeYamlModal();
    document.getElementById("btn-copy-yaml").onclick = () => this._copyYamlToClipboard();
    document.getElementById("btn-download-yaml").onclick = () => this._downloadYaml();
    document.getElementById("btn-mirror-sequence").onclick = () => {
      const otherArm = this.arm === "left" ? "right" : "left";
      this.keyframes.forEach((kf) => {
        kf.arm = otherArm;
        kf.joints_deg = [-kf.joints_deg[0], -kf.joints_deg[1], -kf.joints_deg[2], kf.joints_deg[3], kf.joints_deg[4], kf.joints_deg[5], kf.joints_deg[6]];
      });
      this._setArm(otherArm);
    };

    // Import & Save choreo buttons
    const bindClick = (id, fn) => {
      const el = document.getElementById(id);
      if (el) el.onclick = fn;
    };
    bindClick("btn-import-choreo", () => this._openImportModal());
    bindClick("btn-import-choreo-main", () => this._openImportModal());
    bindClick("btn-close-import-modal", () => this._closeImportModal());
    bindClick("btn-import-cancel", () => this._closeImportModal());
    bindClick("btn-import-load-library", () => this._importFromLibrary());
    bindClick("btn-import-paste-submit", () => this._importFromText());

    const fileInput = document.getElementById("import-file-input");
    if (fileInput) {
      fileInput.onchange = (e) => {
        if (e.target.files && e.target.files[0]) {
          this._importFromFile(e.target.files[0]);
        }
      };
    }

    bindClick("btn-save-choreo", () => this._openSaveModal());
    bindClick("btn-save-choreo-main", () => this._openSaveModal());
    bindClick("btn-close-save-modal", () => this._closeSaveModal());
    bindClick("btn-save-cancel", () => this._closeSaveModal());
    bindClick("btn-save-confirm", () => this._saveNamedChoreo());

    // Benchmark modal buttons
    document.getElementById("btn-open-benchmark").onclick = () => this._openBenchmarkModal();
    document.getElementById("btn-close-benchmark").onclick = () => this._closeBenchmarkModal();
    document.getElementById("btn-close-benchmark-footer").onclick = () => this._closeBenchmarkModal();
    document.getElementById("btn-run-benchmark-now").onclick = () => this._runBenchmark();

    // Gripper controls
    const gripSlider = document.getElementById("gripper-slider");
    if (gripSlider) {
      gripSlider.addEventListener("input", (e) => {
        this._setGripper(parseFloat(e.target.value) / 100.0, false);
      });
      gripSlider.addEventListener("change", (e) => {
        this._setGripper(parseFloat(e.target.value) / 100.0, true);
      });
    }
    const gripToggle = document.getElementById("btn-gripper-toggle");
    if (gripToggle) {
      gripToggle.onclick = () => this._toggleGripper();
    }

    // Hardware & HITL execution buttons
    document.getElementById("btn-preflight-safety").onclick = () => this._openHardwareModal(false);
    document.getElementById("btn-hitl-dryrun").onclick = () => this._openHardwareModal(true, "hitl");
    document.getElementById("btn-execute-robot").onclick = () => this._openHardwareModal(true, "auto");
    document.getElementById("btn-hardware-stop").onclick = () => this._emergencyHold();
    document.getElementById("btn-close-hardware-modal").onclick = () => this._closeHardwareModal();
    document.getElementById("btn-hw-modal-cancel").onclick = () => this._closeHardwareModal();
    document.getElementById("btn-hw-confirm-exec").onclick = () => this._executeOnHardware();

    const hwRadios = document.getElementsByName("hw-exec-mode");
    for (const r of hwRadios) {
      r.addEventListener("change", () => this._updateExecutionButtonState());
    }
  }

  async _setArm(arm) {
    if (this.arm === arm) return;
    this.arm = arm;
    document.getElementById("btn-arm-left").classList.toggle("active", arm === "left");
    document.getElementById("btn-arm-right").classList.toggle("active", arm === "right");
    document.getElementById("active-arm-indicator").textContent = arm.toUpperCase();
    const presets = getPresetsForArm(arm);
    this.qStart = [...presets.stow];
    this.qGoal = [...presets.zombie_reach];
    this._syncUIFromState();
    this._updateArmPosesInView();
    this._updateGripperUI();
    if (this.view && this.view.setGripper) {
      this.view.setGripper(arm, this.gripperState[arm] ?? 1.0);
    }
    this._checkCollisionAndFK();

    // Load keyframes for the newly selected arm
    await this._loadKeyframes();
  }

  _applySelectedPreset(presetKey) {
    if (!presetKey) return;
    const presets = getPresetsForArm(this.arm);
    const p = presets[presetKey];
    if (p) {
      if (this.targetMode === "goal") {
        this.qGoal = [...p];
      } else {
        this.qStart = [...p];
      }
      this._syncUIFromState();
      this._updateArmPosesInView();
      this._checkCollisionAndFK();
    }
  }

  _syncPresetDropdown() {
    const selector = document.getElementById("preset-select");
    if (!selector) return;
    const currentQ = this.targetMode === "goal" ? this.qGoal : this.qStart;
    const presets = getPresetsForArm(this.arm);

    let matchedKey = "";
    for (const [key, angles] of Object.entries(presets)) {
      const match = angles.every((val, idx) => Math.abs(val - currentQ[idx]) < 0.25);
      if (match) {
        matchedKey = key;
        break;
      }
    }
    selector.value = matchedKey;
  }

  _setTargetMode(mode) {
    this.targetMode = mode;
    document.getElementById("btn-mode-goal").classList.toggle("active", mode === "goal");
    document.getElementById("btn-mode-start").classList.toggle("active", mode === "start");

    const panelBody = document.querySelector(".panel-body");
    if (panelBody) {
      panelBody.classList.toggle("editing-goal", mode === "goal");
      panelBody.classList.toggle("editing-start", mode === "start");
    }

    const badge = document.getElementById("active-target-badge");
    const badgeText = document.getElementById("active-target-text");
    if (badge && badgeText) {
      badge.classList.toggle("badge-goal", mode === "goal");
      badge.classList.toggle("badge-start", mode === "start");
      badgeText.textContent = mode === "goal"
        ? "Editing: GOAL TARGET (Ghost Arm)"
        : "Editing: START POSE (Solid Arm)";
    }

    this._syncUIFromState();
    this._checkCollisionAndFK();
    if (this.view && this.view.setActiveEditArm) {
      this.view.setActiveEditArm(mode);
    }
  }

  _syncUIFromState() {
    const currentQ = this.targetMode === "goal" ? this.qGoal : this.qStart;
    for (let i = 0; i < 7; i++) {
      const slider = document.getElementById(`slider-j${i}`);
      const readout = document.getElementById(`val-j${i}`);
      if (slider) slider.value = currentQ[i];
      if (readout) readout.textContent = `${currentQ[i].toFixed(1)}°`;
    }
    this._syncPresetDropdown();
    this._updateArmPosesInView();
    if (this.view && this.view.getTcpWorldPosition) {
      const tcpPos = this.view.getTcpWorldPosition(this.arm, this.targetMode === "goal");
      if (tcpPos) {
        const ex = document.getElementById("ee-x");
        const ey = document.getElementById("ee-y");
        const ez = document.getElementById("ee-z");
        if (ex && ey && ez) {
          ex.value = tcpPos.x.toFixed(3);
          ey.value = tcpPos.y.toFixed(3);
          ez.value = tcpPos.z.toFixed(3);
        }
      }
    }
  }

  _updateArmPosesInView() {
    if (!this.view || !this.view.ready) return;
    const solidAngles = {};
    const ghostAngles = {};

    for (let i = 0; i < 7; i++) {
      solidAngles[this._getJointName(i)] = (this.qStart[i] * Math.PI) / 180.0;
      ghostAngles[this._getJointName(i)] = (this.qGoal[i] * Math.PI) / 180.0;
    }

    this.view.setSolidJoints(solidAngles);
    this.view.setGhostJoints(ghostAngles);
  }

  _scheduleCollisionCheck() {
    clearTimeout(this.collisionDebounceTimer);
    this.collisionDebounceTimer = setTimeout(() => {
      this._checkCollisionAndFK();
    }, 80);
  }

  async _checkCollisionAndFK() {
    const currentQ = this.targetMode === "goal" ? this.qGoal : this.qStart;
    try {
      // 1. Collision check
      const colResp = await fetch("/api/collision_check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ arm: this.arm, joints: currentQ }),
      });
      const colData = await colResp.json();

      const badge = document.getElementById("clearance-badge");
      const statusText = document.getElementById("clearance-status-text");
      const distText = document.getElementById("clearance-distance-text");

      badge.classList.remove("safe", "warn", "danger");
      if (colData.collision) {
        badge.classList.add("danger");
        statusText.textContent = "Collision Detected";
        distText.textContent = `${colData.min_clearance_mm.toFixed(1)} mm`;
        if (this.view && this.view.setCollisionState) this.view.setCollisionState(true);
      } else if (colData.min_clearance_mm < 30.0) {
        badge.classList.add("warn");
        statusText.textContent = "Close Clearance";
        distText.textContent = `${colData.min_clearance_mm.toFixed(1)} mm`;
        if (this.view && this.view.setCollisionState) this.view.setCollisionState(false);
      } else {
        badge.classList.add("safe");
        statusText.textContent = "Safe (MuJoCo)";
        distText.textContent = `${colData.min_clearance_mm.toFixed(1)} mm`;
        if (this.view && this.view.setCollisionState) this.view.setCollisionState(false);
      }

      // 2. Forward Kinematics for End-Effector XYZ readout from 3D visualizer
      if (this.view && this.view.getTcpWorldPosition) {
        const tcpPos = this.view.getTcpWorldPosition(this.arm, this.targetMode === "goal");
        if (tcpPos) {
          document.getElementById("ee-x").value = tcpPos.x.toFixed(3);
          document.getElementById("ee-y").value = tcpPos.y.toFixed(3);
          document.getElementById("ee-z").value = tcpPos.z.toFixed(3);
        }
      } else {
        const fkResp = await fetch("/api/fk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: jsonReq({ arm: this.arm, joints: currentQ }),
        });
        const fkData = await fkResp.json();
        if (fkData.position) {
          const [x, y, z] = fkData.position;
          document.getElementById("ee-x").value = x.toFixed(3);
          document.getElementById("ee-y").value = y.toFixed(3);
          document.getElementById("ee-z").value = z.toFixed(3);
        }
      }
    } catch (err) {
      console.warn("Collision/FK check error:", err);
    }
  }

  async _solveIK() {
    const x = parseFloat(document.getElementById("ee-x").value);
    const y = parseFloat(document.getElementById("ee-y").value);
    const z = parseFloat(document.getElementById("ee-z").value);

    if (isNaN(x) || isNaN(y) || isNaN(z)) {
      alert("Please enter valid Cartesian X, Y, Z numbers.");
      return;
    }

    const btn = document.getElementById("btn-solve-ik");
    btn.textContent = "Solving SSIK...";
    btn.disabled = true;

    try {
      const resp = await fetch("/api/ik", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          arm: this.arm,
          position: [x, y, z],
          q_seed: this.targetMode === "goal" ? this.qGoal : this.qStart,
        }),
      });
      const data = await resp.json();
      if (data.success && data.joints_deg) {
        if (this.targetMode === "goal") {
          this.qGoal = data.joints_deg.map((v) => round1(v));
        } else {
          this.qStart = data.joints_deg.map((v) => round1(v));
        }
        this._syncUIFromState();
        this._checkCollisionAndFK();
      } else {
        alert(data.error || "No valid IK solution found for this position.");
      }
    } catch (err) {
      alert(`IK Error: ${err}`);
    } finally {
      btn.textContent = "Solve IK (SSIK Analytical)";
      btn.disabled = false;
    }
  }

  async _planSingleTrajectory() {
    const btn = document.getElementById("btn-plan-motion");
    const statusInfo = document.getElementById("plan-status-info");
    const perfInfo = document.getElementById("plan-perf-info");

    const activeBackend = document.getElementById("backend-selector")?.value || "planner";
    btn.disabled = true;
    btn.innerHTML = `<span>Planning (${activeBackend.toUpperCase()})...</span>`;
    statusInfo.textContent = `Solving trajectory with ${activeBackend.toUpperCase()}...`;

    try {
      const resp = await fetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          arm: this.arm,
          q_start: this.qStart,
          q_goal: this.qGoal,
          v_max: 1.0,
          a_max: 2.0,
          timeout: 6.0,
        }),
      });
      const data = await resp.json();

      if (!data.success) {
        statusInfo.textContent = `Plan Failed: ${data.error}`;
        perfInfo.textContent = "Try adjusting start or goal pose.";
        alert(`Motion planning failed:\n${data.error}`);
        return;
      }

      this.trajectory = data;
      this.currentTime = 0.0;
      this.isPlaying = true;

      // Update UI readouts
      statusInfo.textContent = `Trajectory Ready (${data.duration.toFixed(2)}s)`;
      perfInfo.textContent = `Planned in ${data.planning_time_ms.toFixed(0)} ms | ${data.waypoints} waypoints | Safe (${data.min_clearance_mm.toFixed(1)} mm)`;

      const scrubber = document.getElementById("timeline-scrubber");
      scrubber.min = 0;
      scrubber.max = data.duration;
      scrubber.value = 0;

      // Render 3D trajectory ribbon in scene aligned exactly to robot TCP
      if (this.view && this.view.setTrajectoryPath) {
        this.view.setTrajectoryPath(this.arm, data.positions_rad);
      }
      this._updatePlayButton();
    } catch (err) {
      statusInfo.textContent = `Planning error: ${err}`;
      alert(`Planning error: ${err}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span>Plan Trajectory</span>`;
    }
  }

  _togglePlayPause() {
    if (!this.trajectory) return;
    this.isPlaying = !this.isPlaying;
    this._updatePlayButton();
  }

  _updatePlayButton() {
    const btn = document.getElementById("btn-play-pause");
    btn.textContent = this.isPlaying ? "Pause" : "Play";
    btn.style.borderColor = this.isPlaying ? "var(--accent-primary)" : "var(--border-color)";
  }

  _playbackLoop(timestamp) {
    if (this.animLastTime === 0) this.animLastTime = timestamp;
    const delta = (timestamp - this.animLastTime) / 1000.0;
    this.animLastTime = timestamp;

    if (this.isPlaying && this.trajectory && this.trajectory.duration > 0) {
      this.currentTime += delta * this.playbackSpeed;
      if (this.currentTime > this.trajectory.duration) {
        if (this.isLooping) {
          this.currentTime = 0.0;
        } else {
          this.currentTime = this.trajectory.duration;
          this.isPlaying = false;
          this._updatePlayButton();
        }
      }
      const scrubber = document.getElementById("timeline-scrubber");
      if (scrubber) scrubber.value = this.currentTime;
      this._applyTrajectoryAtTime(this.currentTime);
    }

    if (this.view && this.view.ready) {
      this.view.render();
    }

    requestAnimationFrame((t) => this._playbackLoop(t));
  }

  _applyTrajectoryAtTime(t) {
    if (!this.trajectory) return;
    const traj = this.trajectory;
    const times = traj.times;
    const positions = traj.positions_deg;

    // Linear interpolation between waypoints
    let idx = 0;
    while (idx < times.length - 1 && times[idx + 1] < t) {
      idx++;
    }

    let qInterp = positions[idx];
    if (idx < times.length - 1) {
      const dt = times[idx + 1] - times[idx];
      const alpha = dt > 1e-6 ? (t - times[idx]) / dt : 0.0;
      qInterp = positions[idx].map((p0, i) => p0 + alpha * (positions[idx + 1][i] - p0));
    }

    // Update solid arm pose in 3D view using the arm the trajectory was planned for
    const trajArm = (this.trajectory && this.trajectory.arm) || this.arm;
    const solidAngles = {};
    for (let i = 0; i < 7; i++) {
      solidAngles[`openarm_${trajArm}_joint${i + 1}`] = (qInterp[i] * Math.PI) / 180.0;
    }
    if (this.view && this.view.setSolidJoints) this.view.setSolidJoints(solidAngles);

    // Update gripper pose at timestamp t based on trajectory gripper events
    if (this.trajectory && this.trajectory.gripperEvents && this.trajectory.gripperEvents.length > 0) {
      let activeEv = this.trajectory.gripperEvents[0];
      for (const ev of this.trajectory.gripperEvents) {
        if (t >= ev.time - 0.05) {
          activeEv = ev;
        }
      }
      this._setGripper(activeEv.fraction, false, trajArm);
    } else if (this.keyframes && this.keyframes.length > 0) {
      let gripFraction = this.gripperState[trajArm] ?? 1.0;
      for (const kf of this.keyframes) {
        if (kf.arm === trajArm && kf.time_s <= t + 1e-4 && kf.gripper) {
          gripFraction = kf.gripper === "close" ? 0.0 : 1.0;
        }
      }
      this._setGripper(gripFraction, false, trajArm);
    }

    // Update readout
    document.getElementById("timeline-readout").textContent = `${t.toFixed(2)}s / ${traj.duration.toFixed(2)}s`;
  }

  /* Gripper Handlers */
  _fetchGripperStatus() {
    fetch(`/api/gripper?arm=${this.arm}`)
      .then((r) => r.json())
      .then((data) => {
        if (data && typeof data.open_fraction === "number") {
          this.gripperState[this.arm] = data.open_fraction;
          this._updateGripperUI();
          if (this.view && this.view.setGripper) {
            this.view.setGripper(this.arm, data.open_fraction);
          }
        }
      })
      .catch(() => {});
  }

  _setGripper(fraction, sendToBackend = false, arm = this.arm) {
    fraction = Math.max(0, Math.min(1, fraction));
    this.gripperState[arm] = fraction;
    if (arm === this.arm) {
      this._updateGripperUI();
    }
    if (this.view && this.view.setGripper) {
      this.view.setGripper(arm, fraction);
    }
    if (sendToBackend) {
      fetch("/api/gripper", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ arm: arm, action: fraction }),
      }).catch((e) => console.warn("Gripper command failed:", e));
    }
  }

  _toggleGripper() {
    const current = this.gripperState[this.arm] ?? 1.0;
    const target = current >= 0.5 ? 0.0 : 1.0;
    this._setGripper(target, true);
  }

  _updateGripperUI() {
    const fraction = this.gripperState[this.arm] ?? 1.0;
    const slider = document.getElementById("gripper-slider");
    if (slider) slider.value = Math.round(fraction * 100);

    const badge = document.getElementById("gripper-status-badge");
    if (badge) {
      const pct = Math.round(fraction * 100);
      if (fraction >= 0.95) {
        badge.textContent = `OPEN (${pct}%)`;
        badge.style.color = "var(--accent-cyan)";
      } else if (fraction <= 0.05) {
        badge.textContent = `CLOSED (${pct}%)`;
        badge.style.color = "#f87171";
      } else {
        badge.textContent = `GRIP (${pct}%)`;
        badge.style.color = "#fbbf24";
      }
    }

    const toggleBtn = document.getElementById("btn-gripper-toggle");
    if (toggleBtn) {
      toggleBtn.textContent = fraction >= 0.5 ? "Close" : "Open";
    }
  }

  /* Keyframe Choreographer */
  async _loadKeyframes() {
    try {
      const resp = await fetch(`/api/keyframes?arm=${this.arm}`);
      this.keyframes = await resp.json();
      this._renderKeyframeList();
    } catch (err) {
      console.warn("Failed to load keyframes:", err);
    }
  }

  async _saveKeyframes() {
    try {
      await fetch("/api/keyframes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ arm: this.arm, keyframes: this.keyframes }),
      });
    } catch (err) {
      console.warn("Failed to save keyframes:", err);
    }
  }

  _renderKeyframeList() {
    const list = document.getElementById("keyframe-list");
    list.innerHTML = "";

    this.keyframes.forEach((kf, index) => {
      const card = document.createElement("div");
      card.className = "keyframe-card";
      const jStr = kf.joints_deg.map((v) => Math.round(v)).join(", ");
      const isClosed = kf.gripper === "close";
      const gripBadgeText = isClosed ? "✊ GRIP CLOSED" : "🖐️ GRIP OPEN";
      const gripColor = isClosed ? "#f87171" : "#38bdf8";

      card.innerHTML = `
        <div class="kf-header">
          <span class="kf-title">${index + 1}. ${escapeHtml(kf.name)}</span>
          <span class="kf-time">${kf.time_s.toFixed(1)}s</span>
        </div>
        <div class="kf-details">
          <span>${kf.arm.toUpperCase()} ARM</span>
          <span class="badge" style="cursor:pointer; font-size:10px; color:${gripColor}; border-color:${gripColor}44;" data-act="toggle-grip" title="Click to toggle gripper action at this keyframe">${gripBadgeText}</span>
          <span style="font-family:var(--font-mono); font-size:10px;">[${jStr}]°</span>
        </div>
        <div style="display:flex; justify-content:flex-end; gap:6px; margin-top:4px;">
          <button class="icon-btn" style="padding:2px 6px; font-size:10px;" data-act="load">Load Target</button>
          <button class="icon-btn" style="padding:2px 6px; font-size:10px;" data-act="del">✕</button>
        </div>
      `;

      const gripBtn = card.querySelector('[data-act="toggle-grip"]');
      if (gripBtn) {
        gripBtn.onclick = (e) => {
          e.stopPropagation();
          kf.gripper = kf.gripper === "close" ? "open" : "close";
          const newFraction = kf.gripper === "close" ? 0.0 : 1.0;
          this._setGripper(newFraction, true, kf.arm);
          this._renderKeyframeList();
          this._saveKeyframes();
        };
      }

      card.querySelector('[data-act="load"]').onclick = (e) => {
        e.stopPropagation();
        this._setArm(kf.arm);
        this.qGoal = [...kf.joints_deg];
        if (kf.gripper) {
          const frac = kf.gripper === "close" ? 0.0 : 1.0;
          this._setGripper(frac, true, kf.arm);
        }
        this._setTargetMode("goal");
        this._syncUIFromState();
        this._checkCollisionAndFK();
      };

      card.querySelector('[data-act="del"]').onclick = (e) => {
        e.stopPropagation();
        this.keyframes.splice(index, 1);
        this._renderKeyframeList();
        this._saveKeyframes();
      };

      list.appendChild(card);
    });
  }

  _addKeyframe() {
    const nameInput = document.getElementById("kf-name-input");
    const timeInput = document.getElementById("kf-time-input");
    const gripSelect = document.getElementById("kf-gripper-action");

    const name = nameInput.value.trim() || `Pose ${this.keyframes.length + 1}`;
    const time_s = parseFloat(timeInput.value) || (this.keyframes.length ? this.keyframes[this.keyframes.length - 1].time_s + 4.0 : 0.0);
    let gripAction = gripSelect ? gripSelect.value : "keep";
    if (gripAction === "keep") {
      gripAction = (this.gripperState[this.arm] ?? 1.0) >= 0.5 ? "open" : "close";
    }

    const kf = {
      name,
      arm: this.arm,
      time_s,
      joints_deg: [...this.qGoal],
      gripper: gripAction,
    };

    this.keyframes.push(kf);
    this.keyframes.sort((a, b) => a.time_s - b.time_s);
    this._renderKeyframeList();
    this._saveKeyframes();

    // Advance time suggestion for next pose
    timeInput.value = (time_s + 4.0).toFixed(1);
    nameInput.value = "";
  }

  async _planShowSequence() {
    if (this.keyframes.length < 2) {
      alert("Please add at least 2 keyframes to plan a sequence.");
      return;
    }

    const btn = document.getElementById("btn-plan-show-sequence");
    btn.disabled = true;
    btn.innerHTML = `<span>Planning Full Sequence...</span>`;

    const allTimes = [];
    const allPositions = [];
    const allEePositions = [];
    const gripperEvents = [];
    let cumulativeDuration = 0.0;

    // Record initial keyframe gripper event at t = 0.0
    const kf0Frac = this.keyframes[0].gripper === "close" ? 0.0 : 1.0;
    gripperEvents.push({
      time: 0.0,
      fraction: kf0Frac,
      action: kf0Frac === 0.0 ? "close" : "open",
      arm: this.keyframes[0].arm,
    });

    try {
      for (let i = 0; i < this.keyframes.length - 1; i++) {
        const kfA = this.keyframes[i];
        const kfB = this.keyframes[i + 1];

        const resp = await fetch("/api/plan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: jsonReq({
            arm: kfA.arm,
            q_start: kfA.joints_deg,
            q_goal: kfB.joints_deg,
            v_max: 1.0,
            a_max: 2.0,
            timeout: 6.0,
          }),
        });
        const seg = await resp.json();
        if (!seg.success) {
          throw new Error(`Failed planning segment ${i + 1} (${kfA.name} -> ${kfB.name}): ${seg.error}`);
        }

        // Concatenate segment
        for (let j = 0; j < seg.times.length; j++) {
          if (allTimes.length > 0 && j === 0) continue; // Skip duplicate waypoint
          allTimes.push(cumulativeDuration + seg.times[j]);
          allPositions.push(seg.positions_deg[j]);
          allEePositions.push(seg.ee_positions[j]);
        }
        cumulativeDuration += seg.duration;

        // Record destination keyframe gripper event at cumulativeDuration
        const kfBFrac = kfB.gripper === "close" ? 0.0 : 1.0;
        gripperEvents.push({
          time: cumulativeDuration,
          fraction: kfBFrac,
          action: kfBFrac === 0.0 ? "close" : "open",
          arm: kfB.arm,
        });
      }

      const rads = allPositions.map((q) => q.map((v) => (v * Math.PI) / 180.0));
      this.trajectory = {
        success: true,
        arm: this.arm,
        duration: cumulativeDuration,
        times: allTimes,
        positions_deg: allPositions,
        positions_rad: rads,
        ee_positions: allEePositions,
        gripperEvents: gripperEvents,
        waypoints: allPositions.length,
        min_clearance_mm: 35.0,
        planning_time_ms: 0,
      };

      this.currentTime = 0.0;
      this.isPlaying = true;
      this._updatePlayButton();

      const scrubber = document.getElementById("timeline-scrubber");
      scrubber.min = 0;
      scrubber.max = cumulativeDuration;
      scrubber.value = 0;

      document.getElementById("plan-status-info").textContent = `Full Sequence Planned (${cumulativeDuration.toFixed(2)}s)`;
      document.getElementById("plan-perf-info").textContent = `${this.keyframes.length} keyframes | ${allPositions.length} waypoints`;

      if (this.view && this.view.setTrajectoryPath) {
        const rads = allPositions.map((q) => q.map((v) => (v * Math.PI) / 180.0));
        this.view.setTrajectoryPath(this.arm, rads);
      }

      // Apply initial pose and gripper at t=0
      this._applyTrajectoryAtTime(0.0);
    } catch (err) {
      alert(`Sequence planning error:\n${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span>Plan Full Sequence</span>`;
    }
  }

  async _openYamlModal() {
    const modal = document.getElementById("yaml-modal");
    const textarea = document.getElementById("yaml-output-text");

    try {
      const resp = await fetch("/api/export_show", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ keyframes: this.keyframes }),
      });
      const yaml = await resp.text();
      textarea.value = yaml;
      modal.style.display = "flex";
    } catch (err) {
      alert(`Export error: ${err}`);
    }
  }

  _closeYamlModal() {
    document.getElementById("yaml-modal").style.display = "none";
  }

  _copyYamlToClipboard() {
    const textarea = document.getElementById("yaml-output-text");
    navigator.clipboard.writeText(textarea.value).then(() => {
      const btn = document.getElementById("btn-copy-yaml");
      btn.textContent = "Copied to Clipboard";
      setTimeout(() => (btn.textContent = "Copy to Clipboard"), 2000);
    });
  }

  _downloadYaml() {
    const textarea = document.getElementById("yaml-output-text");
    const blob = new Blob([textarea.value], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "halloween_arm_routine.yaml";
    a.click();
    URL.revokeObjectURL(url);
  }

  /* Choreo Library, Import, and Persistence */
  async _openImportModal() {
    const modal = document.getElementById("import-modal");
    if (!modal) return;
    modal.style.display = "flex";

    // Populate library select
    const sel = document.getElementById("import-library-select");
    sel.innerHTML = `<option value="">Loading choreos...</option>`;

    try {
      const resp = await fetch("/api/choreo/library");
      const data = await resp.json();
      if (data.success && Array.isArray(data.choreos)) {
        sel.innerHTML = "";
        const builtins = data.choreos.filter((c) => c.type === "builtin");
        const users = data.choreos.filter((c) => c.type === "user");

        if (users.length > 0) {
          const grp = document.createElement("optgroup");
          grp.label = "User Saved Routines";
          users.forEach((c) => {
            const opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = `★ ${c.name}`;
            grp.appendChild(opt);
          });
          sel.appendChild(grp);
        }

        if (builtins.length > 0) {
          const grp = document.createElement("optgroup");
          grp.label = "Built-in Shows (software/choreo/shows/)";
          builtins.forEach((c) => {
            const opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = c.name;
            grp.appendChild(opt);
          });
          sel.appendChild(grp);
        }
      } else {
        sel.innerHTML = `<option value="">No choreos found</option>`;
      }
    } catch (e) {
      sel.innerHTML = `<option value="">Failed to load library</option>`;
      console.warn("Could not fetch choreo library:", e);
    }
  }

  _closeImportModal() {
    const modal = document.getElementById("import-modal");
    if (modal) modal.style.display = "none";
  }

  async _importFromLibrary() {
    const sel = document.getElementById("import-library-select");
    const id = sel ? sel.value : "";
    if (!id) {
      alert("Please select a choreography to import.");
      return;
    }

    try {
      const resp = await fetch("/api/choreo/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ id: id, arm: this.arm }),
      });
      const data = await resp.json();
      if (!data.success) {
        throw new Error(data.error || "Import failed");
      }
      this._applyImportedKeyframes(data.keyframes, `Library show (${id})`);
      this._closeImportModal();
    } catch (err) {
      alert(`Import error: ${err.message}`);
    }
  }

  async _importFromFile(file) {
    if (!file) return;
    try {
      const reader = new FileReader();
      reader.onload = async (e) => {
        const content = e.target.result;
        try {
          const resp = await fetch("/api/choreo/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: jsonReq({ content: content, arm: this.arm }),
          });
          const data = await resp.json();
          if (!data.success) {
            throw new Error(data.error || "Import failed");
          }
          this._applyImportedKeyframes(data.keyframes, file.name);
          this._closeImportModal();
        } catch (err) {
          alert(`Import error: ${err.message}`);
        }
      };
      reader.readAsText(file);
    } catch (e) {
      alert(`Failed to read file: ${e.message}`);
    }
  }

  async _importFromText() {
    const textarea = document.getElementById("import-paste-text");
    const content = textarea ? textarea.value.trim() : "";
    if (!content) {
      alert("Please paste YAML or JSON content first.");
      return;
    }

    try {
      const resp = await fetch("/api/choreo/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({ content: content, arm: this.arm }),
      });
      const data = await resp.json();
      if (!data.success) {
        throw new Error(data.error || "Import failed");
      }
      this._applyImportedKeyframes(data.keyframes, "Pasted Content");
      this._closeImportModal();
      if (textarea) textarea.value = "";
    } catch (err) {
      alert(`Import error: ${err.message}`);
    }
  }

  _applyImportedKeyframes(keyframes, sourceName = "") {
    if (!Array.isArray(keyframes) || keyframes.length === 0) {
      alert("No arm keyframes found in this choreography.");
      return;
    }

    this.keyframes = keyframes;
    this._renderKeyframeList();
    this._saveKeyframes();

    // If keyframes has at least one pose, load the first pose as the current goal
    if (this.keyframes.length > 0) {
      const first = this.keyframes[0];
      this._setArm(first.arm);
      this.qGoal = [...first.joints_deg];
      this._syncUIFromState();
      this._updateArmPosesInView();
      this._checkCollisionAndFK();
    }

    const toastMsg = `Imported ${keyframes.length} keyframes from ${sourceName}`;
    const planInfo = document.getElementById("plan-status-info");
    if (planInfo) planInfo.textContent = toastMsg;
  }

  _openSaveModal() {
    const modal = document.getElementById("save-modal");
    if (!modal) return;
    const nameInput = document.getElementById("save-choreo-name");
    if (nameInput) {
      nameInput.value = `choreo_${this.arm}_${new Date().toISOString().slice(5, 10).replace("-", "_")}`;
    }
    const statusMsg = document.getElementById("save-status-msg");
    if (statusMsg) statusMsg.style.display = "none";
    modal.style.display = "flex";
  }

  _closeSaveModal() {
    const modal = document.getElementById("save-modal");
    if (modal) modal.style.display = "none";
  }

  async _saveNamedChoreo() {
    const nameInput = document.getElementById("save-choreo-name");
    const name = nameInput ? nameInput.value.trim() : "";
    if (!name) {
      alert("Please provide a name for the choreography.");
      return;
    }

    let format = "yaml";
    const formatRadio = document.querySelector('input[name="save-choreo-format"]:checked');
    if (formatRadio) format = formatRadio.value;

    const btn = document.getElementById("btn-save-confirm");
    if (btn) btn.disabled = true;

    try {
      const resp = await fetch("/api/choreo/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          name: name,
          format: format,
          keyframes: this.keyframes,
        }),
      });
      const data = await resp.json();
      if (!data.success) {
        throw new Error(data.error || "Save failed");
      }

      const statusMsg = document.getElementById("save-status-msg");
      if (statusMsg) {
        statusMsg.style.display = "block";
        statusMsg.style.background = "rgba(34, 197, 94, 0.15)";
        statusMsg.style.color = "#86efac";
        statusMsg.style.border = "1px solid rgba(34, 197, 94, 0.3)";
        statusMsg.textContent = `Saved successfully as ${data.filename}!`;
      }

      setTimeout(() => {
        this._closeSaveModal();
        if (btn) btn.disabled = false;
      }, 1000);
    } catch (err) {
      alert(`Save error: ${err.message}`);
      if (btn) btn.disabled = false;
    }
  }

  async _openBenchmarkModal() {
    const modal = document.getElementById("benchmark-modal");
    if (!modal) return;
    modal.style.display = "flex";
    if (!this._hasBenchmarkData) {
      try {
        const resp = await fetch("/api/benchmark");
        const data = await resp.json();
        this._renderBenchmarkResults(data);
      } catch (e) {
        console.warn("Could not fetch initial benchmark:", e);
      }
    }
  }

  _closeBenchmarkModal() {
    const modal = document.getElementById("benchmark-modal");
    if (modal) modal.style.display = "none";
  }

  async _runBenchmark() {
    const btn = document.getElementById("btn-run-benchmark-now");
    const status = document.getElementById("benchmark-running-status");
    const trialsSelect = document.getElementById("benchmark-trials-select");
    const trials = parseInt(trialsSelect?.value || "2", 10);

    btn.disabled = true;
    btn.textContent = "Running...";
    if (status) status.style.display = "inline";

    try {
      const resp = await fetch("/api/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          arm: this.arm,
          trials: trials,
        }),
      });
      const data = await resp.json();
      this._renderBenchmarkResults(data);
    } catch (err) {
      alert(`Benchmark error: ${err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Run Benchmark";
      if (status) status.style.display = "none";
    }
  }

  _renderBenchmarkResults(data) {
    if (!data || !data.overall) return;
    this._hasBenchmarkData = true;

    const cb = data.overall.cbirrt;
    const cu = data.overall.curobo;
    const comp = data.comparison || {};

    if (cb) {
      const el = document.getElementById("bench-stat-cbirrt");
      if (el) el.textContent = `${cb.latency_mean_ms.toFixed(1)} ms`;
    }
    if (cu) {
      const el = document.getElementById("bench-stat-curobo");
      if (el) el.textContent = `${cu.latency_mean_ms.toFixed(1)} ms`;
    }
    if (comp.speedup_factor) {
      const elSpeed = document.getElementById("bench-stat-speedup");
      if (elSpeed) elSpeed.textContent = `${comp.speedup_factor.toFixed(1)}x`;
      const elFaster = document.getElementById("bench-stat-faster");
      if (elFaster) elFaster.textContent = `${comp.summary_text || "Faster"}`;
    }

    const tbody = document.getElementById("benchmark-table-body");
    if (!tbody) return;
    tbody.innerHTML = "";

    // Overall summary row
    const trSummary = document.createElement("tr");
    trSummary.style.fontWeight = "bold";
    trSummary.style.background = "rgba(2, 132, 199, 0.12)";
    const speedStr = comp.speedup_factor ? `${comp.speedup_factor.toFixed(1)}x faster` : "--";
    trSummary.innerHTML = `
      <td><strong>Overall Mean Latency</strong></td>
      <td>${cb ? `${cb.latency_mean_ms.toFixed(1)} ms` : "N/A"}</td>
      <td>${cu ? `${cu.latency_mean_ms.toFixed(1)} ms` : "N/A"}</td>
      <td style="color:var(--accent-cyan);">${speedStr}</td>
    `;
    tbody.appendChild(trSummary);

    // Metrics rows
    const addMetricRow = (label, cbVal, cuVal, note = "") => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(label)}</td>
        <td>${cbVal}</td>
        <td>${cuVal}</td>
        <td style="color:var(--text-muted); font-size:11px;">${note}</td>
      `;
      tbody.appendChild(tr);
    };

    if (cb && cu) {
      addMetricRow("Success Rate", `${cb.success_rate_pct.toFixed(0)}% (${cb.total_successes}/${cb.total_attempts})`, `${cu.success_rate_pct.toFixed(0)}% (${cu.total_successes}/${cu.total_attempts})`, "Both verified");
      addMetricRow("Trajectory Duration", `${cb.duration_mean_s.toFixed(2)} s`, `${cu.duration_mean_s.toFixed(2)} s`, "Smooth TOPP-RA");
      addMetricRow("Trajectory Waypoints", `${cb.waypoints_mean.toFixed(0)} pts`, `${cu.waypoints_mean.toFixed(0)} pts`, "Dense path");
      addMetricRow("Min Collision Clearance", `${cb.min_clearance_mm.toFixed(1)} mm`, `${cu.min_clearance_mm.toFixed(1)} mm`, "MuJoCo verified");
    }

    // Individual motion scenarios
    const cbMotions = data.motions?.cbirrt || [];
    const cuMotions = data.motions?.curobo || [];
    if (cbMotions.length > 0) {
      const trHeader = document.createElement("tr");
      trHeader.style.background = "var(--bg-card)";
      trHeader.innerHTML = `<td colspan="4" style="font-weight:600; color:var(--text-muted); font-size:11px; padding:6px 10px;">INDIVIDUAL MOTION SCENARIOS</td>`;
      tbody.appendChild(trHeader);

      for (let i = 0; i < cbMotions.length; i++) {
        const mCb = cbMotions[i];
        const mCu = cuMotions[i];
        const mSpeed = (mCb && mCu && mCu.latency_mean_ms > 0) ? (mCb.latency_mean_ms / mCu.latency_mean_ms).toFixed(1) + "x" : "--";
        const trM = document.createElement("tr");
        trM.innerHTML = `
          <td>${escapeHtml(mCb.motion)}</td>
          <td>${mCb.latency_mean_ms.toFixed(1)} ms</td>
          <td>${mCu ? `${mCu.latency_mean_ms.toFixed(1)} ms` : "N/A"}</td>
          <td style="color:var(--accent-cyan);">${mSpeed}</td>
        `;
        tbody.appendChild(trM);
      }
    }
  }

  async _pollHardwareStatus() {
    let nextPollMs = 800;
    try {
      const resp = await fetch("/api/hardware/status");
      const data = await resp.json();
      const badgeText = document.getElementById("hw-mode-text");
      const envText = document.getElementById("hw-modal-env-status");

      if (badgeText && data.environment) {
        const rec = data.environment.recommended_mode || "hitl";
        if (rec === "ros2") {
          badgeText.textContent = "ROS 2 Connected (Thor)";
          badgeText.style.color = "#4ade80";
        } else if (rec === "docker") {
          badgeText.textContent = "Docker Container Active";
          badgeText.style.color = "#38bdf8";
        } else if (rec === "remote_ssh") {
          badgeText.textContent = `Remote Thor Connected (${data.environment.remote_thor_host || "10.42.2.10"})`;
          badgeText.style.color = "#4ade80";
        } else {
          badgeText.textContent = "HITL Simulation Available";
          badgeText.style.color = "#38bdf8";
        }
      }

      if (envText && data.environment) {
        envText.textContent = `Env: Remote Thor=${data.environment.remote_thor_available} (${data.environment.remote_thor_host}), Docker=${data.environment.docker_running}, Recommended=${data.environment.recommended_mode.toUpperCase()}`;
      }

      const progressCont = document.getElementById("hw-live-progress-container");
      const progressBar = document.getElementById("hw-progress-bar");
      const progressPct = document.getElementById("hw-progress-pct");
      const progressLabel = document.getElementById("hw-progress-label");
      const btnExec = document.getElementById("btn-hw-confirm-exec");

      // Update Live RC Controller Authority & Deadman Safety Guard card
      if (data.robot_safety) {
        this._lastRobotSafety = data.robot_safety;
        const saf = data.robot_safety;
        const dot = document.getElementById("hw-safety-indicator-dot");
        const badge = document.getElementById("hw-safety-overall-badge");
        const elDeadman = document.getElementById("hw-saf-deadman");
        const elCh5 = document.getElementById("hw-saf-ch5");
        const elPower = document.getElementById("hw-saf-power");
        const elEstop = document.getElementById("hw-saf-estop");

        if (saf.ok) {
          if (elDeadman) {
            elDeadman.textContent = saf.deadman ? "HELD (Active)" : "RELEASED";
            elDeadman.style.color = saf.deadman ? "#4ade80" : "#f87171";
          }
          if (elCh5) {
            elCh5.textContent = saf.ch5 || "--";
            elCh5.style.color = (saf.ch5 === "AUTO" || saf.ch5 === "MANUAL") ? "#4ade80" : "#f59e0b";
          }
          if (elPower) {
            elPower.textContent = saf.motion_power ? "ENERGIZED" : "OFF";
            elPower.style.color = saf.motion_power ? "#4ade80" : "#f87171";
          }
          if (elEstop) {
            elEstop.textContent = saf.estop ? "ENGAGED" : "CLEAR";
            elEstop.style.color = saf.estop ? "#f87171" : "#4ade80";
          }

          const isSafeToMove = saf.deadman && !saf.estop && saf.motion_power && (saf.ch5 === "AUTO" || saf.ch5 === "MANUAL");
          if (dot) dot.style.background = isSafeToMove ? "#22c55e" : "#ef4444";
          if (badge) {
            badge.textContent = isSafeToMove ? "ARMED" : "SAFETY LOCKED";
            badge.style.background = isSafeToMove ? "rgba(34,197,94,0.2)" : "rgba(239,68,68,0.2)";
            badge.style.color = isSafeToMove ? "#86efac" : "#f87171";
          }
        } else {
          if (dot) dot.style.background = "#f59e0b";
          if (badge) {
            badge.textContent = "DISCONNECTED";
            badge.style.background = "rgba(245,158,11,0.2)";
            badge.style.color = "#fcd34d";
          }
          if (elDeadman) { elDeadman.textContent = "OFFLINE"; elDeadman.style.color = "#94a3b8"; }
          if (elCh5) { elCh5.textContent = "--"; elCh5.style.color = "#94a3b8"; }
          if (elPower) { elPower.textContent = "--"; elPower.style.color = "#94a3b8"; }
          if (elEstop) { elEstop.textContent = "--"; elEstop.style.color = "#94a3b8"; }
        }
      }

      const modal = document.getElementById("hardware-modal");
      if (modal && modal.style.display !== "none") {
        this._updateExecutionButtonState();
      }

      if (data.is_executing) {
        nextPollMs = 100; // Fast polling for smooth progress during execution
        this._hasSeenExecuting = true;
        this._isExecutingOnHardware = true;

        if (progressCont) progressCont.style.display = "block";
        if (progressBar) {
          progressBar.style.width = `${data.progress_pct}%`;
          progressBar.style.background = "linear-gradient(90deg, #38bdf8, #22c55e)";
        }
        if (progressPct) progressPct.textContent = `${data.progress_pct.toFixed(0)}%`;
        if (progressLabel) {
          progressLabel.textContent = `Running on ${data.arm.toUpperCase()} (${data.current_time_s.toFixed(1)}s / ${data.total_time_s.toFixed(1)}s)`;
          progressLabel.style.color = "var(--text)";
        }
        if (btnExec && !this._isAwaitingExecutionStart) {
          btnExec.disabled = true;
          btnExec.textContent = `Executing (${data.progress_pct.toFixed(0)}%)...`;
        }

        // In HITL mode, reflect simulated arm angles in 3D scene (convert degrees to radians)
        const activeArm = data.arm || this.arm;
        if (data.sim_joint_states && data.sim_joint_states[activeArm]) {
          const simDeg = data.sim_joint_states[activeArm];
          const solidAngles = {};
          for (let i = 0; i < 7; i++) {
            solidAngles[`openarm_${activeArm}_joint${i + 1}`] = (simDeg[i] * Math.PI) / 180.0;
          }
          if (this.view && this.view.setSolidJoints) {
            this.view.setSolidJoints(solidAngles);
          }
        }
        if (data.sim_gripper_state && typeof data.sim_gripper_state[activeArm] === "number") {
          this._setGripper(data.sim_gripper_state[activeArm], false, activeArm);
        }
      } else if (this._hasSeenExecuting && (data.goal_id === this._activeExecutionGoalId || !this._activeExecutionGoalId)) {
        // Transitioned from active execution to finished state
        this._hasSeenExecuting = false;
        this._activeExecutionGoalId = null;
        this._isExecutingOnHardware = false;

        if (data.status === "succeeded") {
          if (progressBar) {
            progressBar.style.width = "100%";
            progressBar.style.background = "#22c55e";
          }
          if (progressPct) progressPct.textContent = "100%";
          if (progressLabel) {
            progressLabel.textContent = `✓ Execution Succeeded (${data.total_time_s.toFixed(1)}s)`;
            progressLabel.style.color = "#4ade80";
          }
          document.getElementById("plan-status-info").textContent = `Robot Execution Succeeded [${(data.status || "HITL").toUpperCase()}]`;

          // Ensure final joint positions and gripper state are shown in 3D view
          const activeArm = data.arm || this.arm;
          if (data.sim_joint_states && data.sim_joint_states[activeArm]) {
            const simDeg = data.sim_joint_states[activeArm];
            const solidAngles = {};
            for (let i = 0; i < 7; i++) {
              solidAngles[`openarm_${activeArm}_joint${i + 1}`] = (simDeg[i] * Math.PI) / 180.0;
            }
            if (this.view && this.view.setSolidJoints) {
              this.view.setSolidJoints(solidAngles);
            }
          }
          if (data.sim_gripper_state && typeof data.sim_gripper_state[activeArm] === "number") {
            this._setGripper(data.sim_gripper_state[activeArm], false, activeArm);
          }
        } else if (data.status === "cancelled") {
          if (progressLabel) {
            const reason = data.last_error ? ` [${data.last_error}]` : "";
            progressLabel.textContent = `🛑 Execution Halted${reason}`;
            progressLabel.style.color = "#fbbf24";
          }
          document.getElementById("plan-status-info").textContent = data.last_error || "Robot Execution Cancelled";
        } else if (data.status === "failed") {
          if (progressLabel) {
            progressLabel.textContent = `✗ Execution Failed: ${data.last_error || "Unknown error"}`;
            progressLabel.style.color = "#f87171";
          }
          document.getElementById("plan-status-info").textContent = "Robot Execution Failed";
        }

        this._updateExecutionButtonState();
      }
    } catch (_) {}

    setTimeout(() => this._pollHardwareStatus(), nextPollMs);
  }

  _updateExecutionButtonState() {
    const btnExec = document.getElementById("btn-hw-confirm-exec");
    if (!btnExec) return;

    if (this._isExecutingOnHardware || this._isAwaitingExecutionStart) {
      return;
    }

    if (!this._preflightPassed) {
      btnExec.disabled = true;
      btnExec.textContent = "Preflight Check Pending";
      btnExec.style.background = "var(--bg-card)";
      btnExec.style.color = "var(--text-muted)";
      return;
    }

    const radios = document.getElementsByName("hw-exec-mode");
    let mode = "hitl";
    for (const r of radios) {
      if (r.checked) mode = r.value;
    }

    if (mode === "hitl") {
      btnExec.disabled = false;
      btnExec.textContent = "Confirm & Execute (HITL Dry-Run)";
      btnExec.style.background = "#22c55e";
      btnExec.style.color = "#ffffff";
      return;
    }

    // Live mode strictly requires Deadman & safety authority
    const saf = this._lastRobotSafety;
    if (!saf || !saf.ok) {
      btnExec.disabled = true;
      btnExec.textContent = "Telemetry Offline (Cannot Verify Deadman)";
      btnExec.style.background = "#64748b";
      btnExec.style.color = "#ffffff";
      return;
    }

    if (saf.estop) {
      btnExec.disabled = true;
      btnExec.textContent = "E-Stop Engaged (Clear First)";
      btnExec.style.background = "#ef4444";
      btnExec.style.color = "#ffffff";
      return;
    }

    if (!saf.motion_power) {
      btnExec.disabled = true;
      btnExec.textContent = "Motion Power Contactor Open";
      btnExec.style.background = "#f59e0b";
      btnExec.style.color = "#ffffff";
      return;
    }

    if (!saf.deadman) {
      btnExec.disabled = true;
      btnExec.textContent = "Hold RC Deadman (CH6) to Enable";
      btnExec.style.background = "#ef4444";
      btnExec.style.color = "#ffffff";
      return;
    }

    if (saf.ch5 === "STANDBY" || saf.ch5 === "OFF") {
      btnExec.disabled = true;
      btnExec.textContent = "Switch CH5 to AUTO / MANUAL";
      btnExec.style.background = "#f59e0b";
      btnExec.style.color = "#ffffff";
      return;
    }

    btnExec.disabled = false;
    btnExec.textContent = "Execute on Live Hardware (CH6 Held)";
    btnExec.style.background = "#22c55e";
    btnExec.style.color = "#ffffff";
  }

  async _openHardwareModal(autoSelect = false, preferredMode = "hitl") {
    // If no trajectory is planned yet, notify user to plan first
    if (!this.trajectory || !this.trajectory.positions_rad || this.trajectory.positions_rad.length === 0) {
      alert("Please plan a trajectory first using 'Plan Trajectory' or 'Plan Show Sequence'.");
      return;
    }

    const modal = document.getElementById("hardware-modal");
    modal.style.display = "flex";

    this._preflightPassed = false;
    this._isExecutingOnHardware = false;

    // Reset progress container on modal open so previous runs don't show stale 100%
    const progressCont = document.getElementById("hw-live-progress-container");
    if (progressCont) progressCont.style.display = "none";
    const progressBar = document.getElementById("hw-progress-bar");
    if (progressBar) {
      progressBar.style.width = "0%";
      progressBar.style.background = "linear-gradient(90deg, #38bdf8, #22c55e)";
    }
    const progressPct = document.getElementById("hw-progress-pct");
    if (progressPct) progressPct.textContent = "0%";
    const progressLabel = document.getElementById("hw-progress-label");
    if (progressLabel) {
      progressLabel.textContent = "Preparing execution...";
      progressLabel.style.color = "var(--text)";
    }

    this._hasSeenExecuting = false;
    this._activeExecutionGoalId = null;
    this._isAwaitingExecutionStart = false;

    // Select mode
    const radios = document.getElementsByName("hw-exec-mode");
    for (const r of radios) {
      if (r.value === preferredMode) r.checked = true;
    }
    this._updateExecutionButtonState();

    // Run Preflight Check
    const summary = document.getElementById("hw-modal-summary");
    const probBox = document.getElementById("hw-problems-box");
    const probList = document.getElementById("hw-problems-list");
    summary.textContent = "Validating trajectory against hardware limits (HAND_BOX, TRIP_DEG_S, clearances)...";
    summary.style.background = "rgba(56,189,248,0.1)";
    summary.style.borderColor = "rgba(56,189,248,0.3)";
    summary.style.color = "#7dd3fc";
    probBox.style.display = "none";
    probList.innerHTML = "";

    try {
      const resp = await fetch("/api/hardware/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          arm: this.arm,
          times: this.trajectory.times,
          positions_rad: this.trajectory.positions_rad,
          velocities_rad_s: this.trajectory.velocities_rad_s,
          min_clearance_mm: this.trajectory.min_clearance_mm || 30.0,
        }),
      });
      const data = await resp.json();

      document.getElementById("hw-stat-clearance").textContent = `${data.min_clearance_mm} mm`;
      document.getElementById("hw-stat-handbox").textContent = data.hand_box_ok ? "PASS" : "FAIL";
      document.getElementById("hw-stat-handbox").style.color = data.hand_box_ok ? "#4ade80" : "#f87171";

      document.getElementById("hw-stat-speed").textContent = data.speed_trip_ok ? "PASS" : "FAIL";
      document.getElementById("hw-stat-speed").style.color = data.speed_trip_ok ? "#4ade80" : "#f87171";

      document.getElementById("hw-stat-limits").textContent = data.joint_limits_ok ? "PASS" : "FAIL";
      document.getElementById("hw-stat-limits").style.color = data.joint_limits_ok ? "#4ade80" : "#f87171";

      if (data.ok) {
        summary.textContent = `All safety checks PASSED. Trajectory duration: ${this.trajectory.duration.toFixed(2)}s, TCP height z: ${data.tcp_min_z_m}..${data.tcp_max_z_m} m. Safe for execution.`;
        summary.style.background = "rgba(34,197,94,0.1)";
        summary.style.borderColor = "rgba(34,197,94,0.3)";
        summary.style.color = "#86efac";
        this._preflightPassed = true;
      } else {
        summary.textContent = "Safety Check FAILED! Trajectory violates physical hardware safety limits.";
        summary.style.background = "rgba(239,68,68,0.1)";
        summary.style.borderColor = "rgba(239,68,68,0.3)";
        summary.style.color = "#fca5a5";
        this._preflightPassed = false;

        probBox.style.display = "block";
        (data.problems || []).forEach((p) => {
          const li = document.createElement("li");
          li.textContent = p;
          probList.appendChild(li);
        });
      }
      this._updateExecutionButtonState();
    } catch (err) {
      summary.textContent = `Preflight error: ${err}`;
      this._preflightPassed = false;
      this._updateExecutionButtonState();
    }
  }

  _closeHardwareModal() {
    document.getElementById("hardware-modal").style.display = "none";
  }

  async _executeOnHardware() {
    if (!this.trajectory) return;
    const radios = document.getElementsByName("hw-exec-mode");
    let mode = "hitl";
    for (const r of radios) {
      if (r.checked) mode = r.value;
    }

    const btnExec = document.getElementById("btn-hw-confirm-exec");
    if (btnExec) {
      btnExec.disabled = true;
      btnExec.textContent = "Starting...";
    }

    const progressCont = document.getElementById("hw-live-progress-container");
    const progressBar = document.getElementById("hw-progress-bar");
    const progressPct = document.getElementById("hw-progress-pct");
    const progressLabel = document.getElementById("hw-progress-label");

    if (progressCont) progressCont.style.display = "block";
    if (progressBar) {
      progressBar.style.width = "0%";
      progressBar.style.background = "linear-gradient(90deg, #38bdf8, #22c55e)";
    }
    if (progressPct) progressPct.textContent = "0%";
    if (progressLabel) {
      progressLabel.textContent = "Starting execution...";
      progressLabel.style.color = "var(--text)";
    }

    this._hasSeenExecuting = false;
    this._activeExecutionGoalId = null;
    this._isAwaitingExecutionStart = true;
    this._isExecutingOnHardware = true;

    try {
      const resp = await fetch("/api/hardware/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({
          arm: this.arm,
          mode: mode,
          trajectory: {
            times: this.trajectory.times,
            positions_rad: this.trajectory.positions_rad,
            velocities_rad_s: this.trajectory.velocities_rad_s,
            min_clearance_mm: this.trajectory.min_clearance_mm,
            gripper_events: this.trajectory.gripperEvents || [],
          },
        }),
      });
      const data = await resp.json();
      if (!data.success) {
        alert(`Hardware execution refused:\n${data.error}`);
        this._isAwaitingExecutionStart = false;
        this._isExecutingOnHardware = false;
        this._updateExecutionButtonState();
      } else {
        this._activeExecutionGoalId = data.goal_id;
        this._isAwaitingExecutionStart = false;
        if (btnExec) {
          btnExec.disabled = true;
          btnExec.textContent = "Executing...";
        }
        document.getElementById("plan-status-info").textContent = `Robot Execution Active [${mode.toUpperCase()}]`;
      }
    } catch (err) {
      alert(`Hardware execution error: ${err}`);
      this._isAwaitingExecutionStart = false;
      this._isExecutingOnHardware = false;
      this._updateExecutionButtonState();
    }
  }

  async _emergencyHold() {
    try {
      const resp = await fetch("/api/hardware/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: jsonReq({}),
      });
      const data = await resp.json();
      alert(`EMERGENCY HOLD: ${data.message || "Arm stopped."}`);
      document.getElementById("plan-status-info").textContent = "EMERGENCY HOLD ACTIVATED. Robot halted.";
    } catch (err) {
      alert(`Hold error: ${err}`);
    }
  }
}

function jsonReq(obj) {
  return JSON.stringify(obj);
}

function round1(val) {
  return Math.round(val * 10) / 10;
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

window.addEventListener("DOMContentLoaded", () => {
  const app = new StudioApp();
  window.app = app;
  app.init();
});
