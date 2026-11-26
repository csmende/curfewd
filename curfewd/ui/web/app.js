"use strict";

/* ----------------- In-Process WebKitGTK RPC Bridge ----------------- */

let _rpcSeq = 0;
const _rpcPending = new Map();

function rpc(action, params = {}) {
  return new Promise((resolve, reject) => {
    const id = String(++_rpcSeq);
    _rpcPending.set(id, { resolve, reject });
    const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.curfewd;
    if (!handler) {
      _rpcPending.delete(id);
      reject(new Error("Desktop bridge unavailable"));
      return;
    }
    handler.postMessage(JSON.stringify({ id, action, params }));
  });
}

window.__curfewdResolve = function (id, resp) {
  const entry = _rpcPending.get(String(id));
  if (!entry) return;
  _rpcPending.delete(String(id));
  if (resp && resp.ok) {
    entry.resolve(resp.data);
  } else {
    const err = new Error((resp && resp.message) || "Request failed");
    err.code = (resp && resp.code) || "error";
    entry.reject(err);
  }
};

window.__curfewdEvent = function (eventName, payload) {
  if (eventName === "user_status" && payload && payload.username && payload.status) {
    applyLiveStatusPush(payload.username, payload.status);
  }
};

/* ----------------- Constants & Icons ----------------- */

const ICONS = {
  brand: `<svg width="34" height="34" viewBox="0 0 40 40" fill="none"><rect width="40" height="40" rx="12" fill="url(#cg)"/><path d="M20 7.5L30.5 11.8V20.2C30.5 26.2 26 30.8 20 32.8C14 30.8 9.5 26.2 9.5 20.2V11.8L20 7.5Z" fill="rgba(255,255,255,.16)" stroke="rgba(255,255,255,.92)" stroke-width="1.9"/><path d="M22.9 14.3A5.8 5.8 0 1 0 26.2 24.4A4.9 4.9 0 0 1 22.9 14.3Z" fill="#fff"/><defs><linearGradient id="cg" x1="0" y1="0" x2="40" y2="40"><stop stop-color="#3f51b5"/><stop offset="1" stop-color="#6c63ff"/></linearGradient></defs></svg>`,
  overview: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  schedule: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>`,
  apps: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4M8 10v4M15 11h.01M18 13h.01"/></svg>`,
  doctor: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>`,
  settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.85 1 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  lock: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`,
  plus: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  warn: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  shieldKey: `<svg width="56" height="56" viewBox="0 0 48 48" fill="none"><rect width="48" height="48" rx="16" fill="var(--pc)"/><path d="M24 10L36 15V24C36 31.2 30.8 36.8 24 39C17.2 36.8 12 31.2 12 24V15L24 10Z" stroke="var(--p)" stroke-width="2.4" fill="none"/><circle cx="24" cy="22" r="2.6" fill="var(--p)"/><path d="M24 24.5V29" stroke="var(--p)" stroke-width="2.4" stroke-linecap="round"/></svg>`,
  trash: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>`,
};

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_FULL = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
const DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const SLIDER_UNLIMITED_VAL = 495; // 0..480 = 0m..8h in 15m steps; 495 = Unlimited (86400s)

const state = {
  surface: "loading",
  nav: "",
  userTab: "overview",
  historySpan: 7,
  bannerDismissed: false,
  saving: false,
  busyFix: false,
  prefs: { theme: "auto", accent: "indigo" },
  version: "",
  currentUser: "",
  errorMsg: "",
  doctor: null,
  config: null,
  users: {},
  histories: {},
  installedApps: [],
  me: null,
  myHistory7: null,
  dirtyPolicies: new Set(),
  dirtyToday: new Set(),
  dirtyConfig: false,
  savedSnapshot: null,
};

/* ----------------- Pure Formatting & Escaping Helpers ----------------- */

function escHtml(val) {
  return String(val == null ? "" : val)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getTodayKey() {
  const jsDay = new Date().getDay(); // 0=Sun..6=Sat
  return DAYS[(jsDay + 6) % 7];
}

function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

function nowHHMM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function greetingPhrase() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function parseHHMM(str) {
  if (!str || typeof str !== "string") return 0;
  const parts = str.split(":");
  if (parts.length !== 2) return 0;
  const h = Number(parts[0]);
  const m = Number(parts[1]);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  return Math.max(0, Math.min(1440, h * 60 + m));
}

function fmtMin(mins) {
  const c = Math.max(0, Math.min(1440, Math.round(mins)));
  return `${String(Math.floor(c / 60)).padStart(2, "0")}:${String(c % 60).padStart(2, "0")}`;
}

function fmtDur(sec) {
  if (sec >= 86400) return "Unlimited";
  if (sec <= 0) return "0m";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h && m ? `${h}h ${m}m` : h ? `${h}h` : `${m}m`;
}

function secToSliderVal(sec) {
  if (sec >= 86400) return SLIDER_UNLIMITED_VAL;
  return Math.max(0, Math.min(480, Math.round(sec / 900) * 15));
}

function sliderValToSec(val) {
  const n = Number(val);
  if (n >= SLIDER_UNLIMITED_VAL) return 86400;
  return Math.max(0, Math.min(480, n)) * 60;
}

function makeInitials(fullName, username) {
  const src = (fullName || username || "?").trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
}

/* ----------------- Daemon Payload Normalization ----------------- */

function extractRange(windows) {
  if (!Array.isArray(windows) || windows.length === 0) {
    return { startMin: 480, endMin: 1260 };
  }
  const s = parseHHMM(windows[0].start);
  const e = parseHHMM(windows[windows.length - 1].end);
  return { startMin: s, endMin: Math.max(s + 15, e) };
}

function checkSameEveryDay(days) {
  const ref = days.mon;
  return DAYS.every(d => days[d].limit === ref.limit && days[d].startMin === ref.startMin && days[d].endMin === ref.endMin);
}

function normalizeUserStatus(st) {
  const pol = st.policy || {};
  const days = {};
  for (const d of DAYS) {
    const lim = Number((pol.daily_limits && pol.daily_limits[d] != null) ? pol.daily_limits[d] : 86400);
    const rng = extractRange(pol.curfew_windows && pol.curfew_windows[d]);
    days[d] = { limit: lim, startMin: rng.startMin, endMin: rng.endMin };
  }
  const todayRng = extractRange(st.today_windows || (pol.curfew_windows && pol.curfew_windows[getTodayKey()]));
  const alStatus = st.app_limits_status || {};
  const alPol = pol.app_limits || {};
  const statusByPat = new Map();
  (alStatus.apps || []).forEach(a => statusByPat.set(a.pattern, a));
  const apps = (alPol.apps || []).map(a => {
    const live = statusByPat.get(a.pattern) || {};
    return {
      pattern: a.pattern,
      label: a.label || a.pattern,
      category: a.category || live.category || "App",
      dailyLimit: Number(a.daily_limit_sec != null ? a.daily_limit_sec : (live.daily_limit_sec || 3600)),
      spentToday: Number(live.spent_today || 0),
      bonusToday: Number(live.bonus_today || 0),
      running: Boolean(live.running),
    };
  });

  const fullName = (st.full_name || st.username || "").trim();
  return {
    username: st.username,
    fullName: fullName || st.username,
    initials: makeInitials(fullName, st.username),
    loggedIn: Boolean(st.logged_in),
    locked: Boolean(st.screen_locked),
    enforcementState: st.enforcement_state || "ok",
    spentToday: Number(st.time_spent_today || 0),
    spentWeek: Number(st.time_spent_week || 0),
    bonusToday: Number(st.bonus_today || 0),
    timeLeftDaemon: Number(st.time_left_today || 0),
    lockoutAction: pol.lockout_action || "lock",
    sameEveryDay: checkSameEveryDay(days),
    days,
    todayLimitSec: Number(st.daily_limit_today != null ? st.daily_limit_today : days[getTodayKey()].limit),
    todayStartMin: todayRng.startMin,
    todayEndMin: todayRng.endMin,
    todayOverrideActive: Boolean(st.today_override && st.today_override.active),
    appLimits: {
      enabled: Boolean(alPol.enabled),
      apps,
    },
    rawPolicy: pol,
  };
}

function buildHistorySeries(histObj, spanDays) {
  const byDate = new Map();
  ((histObj && histObj.daily) || []).forEach(row => {
    byDate.set(row.date, {
      act: Math.round(Number(row.active_sec || 0) / 60),
      pt: Math.round(Number(row.app_limits_sec || 0) / 60),
    });
  });

  const out = [];
  const today = new Date();
  for (let offset = spanDays - 1; offset >= 0; offset--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const entry = byDate.get(iso) || { act: 0, pt: 0 };
    const lbl = offset === 0
      ? "Today"
      : (spanDays <= 7 ? DAY_SHORT[d.getDay()] : `${d.getMonth() + 1}/${d.getDate()}`);
    out.push({ d: lbl, iso, act: entry.act, pt: entry.pt });
  }
  return out;
}

function timeLeftSec(u) {
  if (u.todayLimitSec >= 86400) return 86400;
  if (state.dirtyToday.has(u.username) || state.dirtyPolicies.has(u.username)) {
    return Math.max(0, u.todayLimitSec - u.spentToday);
  }
  return Math.max(0, u.todayLimitSec + (u.bonusToday || 0) - u.spentToday);
}

function todayCurfew(u) {
  return u.todayEndMin >= 1440 ? "None" : fmtMin(u.todayEndMin);
}

function currentUser() {
  if (state.nav.startsWith("user:")) {
    const uname = state.nav.slice(5);
    if (state.users[uname]) return state.users[uname];
  }
  const names = Object.keys(state.users);
  return names.length ? state.users[names[0]] : null;
}

/* ----------------- Theme & Notifications ----------------- */

const _darkMedia = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
if (_darkMedia && _darkMedia.addEventListener) {
  _darkMedia.addEventListener("change", () => {
    if (state.prefs.theme === "auto") applyThemeAndAccent();
  });
}

function resolvedTheme(prefTheme) {
  if (prefTheme === "light" || prefTheme === "dark") return prefTheme;
  return (_darkMedia && _darkMedia.matches) ? "dark" : "light";
}

function applyThemeAndAccent() {
  document.documentElement.dataset.theme = resolvedTheme(state.prefs.theme);
  document.documentElement.dataset.accent = state.prefs.accent || "indigo";
}

function showToast(msg) {
  const el = document.getElementById("snackbar");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.remove("show"), 2800);
}

function openModal(html) {
  document.getElementById("modalCard").innerHTML = html;
  document.getElementById("modalBg").classList.add("open");
}

function closeModal() {
  document.getElementById("modalBg").classList.remove("open");
}

function isDirty() {
  return state.dirtyPolicies.size > 0 || state.dirtyToday.size > 0 || state.dirtyConfig;
}

function updateUnsavedBar() {
  const bar = document.getElementById("unsavedBar");
  const saveBtn = document.getElementById("btnSave");
  bar.classList.toggle("visible", isDirty() && state.surface === "admin");
  saveBtn.disabled = state.saving;
  saveBtn.textContent = state.saving ? "Saving..." : "Save Changes";
}

function snapshotState() {
  state.savedSnapshot = JSON.stringify({ users: state.users, config: state.config });
}

function clearDirty() {
  state.dirtyPolicies.clear();
  state.dirtyToday.clear();
  state.dirtyConfig = false;
  updateUnsavedBar();
}

function discardChanges() {
  if (!state.savedSnapshot) return;
  const restored = JSON.parse(state.savedSnapshot);
  state.users = restored.users;
  state.config = restored.config;
  clearDirty();
  render();
  showToast("Unsaved changes discarded");
}

function serializeUserPolicy(u) {
  const base = Object.assign({}, u.rawPolicy || {});
  base.username = u.username;
  base.lockout_action = u.lockoutAction;
  base.daily_limits = {};
  base.curfew_windows = {};
  for (const d of DAYS) {
    base.daily_limits[d] = u.days[d].limit;
    base.curfew_windows[d] = [{ start: fmtMin(u.days[d].startMin), end: fmtMin(u.days[d].endMin) }];
  }
  delete base.playtime;
  base.app_limits = {
    enabled: Boolean(u.appLimits.enabled),
    override_main: Boolean(base.app_limits && base.app_limits.override_main),
    apps: u.appLimits.apps.map(a => ({
      pattern: a.pattern,
      label: a.label,
      daily_limit_sec: a.dailyLimit,
      category: a.category || "App",
    })),
  };
  return base;
}

async function commitChanges() {
  if (!isDirty() || state.saving) return;
  state.saving = true;
  updateUnsavedBar();

  const policies = {};
  const today_overrides = {};
  state.dirtyPolicies.forEach(uname => {
    const u = state.users[uname];
    if (!u) return;
    policies[uname] = serializeUserPolicy(u);
    if (!state.dirtyToday.has(uname) && u.todayOverrideActive) {
      today_overrides[uname] = { reset: true };
    }
  });

  state.dirtyToday.forEach(uname => {
    const u = state.users[uname];
    if (!u) return;
    today_overrides[uname] = {
      limit_sec: u.todayLimitSec,
      windows: [{ start: fmtMin(u.todayStartMin), end: fmtMin(u.todayEndMin) }],
      reset: false,
    };
  });

  const payload = {
    policies,
    today_overrides,
    daemon_config: state.dirtyConfig ? state.config : null,
  };

  try {
    const res = await rpc("save_changes", payload);
    if (res && res.users) {
      for (const [uname, st] of Object.entries(res.users)) {
        state.users[uname] = normalizeUserStatus(st);
      }
    }
    if (res && res.config) {
      state.config = res.config;
    }
    snapshotState();
    clearDirty();
    render();
    showToast("Changes saved");
  } catch (err) {
    showToast(err.code === "permission_denied" ? "Administrator approval cancelled" : `Could not save: ${err.message}`);
  } finally {
    state.saving = false;
    updateUnsavedBar();
  }
}

/* ----------------- Live D-Bus Status Push ----------------- */

function applyLiveStatusPush(username, rawStatus) {
  if (state.surface === "my_status" && state.me && state.me.username === username) {
    state.me = normalizeUserStatus(rawStatus);
    render();
    return;
  }
  if (state.surface !== "admin" || !state.users[username]) return;

  const fresh = normalizeUserStatus(rawStatus);
  const existing = state.users[username];
  if (state.dirtyPolicies.has(username) || state.dirtyToday.has(username)) {
    // Keep unsaved slider edits intact while refreshing live counters
    existing.loggedIn = fresh.loggedIn;
    existing.locked = fresh.locked;
    existing.spentToday = fresh.spentToday;
    existing.spentWeek = fresh.spentWeek;
    existing.enforcementState = fresh.enforcementState;
    const freshApps = new Map(fresh.appLimits.apps.map(a => [a.pattern, a]));
    existing.appLimits.apps.forEach(a => {
      const fa = freshApps.get(a.pattern);
      if (fa) {
        a.spentToday = fa.spentToday;
        a.running = fa.running;
      }
    });
  } else {
    state.users[username] = fresh;
    snapshotState();
    render();
  }
}

/* ----------------- System Health (Consumer Language Translation) ----------------- */

function translateDoctorChecks(doc) {
  const checks = (doc && doc.checks) || [];
  const byName = new Map(checks.map(c => [c.name, c]));
  const isOk = (name) => (byName.get(name) || {}).status === "ok";

  const svcOk = isOk("systemd_service") || isOk("dbus_service");
  const authOk = isOk("polkit_policy") && isOk("dbus_policy");
  const lockProtectOk = isOk("dconf_lock_policy");
  const gameLockOk = isOk("bin_gdbus") && (isOk("bin_xdotool") || isOk("bin_wmctrl"));

  return [
    {
      name: "Background Service",
      ok: svcOk,
      detail: svcOk ? "Screen-time monitoring service is running normally" : "Screen-time monitoring service is not running",
    },
    {
      name: "Administrator Approval",
      ok: authOk,
      detail: authOk ? "Changes require an administrator password" : "Administrator protection policy files are missing",
    },
    {
      name: "Lock-Screen Protection",
      ok: lockProtectOk,
      fixable: !lockProtectOk,
      detail: lockProtectOk
        ? "Protected — standard users cannot turn off screen locking"
        : "Standard users can currently turn off screen lock in system settings",
    },
    {
      name: "Fullscreen Game Locking",
      ok: gameLockOk,
      detail: gameLockOk
        ? "Ready to lock over fullscreen games and desktop apps"
        : "Optional fullscreen helper tools are not installed yet",
    },
  ];
}

function doctorNeedsLockFix() {
  const checks = translateDoctorChecks(state.doctor);
  return checks.some(c => c.fixable && !c.ok);
}

function doctorIssueCount() {
  return translateDoctorChecks(state.doctor).filter(c => !c.ok).length;
}

/* ----------------- Component Renderers ----------------- */

function renderRing(u, size = 152) {
  const left = timeLeftSec(u);
  const lim = u.todayLimitSec;
  const r = 58;
  const c = 2 * Math.PI * r;
  const pct = lim >= 86400 ? 1 : Math.max(0, Math.min(1, left / Math.max(1, lim)));
  const col = left === 0 ? "var(--err)" : left <= 600 ? "var(--warn)" : "var(--p)";
  const subTxt = lim >= 86400 ? "no daily limit" : `left of ${fmtDur(lim)}`;
  return `<div class="ring-box" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}" viewBox="0 0 148 148">
      <circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--s3)" stroke-width="11"/>
      <circle cx="74" cy="74" r="${r}" fill="none" stroke="${col}" stroke-width="11" stroke-linecap="round"
        stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - pct)).toFixed(1)}" transform="rotate(-90 74 74)"/>
    </svg>
    <div class="ring-center">
      <div class="ring-main" style="color:${col}">${fmtDur(left)}</div>
      <div class="ring-sub">${subTxt}</div>
    </div>
  </div>`;
}

function renderUsageChart(histObj, spanDays) {
  const rows = buildHistorySeries(histObj, spanDays);
  const maxVal = Math.max(60, ...rows.map(r => r.act));
  const w = 640, h = 158, padL = 36, padB = 26, padT = 12;
  const cw = w - padL - 8, ch = h - padB - padT;
  const step = cw / Math.max(1, rows.length);
  const bw = Math.min(34, step * 0.52);

  const bars = rows.map((row, idx) => {
    const x = padL + idx * step + (step - bw) / 2;
    const bh = row.act > 0 ? Math.max(4, (row.act / maxVal) * ch) : 2;
    const y = padT + ch - bh;
    const pth = row.pt > 0 ? Math.min(bh, Math.max(3, (row.pt / maxVal) * ch)) : 0;
    const pty = padT + ch - pth;
    return `<g><title>${escHtml(row.d)}: ${row.act}m total${row.pt > 0 ? ` (${row.pt}m App Limits)` : ""}</title>
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="6" fill="var(--p)" opacity="${row.act > 0 ? ".78" : ".22"}"/>
      ${pth > 0 ? `<rect x="${(x + 3).toFixed(1)}" y="${pty.toFixed(1)}" width="${Math.max(4, bw - 6).toFixed(1)}" height="${pth.toFixed(1)}" rx="4" fill="var(--on-pc)" opacity=".5"/>` : ""}
      <text x="${(x + bw / 2).toFixed(1)}" y="${h - 7}" text-anchor="middle" fill="var(--fg-muted)" font-size="10.5" font-family="var(--font-mono)">${escHtml(row.d)}</text>
    </g>`;
  }).join("");

  return `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line x1="${padL}" y1="${padT}" x2="${w - 8}" y2="${padT}" stroke="var(--out-var)" stroke-dasharray="3 4"/>
    <line x1="${padL}" y1="${padT + ch / 2}" x2="${w - 8}" y2="${padT + ch / 2}" stroke="var(--out-var)" stroke-dasharray="3 4"/>
    <line x1="${padL}" y1="${padT + ch}" x2="${w - 8}" y2="${padT + ch}" stroke="var(--out-var)"/>
    <text x="2" y="${padT + 4}" fill="var(--fg-muted)" font-size="10" font-family="var(--font-mono)">${maxVal}m</text>
    <text x="2" y="${padT + ch / 2 + 4}" fill="var(--fg-muted)" font-size="10" font-family="var(--font-mono)">${Math.round(maxVal / 2)}m</text>
    ${bars}
  </svg>`;
}

function renderWindowBar(key, startMin, endMin, { editable = false, showNow = false, compact = false, showTicks = true } = {}) {
  const sp = (startMin / 1440) * 100;
  const ep = (endMin / 1440) * 100;
  const wp = Math.max(0, ep - sp);
  const nowPct = (nowMinutes() / 1440) * 100;
  const handles = editable ? `
    <button type="button" class="timeline-handle" data-edge="start" style="left:${sp}%" aria-label="Adjust start time">
      <span class="handle-tip">${fmtMin(startMin)}</span>
    </button>
    <button type="button" class="timeline-handle" data-edge="end" style="left:${ep}%" aria-label="Adjust end time">
      <span class="handle-tip">${fmtMin(endMin)}</span>
    </button>` : "";

  return `<div>
    <div class="timeline-bar ${editable ? "editable" : ""} ${compact ? "compact" : ""}" ${editable ? `data-winbar="${escHtml(key)}"` : ""}>
      <div class="timeline-track"><div class="timeline-seg" style="left:${sp}%;width:${wp}%"></div></div>
      ${showNow ? `<div class="timeline-needle" style="left:${nowPct.toFixed(2)}%" title="Current time (${nowHHMM()})"></div>` : ""}
      ${handles}
    </div>
    ${showTicks ? `<div class="timeline-ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>` : ""}
  </div>`;
}

function renderOverviewTab(u) {
  const left = timeLeftSec(u);
  const leftCls = left === 0 ? "err" : left <= 600 ? "warn" : "primary";
  const alertPill = u.locked ? `<span class="pill p-err">Screen Locked</span>`
    : !u.loggedIn ? `<span class="pill p-muted">Offline</span>` : "";

  return `<div class="card">
    <div class="card-hdr">
      <div style="display:flex;align-items:center;gap:10px">
        <h3 class="card-title">Current Status</h3>
        ${alertPill}
      </div>
      <button type="button" class="btn btn-danger btn-sm" id="btnLockModal">${ICONS.lock} Lock Now</button>
    </div>

    <div class="stat-pills">
      <div class="stat-pill ${leftCls}" id="pillLeftBox">
        <span class="stat-pill-lbl">Time Left Today</span>
        <span class="stat-pill-val" id="pillLeftVal">${fmtDur(left)}</span>
      </div>
      <div class="stat-pill">
        <span class="stat-pill-lbl">Spent Today</span>
        <span class="stat-pill-val">${fmtDur(u.spentToday)}</span>
      </div>
      <div class="stat-pill">
        <span class="stat-pill-lbl">Spent This Week</span>
        <span class="stat-pill-val">${fmtDur(u.spentWeek)}</span>
      </div>
    </div>

    <div class="card-section">
      <div class="sec-hdr">
        <h4 class="sec-title">Today's Time and Schedule</h4>
        <span class="card-sub">Now: ${nowHHMM()}</span>
      </div>
      <div class="day-row">
        <div class="day-top">
          <div class="day-hours-wrap">
            <span class="day-hours-lbl">Time allowed today</span>
            <input class="thin-slider" type="range" min="0" max="${SLIDER_UNLIMITED_VAL}" step="15" value="${secToSliderVal(u.todayLimitSec)}" id="todayLimSlider" aria-label="Time allowed today"/>
            <span class="day-hours-val" id="todayLimVal">${fmtDur(u.todayLimitSec)}</span>
          </div>
        </div>
        ${renderWindowBar("today", u.todayStartMin, u.todayEndMin, { editable: true, showNow: true, compact: false, showTicks: true })}
      </div>
    </div>

    <div class="card-section">
      <div class="sec-hdr">
        <h4 class="sec-title">Screen-Time &amp; App Limits History</h4>
        <div class="segmented">
          <button type="button" class="seg-btn ${state.historySpan === 7 ? "active" : ""}" data-hspan="7">7 Days</button>
          <button type="button" class="seg-btn ${state.historySpan === 14 ? "active" : ""}" data-hspan="14">14 Days</button>
        </div>
      </div>
      ${renderUsageChart(state.histories[u.username], state.historySpan)}
    </div>
  </div>`;
}

function renderScheduleTab(u) {
  const renderDayRow = (key, label, dObj, showTicks) => `
    <div class="day-row">
      <div class="day-top">
        <div class="day-name">${escHtml(label)}</div>
        <div class="day-hours-wrap">
          <span class="day-hours-lbl">Daily time limit</span>
          <input class="thin-slider" type="range" min="0" max="${SLIDER_UNLIMITED_VAL}" step="15" value="${secToSliderVal(dObj.limit)}" data-daylim="${escHtml(key)}" aria-label="${escHtml(label)} daily time limit"/>
          <span class="day-hours-val" data-daylimval="${escHtml(key)}">${fmtDur(dObj.limit)}</span>
        </div>
      </div>
      ${renderWindowBar(key, dObj.startMin, dObj.endMin, { editable: true, showNow: false, compact: true, showTicks })}
    </div>`;

  const rowsHtml = u.sameEveryDay
    ? renderDayRow("all", "Every Day", u.days.mon, true)
    : DAYS.map((d, i) => renderDayRow(d, DAY_FULL[d], u.days[d], i === DAYS.length - 1)).join("");

  return `<div class="card">
    <div class="card-hdr">
      <div><h3 class="card-title">Weekly Schedule</h3><div class="card-sub">Changes stay queued until you click Save so active users aren't interrupted mid-edit</div></div>
      <label class="m3-check">
        <input type="checkbox" id="chkSameAll" ${u.sameEveryDay ? "checked" : ""}/>
        <span>Same schedule every day</span>
      </label>
    </div>
    <div class="day-stack">
      ${rowsHtml}
    </div>
  </div>
  <div class="card">
    <div class="card-hdr"><h3 class="card-title">When Time Expires or Curfew Hits</h3><span class="card-sub">Open apps stay safe when locking the screen</span></div>
    <div class="lockout-grid">
      ${[
        ["lock", "Lock Screen", "Locks the desktop without closing open games or homework"],
        ["suspend", "Lock & Suspend PC", "Locks the screen and puts the computer to sleep"],
        ["terminate", "Log Out Session", "Closes open apps and ends the session"],
      ].map(([v, t, d]) => `<button type="button" class="lockout-opt ${u.lockoutAction === v ? "active" : ""}" data-lockout="${v}">
        <div style="font-weight:600;margin-bottom:3px">${t}</div>
        <div style="font-size:12px;opacity:.85">${d}</div>
      </button>`).join("")}
    </div>
  </div>`;
}

function renderAppLimitsTab(u) {
  const al = u.appLimits;
  return `<div class="card">
    <div class="card-hdr">
      <div><h3 class="card-title">App Limits</h3><div class="card-sub">Set an individual daily time limit for each app or game — when one hits 0m, only that app closes</div></div>
      <div style="display:flex;align-items:center;gap:10px">
        <span class="card-sub">${al.enabled ? "Active" : "Disabled"}</span>
        <button type="button" class="m3-switch ${al.enabled ? "on" : ""}" id="toggleApps" aria-label="Toggle App Limits"></button>
      </div>
    </div>
    <div class="card-hdr" style="margin-bottom:12px">
      <h4 class="card-title" style="font-size:14px">Tracked Applications (${al.apps.length})</h4>
      <button type="button" class="btn btn-tonal btn-sm" id="btnAddApp">${ICONS.plus} Add App / Game</button>
    </div>
    <div class="app-list" style="${al.enabled ? "" : "opacity:.55"}">
      ${al.apps.length ? al.apps.map((a, idx) => {
        const allotted = Math.max(0, a.dailyLimit + (a.bonusToday || 0));
        const rem = Math.max(0, allotted - a.spentToday);
        const pct = Math.min(100, Math.round((a.spentToday / Math.max(1, allotted)) * 100));
        const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
        const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
        return `<div class="app-card">
          <div class="app-top">
            <div>
              <div style="font-weight:600;display:flex;align-items:center;gap:6px">${escHtml(a.label)} ${a.running ? `<span class="pill p-info">Running</span>` : ""}</div>
              <div class="card-sub" style="font-size:12px">${escHtml(a.category || "App")}</div>
            </div>
            <span class="pill ${pCls}">${rem === 0 ? "Limit reached" : `${fmtDur(rem)} left`}</span>
          </div>
          <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
          <div class="app-meta">
            <span>Allotted: <strong>${fmtDur(allotted)}</strong>${a.bonusToday ? ` <span style="color:var(--ok)">(+${fmtDur(a.bonusToday)} bonus)</span>` : ""}</span>
            <span>Used: <strong>${fmtDur(a.spentToday)}</strong> · Left: <strong>${fmtDur(rem)}</strong></span>
          </div>
          <div class="app-ctrl">
            <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
              <span class="card-sub" style="white-space:nowrap">Daily limit</span>
              <input class="slider" style="width:100%;max-width:130px" type="range" min="15" max="240" step="15" value="${Math.max(15, Math.min(240, Math.round(a.dailyLimit / 60)))}" data-applimit="${idx}" ${al.enabled ? "" : "disabled"}/>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <button type="button" class="btn btn-tonal btn-sm" data-appgrant="${idx}" ${al.enabled ? "" : "disabled"}>+15m</button>
              <button type="button" class="btn btn-outlined btn-sm" data-rmapp="${idx}" title="Remove">${ICONS.trash}</button>
            </div>
          </div>
        </div>`;
      }).join("") : `<div class="card-sub">No limited apps configured yet. Click "Add App / Game" to pick an installed app and set its daily limit.</div>`}
    </div>
  </div>`;
}

function renderDoctorView() {
  const checks = translateDoctorChecks(state.doctor);
  const needsFix = doctorNeedsLockFix();
  const allOk = checks.every(c => c.ok);
  return `<div class="page-hdr">
    <div><h1 class="page-title">System Health</h1><div class="page-sub">Quick checks to make sure limits and screen locking work reliably</div></div>
    ${allOk ? `<span class="pill p-ok">All Checks Passing</span>`
      : needsFix ? `<button type="button" class="btn btn-filled" id="btnDocFix" ${state.busyFix ? "disabled" : ""}>${ICONS.doctor} ${state.busyFix ? "Protecting..." : "Protect Lock Screen"}</button>`
      : `<button type="button" class="btn btn-outlined" id="btnDocRefresh">Re-check</button>`}
  </div>
  <div class="card"><div class="check-list">${checks.map(c => `<div class="check-row">
    <div><div style="font-weight:600;font-size:13.5px">${escHtml(c.name)}</div><div class="card-sub">${escHtml(c.detail)}</div></div>
    <span class="pill ${c.ok ? "p-ok" : "p-warn"}">${c.ok ? "OK" : "Action Needed"}</span>
  </div>`).join("")}</div></div>`;
}

function renderSettingsView() {
  const cfg = state.config || { session_lock_fallback: true, release_x11_grab_on_lock: true, final_countdown_sec: 15 };
  const curTheme = state.prefs.theme || "auto";
  const curAcc = state.prefs.accent || "indigo";

  return `<div class="page-hdr"><div><h1 class="page-title">Settings</h1><div class="page-sub">Screen-lock behavior and appearance preferences</div></div></div>
  <div class="grid-eq">
    <div class="card" style="display:flex;flex-direction:column;gap:14px">
      <h3 class="card-title">Screen Locking</h3>
      <div class="rule-row">
        <div><strong>Enhanced Screen Lock</strong><div class="card-sub">Uses extra fallback checks so the lock screen always engages</div></div>
        <button type="button" class="m3-switch ${cfg.session_lock_fallback ? "on" : ""}" data-cbool="session_lock_fallback" aria-label="Enhanced Screen Lock"></button>
      </div>
      <div class="rule-row">
        <div><strong>Fullscreen Game Compatibility</strong><div class="card-sub">Safely releases fullscreen games before locking without closing them</div></div>
        <button type="button" class="m3-switch ${cfg.release_x11_grab_on_lock ? "on" : ""}" data-cbool="release_x11_grab_on_lock" aria-label="Fullscreen Game Compatibility"></button>
      </div>
      <div class="rule-row">
        <div><strong>Final Warning Countdown</strong><div class="card-sub">Countdown alert shown right before the screen locks</div></div>
        <div style="display:flex;align-items:center;gap:10px">
          <input class="thin-slider" style="width:110px" type="range" min="0" max="60" step="5" value="${Number(cfg.final_countdown_sec || 15)}" id="cfgCountdownSlider" aria-label="Final Warning Countdown"/>
          <span class="pill p-info" id="cfgCountdownVal">${Number(cfg.final_countdown_sec || 15)}s</span>
        </div>
      </div>
    </div>
    <div class="card" style="display:flex;flex-direction:column;gap:14px">
      <h3 class="card-title">Appearance</h3>
      <div class="rule-row">
        <div><strong>Color Mode</strong><div class="card-sub">Follows your system Light/Dark preference by default</div></div>
        <div class="segmented">
          <button type="button" class="seg-btn ${curTheme === "auto" ? "active" : ""}" data-settheme="auto">Auto</button>
          <button type="button" class="seg-btn ${curTheme === "light" ? "active" : ""}" data-settheme="light">Light</button>
          <button type="button" class="seg-btn ${curTheme === "dark" ? "active" : ""}" data-settheme="dark">Dark</button>
        </div>
      </div>
      <div class="rule-row">
        <div><strong>Accent Color</strong><div class="card-sub">Choose your preferred highlight color</div></div>
        <div style="display:flex;gap:8px">
          ${[["indigo", "#536dfe"], ["emerald", "#158f5e"], ["slate", "#3870a8"], ["amber", "#c27b0e"]].map(([k, hx]) =>
            `<button type="button" class="swatch ${curAcc === k ? "active" : ""}" data-accent="${k}" title="${k}" style="background:${hx}"></button>`
          ).join("")}
        </div>
      </div>
    </div>
  </div>`;
}

function renderAdminShell() {
  const u = currentUser();
  const userList = Object.values(state.users);
  const issues = doctorIssueCount();
  const needsLockFix = doctorNeedsLockFix();

  const items = userList.map(usr => {
    const active = state.nav === `user:${usr.username}`;
    const left = timeLeftSec(usr);
    const dot = !usr.loggedIn ? "dot-off" : usr.locked ? "dot-lock" : left <= 600 ? "dot-warn" : "dot-ok";
    const rt = usr.locked ? "Locked" : fmtDur(left);
    return `<button type="button" class="nav-item ${active ? "active" : ""}" data-nav="user:${escHtml(usr.username)}">
      <div class="avatar">${escHtml(usr.initials)}<span class="sdot ${dot}"></span></div>
      <div class="nav-meta">
        <div class="nav-uname"><span>${escHtml(usr.fullName)}</span><span style="font-size:12px;font-family:var(--font-mono)" data-navleft="${escHtml(usr.username)}">${rt}</span></div>
        <div class="nav-usub">@${escHtml(usr.username)} · ${usr.loggedIn ? "Signed in" : "Offline"}</div>
      </div>
    </button>`;
  }).join("");

  let content = "";
  if (state.nav === "doctor") {
    content = renderDoctorView();
  } else if (state.nav === "settings") {
    content = renderSettingsView();
  } else if (!u) {
    content = `<div class="card"><h3 class="card-title">No Users Found</h3><div class="card-sub">No desktop user accounts were detected on this computer.</div></div>`;
  } else {
    content = `
      ${needsLockFix && !state.bannerDismissed ? `<div class="doctor-banner">
        <div class="doctor-left">${ICONS.warn}<div><div style="font-weight:700;font-size:13.5px">Lock-screen protection needed</div><div style="font-size:12.5px;opacity:.9">Standard users can currently turn off screen locking in system settings. Protect this setting with one click.</div></div></div>
        <div style="display:flex;gap:8px;flex-shrink:0"><button type="button" class="btn btn-filled btn-sm" id="btnBanFix" ${state.busyFix ? "disabled" : ""}>${state.busyFix ? "Protecting..." : "Protect Now"}</button><button type="button" class="btn btn-outlined btn-sm" id="btnBanDismiss">Dismiss</button></div>
      </div>` : ""}
      <div class="page-hdr"><div>
        <h1 class="page-title">${escHtml(u.fullName)} <span style="font-weight:500;font-size:16px;color:var(--fg-muted)">@${escHtml(u.username)}</span></h1>
        <div class="page-sub">Daily allowance <span id="hdrLimit">${fmtDur(u.todayLimitSec)}</span> · Curfew <span id="hdrCurfew">${todayCurfew(u)}</span></div>
      </div></div>
      <div class="m3-tabs">
        <button type="button" class="m3-tab ${state.userTab === "overview" ? "active" : ""}" data-utab="overview">${ICONS.overview} Overview</button>
        <button type="button" class="m3-tab ${state.userTab === "schedule" ? "active" : ""}" data-utab="schedule">${ICONS.schedule} Schedule</button>
        <button type="button" class="m3-tab ${state.userTab === "apps" ? "active" : ""}" data-utab="apps">${ICONS.apps} App Limits</button>
      </div>
      ${state.userTab === "overview" ? renderOverviewTab(u) : state.userTab === "schedule" ? renderScheduleTab(u) : renderAppLimitsTab(u)}`;
  }

  return `<div class="app-shell">
    <aside class="nav-drawer">
      <div class="brand-row">${ICONS.brand}<div><div class="brand-title">curfewd</div><div class="brand-sub">Screen Time Control</div></div></div>
      <div><div class="nav-sec">Users</div><div class="nav-list">${items}</div></div>
      <div class="nav-spacer"></div><div class="nav-div"></div>
      <div class="nav-list">
        <button type="button" class="nav-item ${state.nav === "doctor" ? "active" : ""}" data-nav="doctor">${ICONS.doctor}<span style="flex:1">System Health</span><span class="pill ${issues === 0 ? "p-ok" : "p-warn"}">${issues === 0 ? "OK" : issues}</span></button>
        <button type="button" class="nav-item ${state.nav === "settings" ? "active" : ""}" data-nav="settings">${ICONS.settings}<span>Settings</span></button>
      </div>
    </aside>
    <main class="main-pane">${content}</main>
  </div>`;
}

function renderMyScreenTime() {
  const u = state.me;
  if (!u) return renderUnavailable("Unable to load your screen-time status.");
  const firstName = (u.fullName || u.username).split(/\s+/)[0];
  const appRows = u.appLimits.enabled && u.appLimits.apps.length ? `<div>
    <div class="card-sub" style="font-weight:600;margin-bottom:6px">My App Limits</div>
    <div class="mini-app-list">${u.appLimits.apps.map(a => {
      const allotted = Math.max(0, a.dailyLimit + (a.bonusToday || 0));
      const rem = Math.max(0, allotted - a.spentToday);
      const pct = Math.min(100, Math.round((a.spentToday / Math.max(1, allotted)) * 100));
      const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
      return `<div class="mini-app-row">
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px"><strong>${escHtml(a.label)}</strong><span style="font-family:var(--font-mono);font-size:12px"><strong>${fmtDur(rem)}</strong> left of ${fmtDur(allotted)}</span></div>
        <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
      </div>`;
    }).join("")}</div></div>` : "";

  const curfewTxt = todayCurfew(u);
  const subInfo = curfewTxt === "None" && u.todayLimitSec >= 86400
    ? "No screen-time limits are currently active for your account."
    : `Your screen locks automatically at <strong>${curfewTxt}</strong> or when your daily allowance reaches 0m. Open apps stay saved!`;

  return `<div class="centered-view"><div class="my-card">
    <div style="display:flex;align-items:center;gap:10px">${ICONS.brand}<div><div style="font-size:17px;font-weight:700">My Screen Time</div><div class="card-sub">Signed in as @${escHtml(u.username)}</div></div></div>
    <div class="hero-wrap" style="justify-content:center;padding:4px 0">
      ${renderRing(u, 152)}
      <div class="hero-stats">
        <div style="font-size:15px;font-weight:600">${greetingPhrase()}, ${escHtml(firstName)}</div>
        <div class="card-sub">${subInfo}</div>
        <div class="metric-grid">
          <div class="metric-box"><div class="metric-lbl">Used Today</div><div class="metric-val">${fmtDur(u.spentToday)}</div></div>
          <div class="metric-box"><div class="metric-lbl">Curfew</div><div class="metric-val">${curfewTxt}</div></div>
        </div>
      </div>
    </div>
    <div><div class="card-sub" style="font-weight:600;margin-bottom:6px">Today's Allowed Hours</div>${renderWindowBar("my", u.todayStartMin, u.todayEndMin, { editable: false, showNow: true })}</div>
    ${appRows}
    <div><div class="card-sub" style="font-weight:600;margin-bottom:4px">My Past 7 Days</div>${renderUsageChart(state.myHistory7, 7)}</div>
  </div></div>`;
}

function renderAuthGate() {
  return `<div class="centered-view"><div class="auth-card">
    ${ICONS.shieldKey}
    <h2 style="margin:4px 0 4px;font-size:20px">Authentication Required</h2>
    <div style="display:flex;flex-direction:column;gap:8px;width:100%;margin-top:6px">
      <button type="button" class="btn btn-filled" id="btnAuth">Authenticate as Administrator</button>
      <button type="button" class="btn btn-outlined" id="btnSelf">View My Own Screen Time (No Password)</button>
    </div>
  </div></div>`;
}

function renderUnavailable(msg) {
  return `<div class="centered-view"><div class="auth-card">
    ${ICONS.shieldKey}
    <h2 style="margin:4px 0 4px;font-size:20px">Service Unavailable</h2>
    <div class="card-sub">${escHtml(msg || "The screen-time service is not currently running on this computer.")}</div>
    <div style="display:flex;flex-direction:column;gap:8px;width:100%;margin-top:6px">
      <button type="button" class="btn btn-filled" id="btnRetry">Retry Connection</button>
    </div>
  </div></div>`;
}

/* ----------------- Main Render & Event Wiring ----------------- */

function render() {
  applyThemeAndAccent();
  const root = document.getElementById("root");
  if (state.surface === "admin") {
    root.innerHTML = renderAdminShell();
  } else if (state.surface === "my_status") {
    root.innerHTML = renderMyScreenTime();
  } else if (state.surface === "auth") {
    root.innerHTML = renderAuthGate();
  } else if (state.surface === "unavailable") {
    root.innerHTML = renderUnavailable(state.errorMsg);
  }
  updateUnsavedBar();
  wireEvents();
}

function openAddAppModal(u) {
  const allApps = state.installedApps || [];
  let filterText = "";

  const drawModal = () => {
    const q = filterText.trim().toLowerCase();
    const filtered = allApps.filter(a => !q || a.label.toLowerCase().includes(q) || a.category.toLowerCase().includes(q));
    openModal(`<h3 style="margin:0">Add App / Game Limit</h3>
      <div class="card-sub">Choose a daily time limit and pick an installed app.</div>
      <div class="rule-row">
        <label class="card-sub" for="newAppLim">Daily time limit</label>
        <select class="text-in" id="newAppLim" style="width:auto">
          <option value="900">15m / day</option>
          <option value="1800">30m / day</option>
          <option value="2700" selected>45m / day</option>
          <option value="3600">1h / day</option>
          <option value="5400">1h 30m / day</option>
          <option value="7200">2h / day</option>
        </select>
      </div>
      <input class="text-in" id="appSearch" type="search" placeholder="Search installed apps..." value="${escHtml(filterText)}"/>
      <div id="appPickList" style="display:flex;flex-direction:column;gap:6px;max-height:210px;overflow-y:auto">
        ${filtered.length ? filtered.map((a, idx) => `<button type="button" class="pick-row" data-pickpat="${escHtml(a.pattern)}" data-pickidx="${idx}">
          <span><strong>${escHtml(a.label)}</strong></span>
          <span class="pick-cat">${escHtml(a.category || "App")}</span>
        </button>`).join("") : `<div class="card-sub" style="padding:8px 2px">No matching installed apps found.</div>`}
      </div>
      <div style="border-top:1px solid var(--out-var);padding-top:10px;display:flex;gap:8px;align-items:center">
        <input class="text-in" id="customAppName" type="text" placeholder="Or enter an app name (e.g. minecraft)"/>
        <button type="button" class="btn btn-tonal btn-sm" id="btnAddCustomApp">Add</button>
      </div>
      <div class="modal-acts"><button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Close</button></div>`);

    const limSel = document.getElementById("newAppLim");
    const searchIn = document.getElementById("appSearch");
    if (searchIn) {
      searchIn.oninput = () => {
        filterText = searchIn.value;
        const pos = searchIn.selectionStart;
        drawModal();
        const next = document.getElementById("appSearch");
        if (next) {
          next.focus();
          if (pos != null) next.setSelectionRange(pos, pos);
        }
      };
    }

    const addRule = (pattern, label, category) => {
      const lim = Number((limSel && limSel.value) || 2700);
      const cleanPat = String(pattern || "").trim().toLowerCase();
      if (!cleanPat) return;
      if (!u.appLimits.apps.some(x => x.pattern.toLowerCase() === cleanPat)) {
        u.appLimits.apps.push({
          pattern: cleanPat,
          label: label || pattern,
          category: category || "App",
          dailyLimit: lim,
          spentToday: 0,
          bonusToday: 0,
          running: false,
        });
        u.appLimits.enabled = true;
        state.dirtyPolicies.add(u.username);
        updateUnsavedBar();
      }
      closeModal();
      render();
      showToast(`Added ${label || pattern} (${fmtDur(lim)}/day)`);
    };

    document.querySelectorAll("[data-pickpat]").forEach(btn => {
      btn.onclick = () => {
        const pat = btn.dataset.pickpat;
        const found = allApps.find(x => x.pattern === pat);
        if (found) addRule(found.pattern, found.label, found.category);
      };
    });

    const btnCustom = document.getElementById("btnAddCustomApp");
    if (btnCustom) {
      btnCustom.onclick = () => {
        const raw = (document.getElementById("customAppName").value || "").trim();
        if (!raw) return;
        const pat = raw.toLowerCase().replace(/[^a-z0-9._|-]/g, "");
        if (!pat) return;
        addRule(pat, raw, "App");
      };
    }

    const btnClose = document.getElementById("btnCloseModal");
    if (btnClose) btnClose.onclick = closeModal;
  };

  drawModal();
}

function wireEvents() {
  document.querySelectorAll("[data-nav]").forEach(b => {
    b.onclick = () => { state.nav = b.dataset.nav; render(); };
  });
  document.querySelectorAll("[data-utab]").forEach(b => {
    b.onclick = () => { state.userTab = b.dataset.utab; render(); };
  });
  document.querySelectorAll("[data-hspan]").forEach(b => {
    b.onclick = () => { state.historySpan = Number(b.dataset.hspan); render(); };
  });

  const btnAuth = document.getElementById("btnAuth");
  if (btnAuth) btnAuth.onclick = () => bootstrap("admin");
  const btnSelf = document.getElementById("btnSelf");
  if (btnSelf) btnSelf.onclick = () => bootstrap("my_status");
  const btnRetry = document.getElementById("btnRetry");
  if (btnRetry) btnRetry.onclick = () => bootstrap(initialMode());

  const u = currentUser();

  // Today's Time Allowed thin slider on Current Status
  const tLimSl = document.getElementById("todayLimSlider");
  if (tLimSl && u) {
    tLimSl.oninput = () => {
      u.todayLimitSec = sliderValToSec(tLimSl.value);
      state.dirtyToday.add(u.username);
      const left = timeLeftSec(u);
      const tv = document.getElementById("todayLimVal");
      if (tv) tv.textContent = fmtDur(u.todayLimitSec);
      const pv = document.getElementById("pillLeftVal");
      if (pv) pv.textContent = fmtDur(left);
      const pb = document.getElementById("pillLeftBox");
      if (pb) pb.className = `stat-pill ${left === 0 ? "err" : left <= 600 ? "warn" : "primary"}`;
      const hl = document.getElementById("hdrLimit");
      if (hl) hl.textContent = fmtDur(u.todayLimitSec);
      const nl = document.querySelector(`[data-navleft="${CSS.escape(u.username)}"]`);
      if (nl && !u.locked) nl.textContent = fmtDur(left);
      updateUnsavedBar();
    };
  }

  // Lock Now modal & immediate RPC
  const btnLock = document.getElementById("btnLockModal");
  if (btnLock && u) {
    btnLock.onclick = () => {
      openModal(`<h3 style="margin:0">Lock ${escHtml(u.fullName)}'s screen now?</h3>
        <div class="card-sub">Locks the screen immediately without closing open apps.</div>
        <div class="modal-acts">
          <button type="button" class="btn btn-outlined btn-sm" id="cancelLock">Cancel</button>
          <button type="button" class="btn btn-danger btn-sm" id="okLock">${ICONS.lock} Lock Screen</button>
        </div>`);
      document.getElementById("cancelLock").onclick = closeModal;
      document.getElementById("okLock").onclick = async () => {
        const okBtn = document.getElementById("okLock");
        okBtn.disabled = true;
        try {
          const res = await rpc("lock_user", { username: u.username });
          if (res && res.status) state.users[u.username] = normalizeUserStatus(res.status);
          if (res && res.result && res.result.locked) {
            state.users[u.username].locked = true;
            showToast(`Locked ${u.fullName}'s screen`);
          } else {
            showToast((res && res.result && res.result.detail) || `No active session for ${u.fullName}`);
          }
          closeModal();
          render();
        } catch (err) {
          closeModal();
          showToast(err.code === "permission_denied" ? "Administrator approval cancelled" : `Could not lock: ${err.message}`);
        }
      };
    };
  }

  // Same schedule every day checkbox
  const chkSame = document.getElementById("chkSameAll");
  if (chkSame && u) {
    chkSame.onchange = () => {
      u.sameEveryDay = chkSame.checked;
      if (u.sameEveryDay) {
        const ref = Object.assign({}, u.days.mon);
        DAYS.forEach(d => { u.days[d] = Object.assign({}, ref); });
        if (!state.dirtyToday.has(u.username)) {
          u.todayLimitSec = ref.limit;
          u.todayStartMin = ref.startMin;
          u.todayEndMin = ref.endMin;
        }
        state.dirtyPolicies.add(u.username);
        updateUnsavedBar();
      }
      render();
    };
  }

  // Weekly per-day thin limit sliders
  document.querySelectorAll("[data-daylim]").forEach(sl => {
    if (!u) return;
    const key = sl.dataset.daylim;
    const todayKey = getTodayKey();
    sl.oninput = () => {
      const sec = sliderValToSec(sl.value);
      if (key === "all") {
        DAYS.forEach(d => { u.days[d].limit = sec; });
        if (!state.dirtyToday.has(u.username)) u.todayLimitSec = sec;
      } else {
        u.days[key].limit = sec;
        if (key === todayKey && !state.dirtyToday.has(u.username)) u.todayLimitSec = sec;
      }
      state.dirtyPolicies.add(u.username);
      const valEl = document.querySelector(`[data-daylimval="${key}"]`);
      if (valEl) valEl.textContent = fmtDur(sec);
      const hdrLim = document.getElementById("hdrLimit");
      if (hdrLim) hdrLim.textContent = fmtDur(u.todayLimitSec);
      const nl = document.querySelector(`[data-navleft="${CSS.escape(u.username)}"]`);
      if (nl && !u.locked) nl.textContent = fmtDur(timeLeftSec(u));
      updateUnsavedBar();
    };
  });

  // Draggable 24h allowed-window bars (Overview Today + Schedule per-day / Every Day)
  document.querySelectorAll("[data-winbar]").forEach(bar => {
    if (!u) return;
    const key = bar.dataset.winbar;
    const todayKey = getTodayKey();
    const seg = bar.querySelector(".timeline-seg");
    const hdrCurfew = document.getElementById("hdrCurfew");

    const getRange = () => key === "today"
      ? { startMin: u.todayStartMin, endMin: u.todayEndMin }
      : (key === "all" ? u.days.mon : u.days[key]);

    const setRange = (s, e) => {
      if (key === "today") {
        u.todayStartMin = s;
        u.todayEndMin = e;
        state.dirtyToday.add(u.username);
      } else if (key === "all") {
        DAYS.forEach(d => { u.days[d].startMin = s; u.days[d].endMin = e; });
        if (!state.dirtyToday.has(u.username)) {
          u.todayStartMin = s;
          u.todayEndMin = e;
        }
        state.dirtyPolicies.add(u.username);
      } else {
        u.days[key].startMin = s;
        u.days[key].endMin = e;
        if (key === todayKey && !state.dirtyToday.has(u.username)) {
          u.todayStartMin = s;
          u.todayEndMin = e;
        }
        state.dirtyPolicies.add(u.username);
      }
    };

    const syncUI = () => {
      const { startMin, endMin } = getRange();
      const sp = (startMin / 1440) * 100;
      const ep = (endMin / 1440) * 100;
      seg.style.left = `${sp}%`;
      seg.style.width = `${Math.max(0, ep - sp)}%`;
      const lh = bar.querySelector('[data-edge="start"]');
      const rh = bar.querySelector('[data-edge="end"]');
      lh.style.left = `${sp}%`;
      lh.querySelector(".handle-tip").textContent = fmtMin(startMin);
      rh.style.left = `${ep}%`;
      rh.querySelector(".handle-tip").textContent = fmtMin(endMin);
      if (hdrCurfew) hdrCurfew.textContent = todayCurfew(u);
    };

    const setEdge = (edge, raw) => {
      const sn = Math.round(raw / 15) * 15;
      const cur = getRange();
      const s = edge === "start" ? Math.max(0, Math.min(cur.endMin - 60, sn)) : cur.startMin;
      const e = edge === "end" ? Math.max(cur.startMin + 60, Math.min(1440, sn)) : cur.endMin;
      setRange(s, e);
      syncUI();
      updateUnsavedBar();
    };

    bar.querySelectorAll(".timeline-handle").forEach(h => {
      const edge = h.dataset.edge;
      h.onpointerdown = (e) => {
        e.preventDefault();
        h.setPointerCapture(e.pointerId);
        h.classList.add("dragging");
        const mv = (ev) => {
          const r = bar.getBoundingClientRect();
          setEdge(edge, Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * 1440);
        };
        const up = () => {
          h.classList.remove("dragging");
          h.removeEventListener("pointermove", mv);
          h.removeEventListener("pointerup", up);
          h.removeEventListener("pointercancel", up);
        };
        h.addEventListener("pointermove", mv);
        h.addEventListener("pointerup", up);
        h.addEventListener("pointercancel", up);
      };
      h.onkeydown = (e) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          const cur = getRange();
          setEdge(edge, (edge === "start" ? cur.startMin : cur.endMin) + (e.key === "ArrowRight" ? 15 : -15));
        }
      };
    });
  });

  // Lockout action cards
  document.querySelectorAll("[data-lockout]").forEach(b => {
    if (!u) return;
    b.onclick = () => {
      u.lockoutAction = b.dataset.lockout;
      state.dirtyPolicies.add(u.username);
      render();
    };
  });

  // App Limits controls
  const tog = document.getElementById("toggleApps");
  if (tog && u) {
    tog.onclick = () => {
      u.appLimits.enabled = !u.appLimits.enabled;
      state.dirtyPolicies.add(u.username);
      render();
    };
  }
  document.querySelectorAll("[data-applimit]").forEach(s => {
    if (!u) return;
    s.oninput = () => {
      const app = u.appLimits.apps[Number(s.dataset.applimit)];
      if (!app) return;
      app.dailyLimit = Number(s.value) * 60;
      state.dirtyPolicies.add(u.username);
      render();
    };
  });
  document.querySelectorAll("[data-appgrant]").forEach(b => {
    if (!u) return;
    b.onclick = async () => {
      const app = u.appLimits.apps[Number(b.dataset.appgrant)];
      if (!app) return;
      b.disabled = true;
      try {
        const res = await rpc("grant_app_time", { username: u.username, pattern: app.pattern, delta_seconds: 900 });
        if (res && res.status && !state.dirtyPolicies.has(u.username)) {
          state.users[u.username] = normalizeUserStatus(res.status);
          snapshotState();
        } else {
          app.bonusToday = (app.bonusToday || 0) + 900;
        }
        render();
        showToast(`+15m added for ${app.label} today`);
      } catch (err) {
        b.disabled = false;
        showToast(err.code === "permission_denied" ? "Administrator approval cancelled" : `Could not add time: ${err.message}`);
      }
    };
  });
  document.querySelectorAll("[data-rmapp]").forEach(b => {
    if (!u) return;
    b.onclick = () => {
      u.appLimits.apps.splice(Number(b.dataset.rmapp), 1);
      state.dirtyPolicies.add(u.username);
      render();
    };
  });
  const btnAdd = document.getElementById("btnAddApp");
  if (btnAdd && u) btnAdd.onclick = () => openAddAppModal(u);

  // System Health remediation
  const doFix = async () => {
    if (state.busyFix) return;
    state.busyFix = true;
    render();
    try {
      const res = await rpc("apply_doctor_fix", {});
      if (res && res.doctor) state.doctor = res.doctor;
      showToast("Lock-screen protection enabled");
    } catch (err) {
      showToast(err.code === "permission_denied" ? "Administrator approval cancelled" : `Could not apply protection: ${err.message}`);
    } finally {
      state.busyFix = false;
      render();
    }
  };
  const b1 = document.getElementById("btnBanFix");
  if (b1) b1.onclick = doFix;
  const b2 = document.getElementById("btnDocFix");
  if (b2) b2.onclick = doFix;
  const bRef = document.getElementById("btnDocRefresh");
  if (bRef) {
    bRef.onclick = async () => {
      try {
        state.doctor = await rpc("refresh_doctor", {});
        render();
        showToast("Health checks refreshed");
      } catch (err) {
        showToast(`Could not refresh: ${err.message}`);
      }
    };
  }
  const bd = document.getElementById("btnBanDismiss");
  if (bd) bd.onclick = () => { state.bannerDismissed = true; render(); };

  // Daemon Settings
  document.querySelectorAll("[data-cbool]").forEach(b => {
    b.onclick = () => {
      if (!state.config) return;
      const key = b.dataset.cbool;
      state.config[key] = !state.config[key];
      state.dirtyConfig = true;
      render();
    };
  });
  const cdSl = document.getElementById("cfgCountdownSlider");
  if (cdSl) {
    cdSl.oninput = () => {
      if (!state.config) return;
      state.config.final_countdown_sec = Number(cdSl.value);
      state.dirtyConfig = true;
      const valEl = document.getElementById("cfgCountdownVal");
      if (valEl) valEl.textContent = `${state.config.final_countdown_sec}s`;
      updateUnsavedBar();
    };
  }

  // Appearance Preferences (saved immediately)
  document.querySelectorAll("[data-settheme]").forEach(b => {
    b.onclick = async () => {
      state.prefs.theme = b.dataset.settheme;
      applyThemeAndAccent();
      render();
      try { await rpc("save_prefs", { theme: state.prefs.theme }); } catch (_e) {}
    };
  });
  document.querySelectorAll("[data-accent]").forEach(b => {
    b.onclick = async () => {
      state.prefs.accent = b.dataset.accent;
      applyThemeAndAccent();
      render();
      try { await rpc("save_prefs", { accent: state.prefs.accent }); } catch (_e) {}
    };
  });
}

/* ----------------- Bootstrap ----------------- */

function initialMode() {
  const qs = new URLSearchParams(window.location.search);
  return qs.get("mode") === "my_status" ? "my_status" : "admin";
}

async function bootstrap(mode) {
  try {
    const data = await rpc("bootstrap", { mode });
    state.prefs = data.prefs || state.prefs;
    state.version = data.version || "";
    state.currentUser = data.current_user || "";
    state.surface = data.surface || "unavailable";
    state.errorMsg = data.error || "";

    if (state.surface === "my_status") {
      state.me = data.me ? normalizeUserStatus(data.me) : null;
      state.myHistory7 = data.history7 || null;
    } else if (state.surface === "admin") {
      state.users = {};
      for (const [uname, st] of Object.entries(data.users || {})) {
        state.users[uname] = normalizeUserStatus(st);
      }
      state.histories = data.histories || {};
      state.config = data.config || null;
      state.doctor = data.doctor || null;
      state.installedApps = data.installed_apps || [];
      const names = Object.keys(state.users);
      // Prefer first non-current or configured restricted user so parent lands on a kid account first
      const preferred = names.find(n => n !== state.currentUser) || names[0] || "";
      state.nav = preferred ? `user:${preferred}` : "doctor";
      snapshotState();
      clearDirty();
    }
    render();
  } catch (err) {
    state.surface = "unavailable";
    state.errorMsg = err.message || "Unable to connect to screen-time service.";
    render();
  }
}

document.getElementById("btnSave").onclick = commitChanges;
document.getElementById("btnDiscard").onclick = discardChanges;
document.getElementById("modalBg").onclick = (e) => { if (e.target.id === "modalBg") closeModal(); };

// Refresh the live `Now` needle once per minute
setInterval(() => {
  if (state.surface === "admin" && state.userTab === "overview" && !isDirty()) render();
  else if (state.surface === "my_status") render();
}, 60000);

bootstrap(initialMode());
