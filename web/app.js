// Frontend logic for the OpenSquawk Bridge desktop app.
// Talks to the Python backend through window.pywebview.api.

const PHASES = [
  "Parked", "Taxi", "Takeoff", "Climb",
  "Cruise", "Descent", "Approach", "Landing", "Rollout",
];

let apiReady = false;
let qrRendered = false;
let teleOpen = false;        // live telemetry collapsed by default
let loginClicked = false;    // show the waiting indicator after the user starts login
let pttCapturing = false;    // mirrors backend capture state for the Set/Cancel toggle

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

let simsSig = "";
function renderSimulators(state) {
  const sources = state.sources || [];
  const sig = JSON.stringify(sources.map((s) => [s.id, s.available])) + "|" + state.source_id;
  if (sig === simsSig) return;            // only re-render on real change
  simsSig = sig;
  const sel = $("sim-select");
  sel.innerHTML = "";
  sources.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.available ? s.label : `${s.label} (coming soon)`;
    opt.disabled = !s.available;
    if (s.id === state.source_id) opt.selected = true;
    sel.appendChild(opt);
  });
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
  $("bg-banner").classList.toggle("hidden", !connected);
}

function renderPtt(state) {
  pttCapturing = state.ptt_capturing || null;   // 'key' | 'joy' | null
  const capturing = pttCapturing !== null;

  if (pttCapturing === "key") setText("ptt-key", "Press a key or combo…");
  else if (pttCapturing === "joy") setText("ptt-key", "Press a joystick button…");
  else setText("ptt-key", state.ptt_key_label);

  // Status tag: live TX while transmitting, else ARMED / OFF.
  const tag = $("ptt-status");
  if (state.ptt_active) {
    tag.textContent = "TX"; tag.className = "tag tag-tx";
  } else if (state.ptt_set && !capturing) {
    tag.textContent = "ARMED"; tag.className = "tag tag-green";
  } else {
    tag.textContent = "OFF"; tag.className = "tag tag-grey";
  }

  // Live "Transmitting…" banner — proves the trigger is recognised even before
  // anything reaches the browser.
  $("ptt-tx").classList.toggle("hidden", !state.ptt_active);

  // Key button: doubles as Cancel while capturing a key.
  $("ptt-set-btn").textContent = pttCapturing === "key" ? "Cancel" : (state.ptt_set ? "Change" : "Set key");
  $("ptt-set-btn").classList.toggle("hidden", pttCapturing === "joy");

  // Joystick button: only when pygame found a device; doubles as Cancel.
  const joyBtn = $("ptt-joy-btn");
  joyBtn.classList.toggle("hidden", !state.ptt_joy_supported || pttCapturing === "key");
  joyBtn.textContent = pttCapturing === "joy" ? "Cancel" : "Set joystick";

  $("ptt-clear-btn").classList.toggle("hidden", !state.ptt_set || capturing);

  $("ptt-capturing").classList.toggle("hidden", !capturing);
  setText(
    "ptt-capturing-text",
    pttCapturing === "joy"
      ? "Press a joystick button to bind it…"
      : "Press a key or hold a combo, then release… (Esc to cancel)",
  );

  // We can't detect whether Input Monitoring was granted, so on macOS we show
  // the hint once a trigger is bound — that's exactly when a missing grant bites.
  $("ptt-perm").classList.toggle("hidden", !(state.ptt_is_mac && state.ptt_set));
}

// Mirror the backend's _pretty_key: turn an identity string ("char:m",
// "key:ctrl_l", "vk:65") into a readable label for the step list.
function prettyKey(identity) {
  const i = identity.indexOf(":");
  const kind = i === -1 ? identity : identity.slice(0, i);
  const value = i === -1 ? "" : identity.slice(i + 1);
  if (kind === "char") return value.toUpperCase();
  if (kind === "key") return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  if (kind === "vk") {
    const ch = String.fromCharCode(parseInt(value, 10));
    return /\S/.test(ch) ? ch.toUpperCase() : `Key ${value}`;
  }
  return identity;
}

function stepLabel(s) {
  if (s.type === "wait") return `Wait ${s.seconds}s`;
  if (s.type === "key") return `Key ${s.keys.map(prettyKey).join(" + ")}`;
  if (s.type === "click") return `Click ${s.button} @ ${s.x},${s.y}`;
  return s.type;
}

let actActiveId = null;       // id of the chain shown in the editor
let actCapturing = null;      // 'key' | 'joy' | null, for the active chain

function activeChain(state) {
  const chains = state.actions_chains || [];
  return chains.find((c) => c.id === state.actions_active_id) || chains[0] || null;
}

let actTabsSig = "";
function renderActionTabs(state) {
  const chains = state.actions_chains || [];
  const sig = JSON.stringify(chains.map((c) => [c.id, c.name, c.enabled]))
    + "|" + state.actions_active_id;
  if (sig === actTabsSig) return;
  actTabsSig = sig;
  const tabs = $("act-tabs");
  tabs.innerHTML = "";
  chains.forEach((c) => {
    const b = document.createElement("button");
    b.className = "act-tab"
      + (c.id === state.actions_active_id ? " active" : "")
      + (c.enabled ? "" : " off");
    b.dataset.id = c.id;
    b.textContent = c.name || "Action";
    tabs.appendChild(b);
  });
  const add = document.createElement("button");
  add.className = "act-tab act-tab-add";
  add.textContent = "＋";
  tabs.appendChild(add);
}

let actStepsSig = "";
function renderActions(state) {
  renderActionTabs(state);
  const chains = state.actions_chains || [];
  const chain = activeChain(state);
  actActiveId = chain ? chain.id : null;

  $("act-empty").classList.toggle("hidden", chains.length > 0);
  $("act-editor").classList.toggle("hidden", !chain);

  const running = state.actions_running;
  const recording = !!state.actions_recording_id;
  const tag = $("act-tag");
  const anyArmed = chains.some((c) => c.enabled && c.trigger);
  if (running) { tag.textContent = "RUN"; tag.className = "tag tag-tx"; }
  else if (recording) { tag.textContent = "REC"; tag.className = "tag tag-amber"; }
  else if (anyArmed) { tag.textContent = "ARMED"; tag.className = "tag tag-green"; }
  else { tag.textContent = "OFF"; tag.className = "tag tag-grey"; }

  if (!chain) { actStepsSig = ""; actCapturing = null; return; }

  const capturing = state.actions_capturing && state.actions_capturing_id === chain.id;
  actCapturing = capturing ? state.actions_capturing : null;

  const nameEl = $("act-name");
  if (document.activeElement !== nameEl) nameEl.value = chain.name || "";
  $("act-enabled").checked = !!chain.enabled;

  setText("act-trigger", capturing
    ? (actCapturing === "joy" ? "Press a joystick button…" : "Press a key or combo…")
    : (chain.trigger_label || "Not set"));
  $("act-hook-select").value = chain.trigger_hook || "";
  $("act-clear-trigger").classList.toggle("hidden", !chain.trigger);
  $("act-set-key").textContent = actCapturing === "key" ? "Cancel" : "Set key";
  $("act-set-joy").textContent = actCapturing === "joy" ? "Cancel" : "Set joystick";

  const steps = chain.steps || [];
  const sig = chain.id + "|" + JSON.stringify(steps);
  if (sig !== actStepsSig) {
    actStepsSig = sig;
    const list = $("act-steps");
    list.innerHTML = "";
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "act-step";
      const span = document.createElement("span");
      span.textContent = stepLabel(s);
      const del = document.createElement("button");
      del.className = "act-del"; del.textContent = "✕"; del.dataset.i = i;
      li.append(span, del);
      list.appendChild(li);
    });
  }

  const recordingThis = state.actions_recording_id === chain.id;
  $("act-record").textContent = recordingThis ? "Stop recording" : "Record";
  $("act-record").classList.toggle("btn-ghost", recordingThis);
  $("act-run").textContent = running ? "Stop" : "Run now";
  $("act-clear").classList.toggle("hidden", steps.length === 0);
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
    // simulator / source status
    const active = state.source_id && state.source_id !== "none";
    const connected = active && state.stream_status === "streaming";
    const simTag = $("sim-status");
    simTag.textContent = connected ? "CONNECTED" : (active ? "CONNECTING" : "DISCONNECTED");
    simTag.className = connected ? "tag tag-green" : (active ? "tag tag-amber" : "tag tag-grey");
    $("sim-aircraft").textContent = state.aircraft || (active ? "Detecting aircraft…" : "—");

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
    setText("m-coma", t.com_active_frequency != null ? t.com_active_frequency.toFixed(3) : "—");
    setText("m-coms", t.com_standby_frequency != null ? t.com_standby_frequency.toFixed(3) : "—");
    setText("m-sqwk", t.transponder_code != null ? String(t.transponder_code).padStart(4, "0") : "—");
    setText("m-lat", t.latitude_deg != null ? t.latitude_deg.toFixed(4) : "—");
    setText("m-lon", t.longitude_deg != null ? t.longitude_deg.toFixed(4) : "—");
    setText("m-hdg", t.heading_deg != null ? Math.round(t.heading_deg) + "°" : "—");

    const phaseTag = $("phase-tag");
    phaseTag.textContent = (state.flight_phase || "PARKED").toUpperCase();
    phaseTag.className = active ? "tag tag-cyan" : "tag tag-grey";
    updatePlane(state.flight_progress || 0, state.flight_phase || "Parked");

    renderPtt(state);
    renderActions(state);
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
    actStepsSig = ""; actTabsSig = "";   // force the actions UI to re-render after token rotation
    $("act-body").classList.add("hidden");
    $("act-head").setAttribute("aria-expanded", "false");
    simsSig = "";   // force the source dropdown to re-render after token rotation
    api().logout();
  });
  $("signup-link").addEventListener("click", (e) => { e.preventDefault(); api().open_signup(); });
  $("open-pm-btn").addEventListener("click", () => api().open_pm());
  $("ptt-set-btn").addEventListener("click", () => {
    if (pttCapturing === "key") api().ptt_cancel_capture();
    else api().ptt_capture_key();
  });
  $("ptt-joy-btn").addEventListener("click", () => {
    if (pttCapturing === "joy") api().ptt_cancel_capture();
    else api().ptt_capture_joy();
  });
  $("ptt-clear-btn").addEventListener("click", () => api().ptt_clear());
  $("ptt-perm-btn").addEventListener("click", () => api().open_input_monitoring());
  $("sim-select").addEventListener("change", (e) => api().set_source(e.target.value));
  $("ptt-head").addEventListener("click", () => {
    const open = !$("ptt-body").classList.toggle("hidden");
    $("ptt-head").setAttribute("aria-expanded", String(open));
  });
  $("tele-head").addEventListener("click", toggleTelemetry);

  $("act-head").addEventListener("click", () => {
    const open = !$("act-body").classList.toggle("hidden");
    $("act-head").setAttribute("aria-expanded", String(open));
  });
  // tab strip: switch chain, or add a new one via the trailing ＋
  $("act-tabs").addEventListener("click", (e) => {
    const t = e.target.closest(".act-tab");
    if (!t) return;
    if (t.classList.contains("act-tab-add")) { api().actions_add_chain(); actTabsSig = ""; }
    else if (t.dataset.id) { api().actions_set_active(t.dataset.id); actTabsSig = ""; actStepsSig = ""; }
  });
  $("act-add-empty").addEventListener("click", () => { api().actions_add_chain(); actTabsSig = ""; });
  $("act-name").addEventListener("change", (e) => {
    if (actActiveId) { api().actions_rename_chain(actActiveId, e.target.value); actTabsSig = ""; }
  });
  $("act-enabled").addEventListener("change", (e) => {
    if (actActiveId) { api().actions_set_enabled(actActiveId, e.target.checked); actTabsSig = ""; }
  });
  $("act-delete").addEventListener("click", () => {
    if (actActiveId && confirm("Delete this action?")) {
      api().actions_remove_chain(actActiveId); actTabsSig = ""; actStepsSig = "";
    }
  });
  $("act-hook-select").addEventListener("change", (e) => {
    if (!actActiveId) return;
    if (e.target.value) api().actions_set_trigger_hook(actActiveId, e.target.value);
    else api().actions_clear_trigger(actActiveId);
    actTabsSig = "";
  });
  $("act-set-key").addEventListener("click", () => {
    if (!actActiveId) return;
    if (actCapturing === "key") api().actions_cancel_capture();
    else api().actions_capture_trigger(actActiveId, "key");
  });
  $("act-set-joy").addEventListener("click", () => {
    if (!actActiveId) return;
    if (actCapturing === "joy") api().actions_cancel_capture();
    else api().actions_capture_trigger(actActiveId, "joy");
  });
  $("act-clear-trigger").addEventListener("click", () => {
    if (actActiveId) { api().actions_clear_trigger(actActiveId); actTabsSig = ""; }
  });
  $("act-add-wait").addEventListener("click", () => {
    if (!actActiveId) return;
    const v = parseFloat(prompt("Wait seconds:", "1") || "");
    if (!isNaN(v)) { api().actions_add_step(actActiveId, { type: "wait", seconds: v }); actStepsSig = ""; }
  });
  $("act-add-click").addEventListener("click", () => {
    if (!actActiveId) return;
    const x = parseInt(prompt("Click X:", "0") || "", 10);
    const y = parseInt(prompt("Click Y:", "0") || "", 10);
    if (!isNaN(x) && !isNaN(y)) { api().actions_add_step(actActiveId, { type: "click", x, y, button: "left" }); actStepsSig = ""; }
  });
  $("act-record").addEventListener("click", async () => {
    if (!actActiveId) return;
    const s = await api().get_state();
    if (s.actions_recording_id) api().actions_record_stop();
    else api().actions_record_start(actActiveId);
  });
  $("act-run").addEventListener("click", async () => {
    if (!actActiveId) return;
    const s = await api().get_state();
    if (s.actions_running) api().actions_stop(); else api().actions_run_now(actActiveId);
  });
  $("act-clear").addEventListener("click", () => {
    if (actActiveId && confirm("Clear all steps?")) { api().actions_clear_steps(actActiveId); actStepsSig = ""; }
  });
  $("act-steps").addEventListener("click", (e) => {
    if (actActiveId && e.target.classList.contains("act-del")) {
      api().actions_remove_step(actActiveId, parseInt(e.target.dataset.i, 10)); actStepsSig = "";
    }
  });
}

window.addEventListener("pywebviewready", () => { apiReady = true; });

document.addEventListener("DOMContentLoaded", () => {
  initProfile();
  wireEvents();
  if (window.pywebview && window.pywebview.api) apiReady = true;
  setInterval(tick, 300);
});
