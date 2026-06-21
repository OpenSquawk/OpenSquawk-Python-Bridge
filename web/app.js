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

// ---- inline icon set (Lucide-style strokes) --------------------------------
const ICON = {
  plane: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6 18.4 18.4"/>',
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  x: '<path d="M6 6 18 18M18 6 6 18"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  plug: '<path d="M12 22v-5M9 8V2M15 8V2"/><path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/>',
  pin: '<path d="M12 21s7-6.6 7-11a7 7 0 1 0-14 0c0 4.4 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  joystick: '<circle cx="12" cy="7" r="4"/><path d="M12 11v3"/><path d="M7 21h10l-1.4-7H8.4z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  mouse: '<rect x="6" y="3" width="12" height="18" rx="6"/><path d="M12 7v4"/>',
  trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>',
  record: '<circle cx="12" cy="12" r="8"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
};
function svg(name, extra) {
  return '<svg class="ico' + (extra ? ' ' + extra : '') + '" viewBox="0 0 24 24" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + (ICON[name] || '') + '</svg>';
}
const SIM_ICON = { none: "ban", dummy: "box", msfs2024: "plane", msfs2020: "plane", xplane: "x", flightgear: "gear" };
const TRIG_ICON = { app_start: "power", sim: "plug", aircraft: "plane", gps_jump: "pin", key: "keyboard", joy: "joystick" };
const STEP_ICON = { wait: "clock", key: "keyboard", click: "mouse" };

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
let simMenuOpen = false;
function renderSimulators(state) {
  const sources = state.sources || [];
  const sig = JSON.stringify(sources.map((s) => [s.id, s.available])) + "|" + state.source_id;
  if (sig === simsSig) return;            // only re-render on real change
  simsSig = sig;

  const cur = sources.find((s) => s.id === state.source_id) || sources[0] || { id: "none", label: "(None)" };
  $("sim-cur").innerHTML = svg(SIM_ICON[cur.id] || "plane")
    + `<span class="cs-label">${escapeHtml(cur.label)}</span>`;

  const menu = $("sim-menu");
  menu.innerHTML = "";
  sources.forEach((s) => {
    const li = document.createElement("li");
    li.className = "cselect-opt"
      + (s.id === state.source_id ? " sel" : "")
      + (s.available ? "" : " disabled");
    li.setAttribute("role", "option");
    li.dataset.id = s.id;
    li.dataset.available = s.available ? "1" : "0";
    li.innerHTML = svg(SIM_ICON[s.id] || "plane")
      + `<span class="cs-label">${escapeHtml(s.label)}</span>`
      + (s.available ? "" : '<span class="cs-soon">soon</span>')
      + (s.id === state.source_id ? svg("check", "cs-check") : "");
    menu.appendChild(li);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function setSimMenu(open) {
  simMenuOpen = open;
  $("sim-menu").classList.toggle("hidden", !open);
  $("sim-select").dataset.open = open ? "true" : "false";
  $("sim-btn").setAttribute("aria-expanded", String(open));
  $("sim-select").closest(".panel")?.classList.toggle("panel-raised", open);
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

function userInitials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const ini = parts.slice(0, 2).map((p) => p[0].toUpperCase()).join("");
  return ini || "?";
}

function renderUser(user) {
  $("user-chip").classList.remove("hidden");
  setText("user-name", user.name || "Pilot");
  const av = $("user-avatar");
  const img = user.avatar || user.avatar_url || user.image || user.picture || user.photo;
  if (img) {
    av.style.backgroundImage = `url("${img}")`;
    av.classList.add("has-img");
    av.textContent = "";
  } else {
    av.style.backgroundImage = "";
    av.classList.remove("has-img");
    av.textContent = userInitials(user.name);
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

  // Header status dot: live TX while transmitting, else ARMED / OFF.
  const dot = $("ptt-dot");
  if (state.ptt_active) dot.className = "hdot hdot-red";
  else if (state.ptt_set && !capturing) dot.className = "hdot hdot-green";
  else dot.className = "hdot hdot-grey";

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

function chainDotClass(c, state) {
  if (state.actions_recording_id === c.id) return "act-tab-dot amber";
  if (!(c.enabled && c.trigger)) return "act-tab-dot";
  return "act-tab-dot green";
}

let actTabsSig = "";
function renderActionTabs(state) {
  const chains = state.actions_chains || [];
  const sig = JSON.stringify(chains.map((c) => [c.id, c.name, c.enabled, !!c.trigger]))
    + "|" + state.actions_active_id + "|" + state.actions_recording_id;
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
    const dot = document.createElement("span");
    dot.className = chainDotClass(c, state);
    const name = document.createElement("span");
    name.className = "act-tab-name";
    name.textContent = c.name || "Action";
    b.append(dot, name);
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
  const dot = $("act-dot");
  const anyArmed = chains.some((c) => c.enabled && c.trigger);
  if (running) dot.className = "hdot hdot-cyan";
  else if (recording) dot.className = "hdot hdot-amber";
  else if (anyArmed) dot.className = "hdot hdot-green";
  else dot.className = "hdot hdot-grey";

  if (!chain) { actStepsSig = ""; actCapturing = null; return; }

  const capturing = state.actions_capturing && state.actions_capturing_id === chain.id;
  actCapturing = capturing ? state.actions_capturing : null;

  const nameEl = $("act-name");
  if (document.activeElement !== nameEl) nameEl.value = chain.name || "";
  $("act-enabled").checked = !!chain.enabled;

  setText("act-trigger", capturing
    ? (actCapturing === "joy" ? "press a joystick button…" : "press a key or combo…")
    : (chain.trigger ? (chain.trigger_label || "") : "none"));

  // highlight the active trigger tile (hook, or key/joy)
  const hook = chain.trigger_hook;
  const tkind = chain.trigger && chain.trigger.type; // 'hook' | 'keys' | 'joy'
  document.querySelectorAll("#act-trig-grid .trig").forEach((el) => {
    const elHook = el.dataset.hook, elCap = el.dataset.cap;
    let active = false;
    if (elHook) active = tkind === "hook" && hook === elHook;
    else if (elCap === "key") active = tkind === "keys";
    else if (elCap === "joy") active = tkind === "joy";
    el.classList.toggle("active", active && !capturing);
    el.classList.toggle("capturing",
      capturing && ((elCap === "key" && actCapturing === "key") || (elCap === "joy" && actCapturing === "joy")));
  });

  const steps = chain.steps || [];
  $("act-steps-empty").classList.toggle("hidden", steps.length > 0);
  const sig = chain.id + "|" + JSON.stringify(steps);
  if (sig !== actStepsSig) {
    actStepsSig = sig;
    const list = $("act-steps");
    list.innerHTML = "";
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "act-step";
      li.innerHTML =
        `<span class="step-no">${i + 1}</span>` +
        `<span class="step-ico">${svg(STEP_ICON[s.type] || "clock")}</span>` +
        `<span class="step-txt">${escapeHtml(stepLabel(s))}</span>` +
        `<button class="act-del" data-i="${i}" title="Remove step">${svg("trash")}</button>`;
      list.appendChild(li);
    });
  }

  const recordingThis = state.actions_recording_id === chain.id;
  setText("act-record-lbl", recordingThis ? "Stop" : "Record");
  $("act-record").classList.toggle("recording", recordingThis);
  $("act-run").textContent = running ? "Stop" : "Run now";
  $("act-clear").classList.toggle("hidden", steps.length === 0);
}

// ---- main render -----------------------------------------------------------

function render(state) {
  // pairing code (login view)
  setText("code-digits", state.token || "······");

  // connection pill / user chip + view switch
  const connPill = $("conn-pill");
  if (state.connected && state.user) {
    connPill.classList.add("hidden");
    renderUser(state.user);
    showView(true);
    renderQr(state);
    // simulator / source status (shown by the stream row below, no header chip)
    const active = state.source_id && state.source_id !== "none";
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
    phaseTag.style.color = active ? "var(--cyan-bright)" : "";
    updatePlane(state.flight_progress || 0, state.flight_phase || "Parked");

    renderPtt(state);
    renderActions(state);
    const autostart = $("autostart-toggle");
    if (autostart && document.activeElement !== autostart) {
      autostart.checked = !!state.autostart_enabled;
    }
  } else {
    connPill.textContent = "Not linked";
    connPill.className = "pill pill-muted";
    $("user-chip").classList.add("hidden");
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
  $("autostart-toggle").addEventListener("change", (e) => api().set_autostart(e.target.checked));
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

  // custom simulator dropdown
  $("sim-btn").addEventListener("click", (e) => { e.stopPropagation(); setSimMenu(!simMenuOpen); });
  $("sim-menu").addEventListener("click", (e) => {
    const opt = e.target.closest(".cselect-opt");
    if (!opt) return;
    if (opt.dataset.available === "0") return;     // coming soon → not selectable
    setSimMenu(false);
    if (opt.dataset.id !== undefined) { simsSig = ""; api().set_source(opt.dataset.id); }
  });
  document.addEventListener("click", (e) => {
    if (simMenuOpen && !e.target.closest("#sim-select")) setSimMenu(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && simMenuOpen) setSimMenu(false); });
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
  // trigger picker grid: hooks toggle, key/joy arm capture
  $("act-trig-grid").addEventListener("click", (e) => {
    const t = e.target.closest(".trig");
    if (!t || !actActiveId) return;
    if (t.dataset.hook) {
      if (t.classList.contains("active")) api().actions_clear_trigger(actActiveId);  // click active → clear
      else api().actions_set_trigger_hook(actActiveId, t.dataset.hook);
      actTabsSig = "";
    } else if (t.dataset.cap) {
      const kind = t.dataset.cap;
      if (actCapturing === kind) api().actions_cancel_capture();
      else api().actions_capture_trigger(actActiveId, kind);
    }
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
    const del = e.target.closest(".act-del");
    if (actActiveId && del) {
      api().actions_remove_step(actActiveId, parseInt(del.dataset.i, 10)); actStepsSig = "";
    }
  });
}

function initStaticIcons() {
  document.querySelectorAll("#act-trig-grid .trig").forEach((t) => {
    const key = t.dataset.hook || t.dataset.cap;
    const holder = t.querySelector(".trig-ico");
    if (holder && TRIG_ICON[key]) holder.innerHTML = svg(TRIG_ICON[key]);
  });
  const tile = (id, name) => { const el = $(id)?.querySelector(".tile-ico"); if (el) el.innerHTML = svg(name); };
  tile("act-add-wait", "clock");
  tile("act-add-click", "mouse");
  tile("act-record", "record");
  const del = $("act-delete"); if (del) del.innerHTML = svg("trash");
}

window.addEventListener("pywebviewready", () => { apiReady = true; });

document.addEventListener("DOMContentLoaded", () => {
  initProfile();
  initStaticIcons();
  wireEvents();
  if (window.pywebview && window.pywebview.api) apiReady = true;
  setInterval(tick, 300);
});
