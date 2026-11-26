/* Constants, state store, formatting helpers, payload normalization, and persistence (Spec 005). */
(function (UI) {
  "use strict";

  const ICONS = {
    brand: `<svg width="34" height="34" viewBox="0 0 40 40" fill="none"><rect width="40" height="40" rx="12" fill="url(#cg)"/><path d="M20 7.5L30.5 11.8V20.2C30.5 26.2 26 30.8 20 32.8C14 30.8 9.5 26.2 9.5 20.2V11.8L20 7.5Z" fill="rgba(255,255,255,.16)" stroke="rgba(255,255,255,.92)" stroke-width="1.9"/><path d="M22.9 14.3A5.8 5.8 0 1 0 26.2 24.4A4.9 4.9 0 0 1 22.9 14.3Z" fill="#fff"/><defs><linearGradient id="cg" x1="0" y1="0" x2="40" y2="40"><stop stop-color="#3f51b5"/><stop offset="1" stop-color="#6c63ff"/></linearGradient></defs></svg>`,
    overview: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
    schedule: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>`,
    apps: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4M8 10v4M15 11h.01M18 13h.01"/></svg>`,
    doctor: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>`,
    settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l-.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.85 1 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
    lock: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>`,
    plus: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
    warn: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    shieldKey: `<svg width="56" height="56" viewBox="0 0 48 48" fill="none"><rect width="48" height="48" rx="16" fill="var(--pc)"/><path d="M24 10L36 15V24C36 31.2 30.8 36.8 24 39C17.2 36.8 12 31.2 12 24V15L24 10Z" stroke="var(--p)" stroke-width="2.4" fill="none"/><circle cx="24" cy="22" r="2.6" fill="var(--p)"/><path d="M24 24.5V29" stroke="var(--p)" stroke-width="2.4" stroke-linecap="round"/></svg>`,
    trash: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>`,
  };

  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_FULL = {
    mon: "Monday",
    tue: "Tuesday",
    wed: "Wednesday",
    thu: "Thursday",
    fri: "Friday",
    sat: "Saturday",
    sun: "Sunday",
  };
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
    return String(val === null || val === undefined ? "" : val)
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
    return DAYS.every(
      (d) =>
        days[d].limit === ref.limit &&
        days[d].startMin === ref.startMin &&
        days[d].endMin === ref.endMin
    );
  }

  function normalizeUserStatus(st) {
    const pol = st.policy || {};
    const days = {};
    for (const d of DAYS) {
      const rawLim = pol.daily_limits ? pol.daily_limits[d] : undefined;
      const lim = Number(rawLim !== null && rawLim !== undefined ? rawLim : 86400);
      const rng = extractRange(pol.curfew_windows && pol.curfew_windows[d]);
      days[d] = { limit: lim, startMin: rng.startMin, endMin: rng.endMin };
    }
    const todayRng = extractRange(
      st.today_windows || (pol.curfew_windows && pol.curfew_windows[getTodayKey()])
    );
    const alStatus = st.app_limits_status || {};
    const alPol = pol.app_limits || {};
    const statusByPat = new Map();
    (alStatus.apps || []).forEach((a) => statusByPat.set(a.pattern, a));
    const apps = (alPol.apps || []).map((a) => {
      const live = statusByPat.get(a.pattern) || {};
      const rawAppLim = a.daily_limit_sec;
      return {
        pattern: a.pattern,
        label: a.label || a.pattern,
        category: a.category || live.category || "App",
        dailyLimit: Number(
          rawAppLim !== null && rawAppLim !== undefined ? rawAppLim : live.daily_limit_sec || 3600
        ),
        spentToday: Number(live.spent_today || 0),
        bonusToday: Number(live.bonus_today || 0),
        running: Boolean(live.running),
      };
    });

    const fullName = (st.full_name || st.username || "").trim();
    const rawTodayLim = st.daily_limit_today;
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
      todayLimitSec: Number(
        rawTodayLim !== null && rawTodayLim !== undefined ? rawTodayLim : days[getTodayKey()].limit
      ),
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
    ((histObj && histObj.daily) || []).forEach((row) => {
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
      const lbl =
        offset === 0
          ? "Today"
          : spanDays <= 7
            ? DAY_SHORT[d.getDay()]
            : `${d.getMonth() + 1}/${d.getDate()}`;
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

  /* ----------------- Theme, Modal, Toast & Dirty State ----------------- */

  const darkMedia = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  if (darkMedia && darkMedia.addEventListener) {
    darkMedia.addEventListener("change", () => {
      if (state.prefs.theme === "auto") applyThemeAndAccent();
    });
  }

  function resolvedTheme(prefTheme) {
    if (prefTheme === "light" || prefTheme === "dark") return prefTheme;
    return darkMedia && darkMedia.matches ? "dark" : "light";
  }

  function applyThemeAndAccent() {
    document.documentElement.dataset.theme = resolvedTheme(state.prefs.theme);
    document.documentElement.dataset.accent = state.prefs.accent || "indigo";
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = document.getElementById("snackbar");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
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
    UI.render();
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
      base.curfew_windows[d] = [
        { start: fmtMin(u.days[d].startMin), end: fmtMin(u.days[d].endMin) },
      ];
    }
    delete base.playtime;
    base.app_limits = {
      enabled: Boolean(u.appLimits.enabled),
      apps: u.appLimits.apps.map((a) => ({
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
    state.dirtyPolicies.forEach((uname) => {
      const u = state.users[uname];
      if (!u) return;
      policies[uname] = serializeUserPolicy(u);
      if (!state.dirtyToday.has(uname) && u.todayOverrideActive) {
        today_overrides[uname] = { reset: true };
      }
    });

    state.dirtyToday.forEach((uname) => {
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
      const res = await UI.rpc("save_changes", payload);
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
      UI.render();
      showToast("Changes saved");
    } catch (err) {
      showToast(
        err.code === "permission_denied"
          ? "Administrator approval cancelled"
          : `Could not save: ${err.message}`
      );
    } finally {
      state.saving = false;
      updateUnsavedBar();
    }
  }

  /* ----------------- Live D-Bus Status Push ----------------- */

  function applyLiveStatusPush(username, rawStatus) {
    if (state.surface === "my_status" && state.me && state.me.username === username) {
      state.me = normalizeUserStatus(rawStatus);
      UI.render();
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
      const freshApps = new Map(fresh.appLimits.apps.map((a) => [a.pattern, a]));
      existing.appLimits.apps.forEach((a) => {
        const fa = freshApps.get(a.pattern);
        if (fa) {
          a.spentToday = fa.spentToday;
          a.running = fa.running;
        }
      });
    } else {
      state.users[username] = fresh;
      snapshotState();
      UI.render();
    }
  }

  /* ----------------- System Health (Consumer Language Translation) ----------------- */

  function translateDoctorChecks(doc) {
    const checks = (doc && doc.checks) || [];
    const byName = new Map(checks.map((c) => [c.name, c]));
    const isOk = (name) => (byName.get(name) || {}).status === "ok";

    const svcOk = isOk("systemd_service") || isOk("dbus_service");
    const authOk = isOk("polkit_policy") && isOk("dbus_policy");
    const lockProtectOk = isOk("dconf_lock_policy");
    const gameLockOk = isOk("bin_gdbus") && (isOk("bin_xdotool") || isOk("bin_wmctrl"));

    return [
      {
        name: "Background Service",
        ok: svcOk,
        detail: svcOk
          ? "Screen-time monitoring service is running normally"
          : "Screen-time monitoring service is not running",
      },
      {
        name: "Administrator Approval",
        ok: authOk,
        detail: authOk
          ? "Changes require an administrator password"
          : "Administrator protection policy files are missing",
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
    return translateDoctorChecks(state.doctor).some((c) => c.fixable && !c.ok);
  }

  function doctorIssueCount() {
    return translateDoctorChecks(state.doctor).filter((c) => !c.ok).length;
  }

  Object.assign(UI, {
    ICONS,
    DAYS,
    DAY_FULL,
    SLIDER_UNLIMITED_VAL,
    state,
    escHtml,
    getTodayKey,
    nowMinutes,
    nowHHMM,
    greetingPhrase,
    fmtMin,
    fmtDur,
    secToSliderVal,
    sliderValToSec,
    normalizeUserStatus,
    buildHistorySeries,
    timeLeftSec,
    todayCurfew,
    currentUser,
    applyThemeAndAccent,
    showToast,
    openModal,
    closeModal,
    isDirty,
    updateUnsavedBar,
    snapshotState,
    clearDirty,
    discardChanges,
    commitChanges,
    applyLiveStatusPush,
    translateDoctorChecks,
    doctorNeedsLockFix,
    doctorIssueCount,
  });
})((window.CurfewUI = window.CurfewUI || {}));
