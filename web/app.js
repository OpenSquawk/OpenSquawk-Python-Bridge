// Frontend logic for the OpenSquawk Bridge desktop app.
// Talks to the Python backend through window.pywebview.api.

const PHASES = [
  "Parked", "Taxi", "Takeoff", "Climb",
  "Cruise", "Descent", "Approach", "Landing", "Rollout",
];

let apiReady = false;
let simsRendered = false;

function api() {
  return window.pywebview && window.pywebview.api;
}

// ---- flight profile path ---------------------------------------------------
// A normalized trajectory across the 0..1000 viewBox. Y is inverted (200=ground).
const PROFILE_POINTS = [
  [0, 200],     // parked
  [120, 198],   // taxi
  [200, 175],   // takeoff / rotate
  [360, 70],    // climb
  [560, 40],    // cruise
  [760, 95],    // descent
  [880, 165],   // approach
  [950, 198],   // landing
  [1000, 200],  // rollout
];

function buildProfilePath() {
  // smooth-ish polyline using quadratic-ish midpoints
  const pts = PROFILE_POINTS;
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x, y] = pts[i];
    const [px, py] = pts[i - 1];
    const cx = (px + x) / 2;
    d += ` Q ${px} ${py} ${cx} ${(py + y) / 2}`;
    d += ` T ${x} ${y}`;
  }
  return d;
}

function initProfile() {
  const path = document.getElementById("profile-path");
  const fill = document.getElementById("profile-fill");
  const d = buildProfilePath();
  path.setAttribute("d", d);
  fill.setAttribute("d", `${d} L 1000 200 L 0 200 Z`);

  // phase track labels
  const track = document.getElementById("phase-track");
  track.innerHTML = "";
  PHASES.forEach((p) => {
    const el = document.createElement("div");
    el.className = "phase-step";
    el.dataset.phase = p;
    el.textContent = p;
    track.appendChild(el);
  });
}

function updatePlane(progress, phase) {
  const path = document.getElementById("profile-path");
  const plane = document.getElementById("plane");
  if (!path.getTotalLength) return;
  const len = path.getTotalLength();
  const at = path.getPointAtLength(Math.max(0, Math.min(1, progress)) * len);
  // a touch above the line and angle from a nearby point
  const ahead = path.getPointAtLength(Math.min(len, (progress + 0.01) * len));
  const angle = Math.atan2(ahead.y - at.y, ahead.x - at.x) * (180 / Math.PI);
  plane.setAttribute("transform", `translate(${at.x}, ${at.y - 12}) rotate(${angle})`);

  document.querySelectorAll(".phase-step").forEach((el) => {
    el.classList.toggle("active", el.dataset.phase === phase);
  });
}

// ---- rendering -------------------------------------------------------------

function renderSimulators(state) {
  if (simsRendered) return;
  const sel = document.getElementById("sim-select");
  sel.innerHTML = "";
  state.simulators.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.available ? s.label : `${s.label} (coming soon)`;
    opt.disabled = !s.available;
    if (s.id === state.simulator_id) opt.selected = true;
    sel.appendChild(opt);
  });
  simsRendered = true;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function render(state) {
  // connection pill + account card
  const connPill = document.getElementById("conn-pill");
  const accStatus = document.getElementById("account-status");
  const accBody = document.getElementById("account-body");
  const accLinked = document.getElementById("account-linked");

  if (state.connected && state.user) {
    connPill.textContent = "Linked";
    connPill.className = "pill pill-ok";
    accStatus.textContent = "LINKED";
    accStatus.className = "tag tag-cyan";
    accBody.classList.add("hidden");
    accLinked.classList.remove("hidden");
    setText("user-name", state.user.name || "—");
    setText("user-email", state.user.email || "—");
  } else {
    connPill.textContent = "Not linked";
    connPill.className = "pill pill-muted";
    accStatus.textContent = "LOGIN NEEDED";
    accStatus.className = "tag";
    accBody.classList.remove("hidden");
    accLinked.classList.add("hidden");
  }

  setText("token", state.token || "—");
  setText("base-url", `Connected to ${state.base_url}`);

  // simulator status tag
  const simTag = document.getElementById("sim-status");
  if (state.sim_active) {
    simTag.textContent = "CONNECTED";
    simTag.className = "tag tag-green";
  } else {
    simTag.textContent = "DISCONNECTED";
    simTag.className = "tag tag-grey";
  }
  const toggle = document.getElementById("sim-toggle");
  if (toggle.checked !== state.sim_active) toggle.checked = state.sim_active;

  // stream status
  const dot = document.getElementById("stream-dot");
  const label = document.getElementById("stream-label");
  const map = {
    streaming: ["dot dot-green", "Streaming live"],
    stalling: ["dot dot-amber", "Stalling…"],
    idle: ["dot dot-grey", "Idle"],
  };
  const [cls, txt] = map[state.stream_status] || map.idle;
  dot.className = cls;
  label.textContent = txt;

  // telemetry
  const t = state.telemetry || {};
  setText("m-ias", Math.round(t.ias_kt || 0));
  setText("m-alt", Math.round(t.altitude_ft_indicated || 0).toLocaleString());
  setText("m-vs", Math.round(t.vertical_speed_fpm || 0));
  setText("m-n1", Math.round(t.n1_pct || 0));
  setText("m-gear", t.gear_handle ? "DOWN" : "UP");
  setText("m-flaps", t.flaps_index != null ? t.flaps_index : 0);

  // phase tag + plane
  const phaseTag = document.getElementById("phase-tag");
  phaseTag.textContent = (state.flight_phase || "PARKED").toUpperCase();
  updatePlane(state.flight_progress || 0, state.flight_phase || "Parked");

  // error banner
  const banner = document.getElementById("banner");
  if (state.error) {
    banner.textContent = state.error;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

async function tick() {
  if (!apiReady) return;
  try {
    const state = await api().get_state();
    renderSimulators(state);
    render(state);
  } catch (e) {
    // backend not ready yet; ignore
  }
}

// ---- wiring ----------------------------------------------------------------

function wireEvents() {
  document.getElementById("login-btn").addEventListener("click", () => {
    api().login();
  });
  document.getElementById("logout-btn").addEventListener("click", () => {
    const toggle = document.getElementById("sim-toggle");
    toggle.checked = false;
    api().logout();
  });
  document.getElementById("sim-toggle").addEventListener("change", (e) => {
    api().set_sim_active(e.target.checked);
  });
  document.getElementById("sim-select").addEventListener("change", (e) => {
    api().set_simulator(e.target.value);
  });
}

window.addEventListener("pywebviewready", () => {
  apiReady = true;
});

document.addEventListener("DOMContentLoaded", () => {
  initProfile();
  wireEvents();
  updatePlane(0, "Parked");
  // pywebviewready may have fired already
  if (window.pywebview && window.pywebview.api) apiReady = true;
  setInterval(tick, 300);
});
