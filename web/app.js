// Frontend logic for the OpenSquawk Bridge desktop app.
// Talks to the Python backend through window.pywebview.api.

const PHASES = [
  "Parked", "Taxi", "Takeoff", "Climb",
  "Cruise", "Descent", "Approach", "Landing", "Rollout",
];

let apiReady = false;
let simsRendered = false;
let qrRendered = false;
let teleOpen = false;        // live telemetry collapsed by default
let loginClicked = false;    // show the waiting indicator after the user starts login

function api() {
  return window.pywebview && window.pywebview.api;
}

function $(id) { return document.getElementById(id); }
function setText(id, value) { const el = $(id); if (el) el.textContent = value; }

// ---- flight profile path ---------------------------------------------------
const PROFILE_POINTS = [
  [0, 200], [120, 198], [200, 175], [360, 70], [560, 40],
  [760, 95], [880, 165], [950, 198], [1000, 200],
];

function buildProfilePath() {
  const pts = PROFILE_POINTS;
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x, y] = pts[i];
    const [px, py] = pts[i - 1];
    const cx = (px + x) / 2;
    d += ` Q ${px} ${py} ${cx} ${(py + y) / 2} T ${x} ${y}`;
  }
  return d;
}

function initProfile() {
  const path = $("profile-path");
  const fill = $("profile-fill");
  const d = buildProfilePath();
  path.setAttribute("d", d);
  fill.setAttribute("d", `${d} L 1000 200 L 0 200 Z`);

  const track = $("phase-track");
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
  if (!teleOpen) return;            // skip layout math while collapsed (hidden)
  const path = $("profile-path");
  const plane = $("plane");
  if (!path.getTotalLength) return;
  const len = path.getTotalLength();
  if (!len) return;
  const p = Math.max(0, Math.min(1, progress));
  const at = path.getPointAtLength(p * len);
  const ahead = path.getPointAtLength(Math.min(len, (p + 0.01) * len));
  const angle = Math.atan2(ahead.y - at.y, ahead.x - at.x) * (180 / Math.PI);
  plane.setAttribute("transform", `translate(${at.x}, ${at.y - 12}) rotate(${angle})`);

  document.querySelectorAll(".phase-step").forEach((el) => {
    el.classList.toggle("active", el.dataset.phase === phase);
  });
}

// ---- rendering helpers -----------------------------------------------------

function renderSimulators(state) {
  if (simsRendered) return;
  const sel = $("sim-select");
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

function renderQr(state) {
  if (qrRendered) return;
  const box = $("qr-box");
  if (state.pm_qr_svg) {
    box.innerHTML = state.pm_qr_svg;
    qrRendered = true;
  } else {
    box.innerHTML = '<span class="qr-ph">no QR</span>';
  }
}

function showView(connected) {
  $("view-login").classList.toggle("hidden", connected);
  $("view-main").classList.toggle("hidden", !connected);
  $("logout-btn").classList.toggle("hidden", !connected);
}

// ---- main render -----------------------------------------------------------

function render(state) {
  // pairing code (login view)
  setText("code-digits", state.token || "······");

  // connection pill + view switch
  const connPill = $("conn-pill");
  if (state.connected && state.user) {
    connPill.textContent = state.user.name || "Linked";
    connPill.className = "pill pill-ok";
    showView(true);
    renderQr(state);
    // simulator status
    const simTag = $("sim-status");
    if (state.sim_active) { simTag.textContent = "CONNECTED"; simTag.className = "tag tag-green"; }
    else { simTag.textContent = "DISCONNECTED"; simTag.className = "tag tag-grey"; }
    const toggle = $("sim-toggle");
    if (toggle.checked !== state.sim_active) toggle.checked = state.sim_active;

    // stream status
    const map = {
      streaming: ["dot dot-green", "Streaming live"],
      stalling: ["dot dot-amber", "Reconnecting…"],
      idle: ["dot dot-grey", "Idle"],
    };
    const [cls, txt] = map[state.stream_status] || map.idle;
    $("stream-dot").className = cls;
    $("stream-label").textContent = txt;

    // telemetry values
    const t = state.telemetry || {};
    setText("m-ias", Math.round(t.ias_kt || 0));
    setText("m-alt", Math.round(t.altitude_ft_indicated || 0).toLocaleString());
    setText("m-vs", Math.round(t.vertical_speed_fpm || 0));
    setText("m-n1", Math.round(t.n1_pct || 0));
    setText("m-gear", t.gear_handle ? "DOWN" : "UP");
    setText("m-flaps", t.flaps_index != null ? t.flaps_index : 0);

    const phaseTag = $("phase-tag");
    phaseTag.textContent = (state.flight_phase || "PARKED").toUpperCase();
    phaseTag.className = state.sim_active ? "tag tag-cyan" : "tag tag-grey";
    updatePlane(state.flight_progress || 0, state.flight_phase || "Parked");
  } else {
    connPill.textContent = "Not linked";
    connPill.className = "pill pill-muted";
    showView(false);
    $("login-waiting").classList.toggle("hidden", !loginClicked);
  }

  // error banner
  const banner = $("banner");
  if (state.error) { banner.textContent = state.error; banner.classList.remove("hidden"); }
  else banner.classList.add("hidden");
}

// ---- polling ---------------------------------------------------------------

async function tick() {
  if (!apiReady) return;
  try {
    const state = await api().get_state();
    renderSimulators(state);
    render(state);
  } catch (e) { /* backend not ready */ }
}

// ---- wiring ----------------------------------------------------------------

function toggleTelemetry() {
  teleOpen = !teleOpen;
  $("tele-body").classList.toggle("hidden", !teleOpen);
  $("tele-head").setAttribute("aria-expanded", String(teleOpen));
}

function wireEvents() {
  $("login-btn").addEventListener("click", () => {
    loginClicked = true;
    $("login-waiting").classList.remove("hidden");
    api().login();
  });
  $("logout-btn").addEventListener("click", () => {
    loginClicked = false;
    qrRendered = false;   // token rotates on logout → redraw QR on next link
    teleOpen = false;
    $("tele-body").classList.add("hidden");
    $("tele-head").setAttribute("aria-expanded", "false");
    $("sim-toggle").checked = false;
    api().logout();
  });
  $("signup-link").addEventListener("click", (e) => { e.preventDefault(); api().open_signup(); });
  $("open-pm-btn").addEventListener("click", () => api().open_pm());
  $("sim-toggle").addEventListener("change", (e) => api().set_sim_active(e.target.checked));
  $("sim-select").addEventListener("change", (e) => api().set_simulator(e.target.value));
  $("tele-head").addEventListener("click", toggleTelemetry);
}

window.addEventListener("pywebviewready", () => { apiReady = true; });

document.addEventListener("DOMContentLoaded", () => {
  initProfile();
  wireEvents();
  if (window.pywebview && window.pywebview.api) apiReady = true;
  setInterval(tick, 300);
});
