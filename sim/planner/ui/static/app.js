/* OpenArm Motion Planner Studio & Halloween Show Designer
 * Main Application Logic
 */
import { PlannerStudioView } from "./armview.js?v=2.4";

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
  }

  async init() {
    // 1. Build sliders and bind events FIRST so all buttons work immediately
    this._createJointSliders();
    this._bindEvents();
    this._syncUIFromState();

    // 2. Fetch backend status and keyframes in parallel
    this._loadBackendStatus();
    this._loadKeyframes();
    this._checkCollisionAndFK();

    // 3. Initialize 3D view gracefully without blocking buttons or UI
    const canvas = document.getElementById("arm-canvas");
    try {
      if (!canvas) throw new Error("Canvas element #arm-canvas not found in DOM");
      logClient("info", `Initializing 3D view on canvas (${canvas.clientWidth}x${canvas.clientHeight})...`);
      this.view = await new PlannerStudioView(canvas).load();
      logClient("info", "3D WebGL PlannerStudioView loaded successfully.");
      this._updateArmPosesInView();
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
      const presets = getPresetsForArm(this.arm);
      const p = presets[e.target.value];
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

    // Benchmark modal buttons
    document.getElementById("btn-open-benchmark").onclick = () => this._openBenchmarkModal();
    document.getElementById("btn-close-benchmark").onclick = () => this._closeBenchmarkModal();
    document.getElementById("btn-close-benchmark-footer").onclick = () => this._closeBenchmarkModal();
    document.getElementById("btn-run-benchmark-now").onclick = () => this._runBenchmark();
  }

  _setArm(arm) {
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
    this._checkCollisionAndFK();

    // Auto-mirror keyframe sequence poses to match the selected arm (flip J1, J2, J3)
    let mirrored = 0;
    this.keyframes.forEach((kf) => {
      if (kf.arm !== arm) {
        kf.arm = arm;
        kf.joints_deg = [-kf.joints_deg[0], -kf.joints_deg[1], -kf.joints_deg[2], kf.joints_deg[3], kf.joints_deg[4], kf.joints_deg[5], kf.joints_deg[6]];
        mirrored++;
      }
    });
    if (mirrored > 0) {
      this._renderKeyframeList();
      this._saveKeyframes();
    }
  }

  _setTargetMode(mode) {
    this.targetMode = mode;
    document.getElementById("btn-mode-goal").classList.toggle("active", mode === "goal");
    document.getElementById("btn-mode-start").classList.toggle("active", mode === "start");
    this._syncUIFromState();
  }

  _syncUIFromState() {
    const currentQ = this.targetMode === "goal" ? this.qGoal : this.qStart;
    for (let i = 0; i < 7; i++) {
      const slider = document.getElementById(`slider-j${i}`);
      const readout = document.getElementById(`val-j${i}`);
      if (slider) slider.value = currentQ[i];
      if (readout) readout.textContent = `${currentQ[i].toFixed(1)}°`;
    }
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

    // Update readout
    document.getElementById("timeline-readout").textContent = `${t.toFixed(2)}s / ${traj.duration.toFixed(2)}s`;
  }

  /* Keyframe Choreographer */
  async _loadKeyframes() {
    try {
      const resp = await fetch("/api/keyframes");
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
        body: jsonReq({ keyframes: this.keyframes }),
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

      card.innerHTML = `
        <div class="kf-header">
          <span class="kf-title">${index + 1}. ${escapeHtml(kf.name)}</span>
          <span class="kf-time">${kf.time_s.toFixed(1)}s</span>
        </div>
        <div class="kf-details">
          <span>${kf.arm.toUpperCase()} ARM</span>
          <span style="font-family:var(--font-mono); font-size:10px;">[${jStr}]°</span>
        </div>
        <div style="display:flex; justify-content:flex-end; gap:6px; margin-top:4px;">
          <button class="icon-btn" style="padding:2px 6px; font-size:10px;" data-act="load">Load Target</button>
          <button class="icon-btn" style="padding:2px 6px; font-size:10px;" data-act="del">✕</button>
        </div>
      `;

      card.querySelector('[data-act="load"]').onclick = (e) => {
        e.stopPropagation();
        this._setArm(kf.arm);
        this.qGoal = [...kf.joints_deg];
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

    const name = nameInput.value.trim() || `Pose ${this.keyframes.length + 1}`;
    const time_s = parseFloat(timeInput.value) || (this.keyframes.length ? this.keyframes[this.keyframes.length - 1].time_s + 4.0 : 0.0);

    const kf = {
      name,
      arm: this.arm,
      time_s,
      joints_deg: [...this.qGoal],
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
    let cumulativeDuration = 0.0;

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
      }

      this.trajectory = {
        success: true,
        arm: this.arm,
        duration: cumulativeDuration,
        times: allTimes,
        positions_deg: allPositions,
        ee_positions: allEePositions,
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
