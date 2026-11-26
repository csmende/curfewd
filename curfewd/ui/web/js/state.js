/* Constants, state store, formatting, normalization, surgical live DOM updates & persistence (Spec 006). */
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
    pause: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`,
    play: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>`,
    book: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    warn: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    shieldKey: `<svg width="56" height="56" viewBox="0 0 48 48" fill="none"><rect width="48" height="48" rx="16" fill="var(--pc)"/><path d="M24 10L36 15V24C36 31.2 30.8 36.8 24 39C17.2 36.8 12 31.2 12 24V15L24 10Z" stroke="var(--p)" stroke-width="2.4" fill="none"/><circle cx="24" cy="22" r="2.6" fill="var(--p)"/><path d="M24 24.5V29" stroke="var(--p)" stroke-width="2.4" stroke-linecap="round"/></svg>`,
    trash: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>`,
    bell: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`,
    key: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6"/><path d="M15.5 7.5l3 3L22 7l-3-3"/></svg>`,
  };

  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri"];
  const WEEKEND_KEYS = ["sat", "sun"];
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
  const CATEGORIES = ["Gaming", "Social", "Media", "Browser", "Creative", "Productivity", "App"];
  const SLIDER_UNLIMITED_VAL = 495; // 0..480 = 0m..8h in 15m steps; 495 = Unlimited (86400s)

  const state = {
    surface: "loading",
    nav: "",
    userTab: "overview",
    historySpan: 7,
    showOtherAccounts: false,
    showSetupWizard: false,
    wizardInitialized: false,
    wizardKids: new Set(),
    wizardPreset: "middle",
    wizardProtectLock: true,
    bannerDismissed: false,
    childSessionBannerDismissed: false,
    saving: false,
    busyFix: false,
    prefs: { theme: "auto", accent: "indigo", time_format: "auto" },
    version: "",
    currentUser: "",
    errorMsg: "",
    doctor: null,
    config: null,
    users: {},
    histories: {},
    timeRequests: [],
    parentCode: { enabled: false, created_utc: "" },
    parentCodeEnrollment: null,
    installedApps: [],
    userAppsCache: {},
    me: null,
    myHistory7: null,
    myRequests: [],
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
    const jsDay = new Date().getDay();
    return DAYS[(jsDay + 6) % 7];
  }

  function nowMinutes() {
    const d = new Date();
    return d.getHours() * 60 + d.getMinutes();
  }

  function uses12HourClock() {
    const pref = (state.prefs && state.prefs.time_format) || "auto";
    if (pref === "12h") return true;
    if (pref === "24h") return false;
    try {
      const opts = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions();
      return Boolean(opts.hour12);
    } catch {
      return false;
    }
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

  /** Canonical wire HH:MM (always 24h for backend JSON). */
  function fmtMin(mins) {
    const c = Math.max(0, Math.min(1440, Math.round(mins)));
    return `${String(Math.floor(c / 60)).padStart(2, "0")}:${String(c % 60).padStart(2, "0")}`;
  }

  /** Human-facing time formatted per user preference (Auto / 12h AM-PM / 24h). */
  function fmtTimeMin(mins) {
    const c = Math.max(0, Math.min(1440, Math.round(mins)));
    if (!uses12HourClock()) return fmtMin(c);
    if (c === 0 || c === 1440) return "12:00 AM";
    const h24 = Math.floor(c / 60);
    const m = c % 60;
    const suffix = h24 >= 12 ? "PM" : "AM";
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    return m === 0 ? `${h12} ${suffix}` : `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
  }

  function nowHHMM() {
    return fmtTimeMin(nowMinutes());
  }

  function greetingPhrase() {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  }

  function fmtDur(sec) {
    if (sec >= 86400) return "Unlimited";
    if (sec <= 0) return "0m";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (!h && !m) return `${sec}s`;
    return h && m ? `${h}h ${m}m` : h ? `${h}h` : `${m}m`;
  }

  function fmtAppLimit(sec) {
    if (sec <= 0) return "Blocked";
    return fmtDur(sec);
  }

  function fmtIsoClock(isoStr) {
    if (!isoStr || typeof isoStr !== "string") return "";
    try {
      const d = new Date(isoStr);
      if (Number.isNaN(d.getTime())) return isoStr.slice(11, 16);
      return fmtTimeMin(d.getHours() * 60 + d.getMinutes());
    } catch {
      return isoStr.slice(11, 16);
    }
  }

  function fmtRelativeEventTime(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      if (Number.isNaN(d.getTime())) return "";
      const today = new Date();
      const sameDay =
        d.getFullYear() === today.getFullYear() &&
        d.getMonth() === today.getMonth() &&
        d.getDate() === today.getDate();
      const clock = fmtTimeMin(d.getHours() * 60 + d.getMinutes());
      return sameDay ? `Today ${clock}` : `${d.getMonth() + 1}/${d.getDate()} ${clock}`;
    } catch {
      return "";
    }
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

  /* ----------------- Multi-Window Normalization & Schedule Grouping ----------------- */

  function normalizeWindows(rawList) {
    if (!Array.isArray(rawList)) return [{ startMin: 0, endMin: 1440 }];
    if (rawList.length === 0) return [];
    return rawList
      .map((w) => {
        const s = typeof w.startMin === "number" ? w.startMin : parseHHMM(w.start);
        const e = typeof w.endMin === "number" ? w.endMin : parseHHMM(w.end);
        const startMin = Math.max(0, Math.min(1425, s));
        const endMin = Math.min(1440, Math.max(startMin + 15, Math.min(1440, e)));
        return { startMin, endMin };
      })
      .sort((a, b) => a.startMin - b.startMin);
  }

  function cloneWindows(list) {
    return (list || []).map((w) => ({ startMin: w.startMin, endMin: w.endMin }));
  }

  function windowsEqual(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((w, i) => w.startMin === b[i].startMin && w.endMin === b[i].endMin);
  }

  function dayEqual(a, b) {
    return a.limit === b.limit && windowsEqual(a.windows, b.windows);
  }

  function detectScheduleMode(days) {
    if (DAYS.every((d) => dayEqual(days[d], days.mon))) return "same";
    const wdSame = WEEKDAY_KEYS.every((d) => dayEqual(days[d], days.mon));
    const weSame = WEEKEND_KEYS.every((d) => dayEqual(days[d], days.sat));
    if (wdSame && weSame) return "weekday_weekend";
    return "custom";
  }

  function normalizeUserStatus(st, prev = null) {
    const pol = st.policy || {};
    const days = {};
    for (const d of DAYS) {
      const rawLim = pol.daily_limits ? pol.daily_limits[d] : undefined;
      const lim = Number(rawLim !== null && rawLim !== undefined ? rawLim : 86400);
      const rawWins = pol.curfew_windows && pol.curfew_windows[d];
      days[d] = {
        limit: lim,
        windows: normalizeWindows(
          rawWins === undefined ? [{ start: "00:00", end: "24:00" }] : rawWins
        ),
      };
    }
    const todayWins = normalizeWindows(
      st.today_windows !== undefined
        ? st.today_windows
        : (pol.curfew_windows && pol.curfew_windows[getTodayKey()]) || []
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
        weekendLimit:
          a.weekend_limit_sec !== null && a.weekend_limit_sec !== undefined
            ? Number(a.weekend_limit_sec)
            : null,
        effectiveLimit: Number(
          live.effective_limit_sec !== undefined
            ? live.effective_limit_sec
            : rawAppLim !== null && rawAppLim !== undefined
              ? rawAppLim
              : 3600
        ),
        flatpakId: a.flatpak_id || "",
        steamAppId: a.steam_app_id || "",
        alwaysAllowed: Boolean(a.always_allowed),
        foregroundOnly: Boolean(a.foreground_only),
        spentToday: Number(live.spent_today || 0),
        bonusToday: Number(live.bonus_today || 0),
        running: Boolean(live.running),
        blockedByHomework: Boolean(live.blocked_by_homework),
      };
    });

    const catStatusByName = new Map();
    (alStatus.categories || []).forEach((c) => catStatusByName.set(c.category.toLowerCase(), c));
    const categories = (alPol.category_limits || []).map((c) => {
      const live = catStatusByName.get(String(c.category || "").toLowerCase()) || {};
      return {
        category: c.category,
        dailyLimit: Number(c.daily_limit_sec !== undefined ? c.daily_limit_sec : 3600),
        weekendLimit:
          c.weekend_limit_sec !== null && c.weekend_limit_sec !== undefined
            ? Number(c.weekend_limit_sec)
            : null,
        effectiveLimit: Number(
          live.effective_limit_sec !== undefined ? live.effective_limit_sec : c.daily_limit_sec
        ),
        spentToday: Number(live.spent_today || 0),
        bonusToday: Number(live.bonus_today || 0),
        running: Boolean(live.running),
      };
    });

    const fullName = (st.full_name || st.username || "").trim();
    const rawTodayLim = st.daily_limit_today;
    const schedMode = detectScheduleMode(days);
    return {
      username: st.username,
      fullName: fullName || st.username,
      initials: makeInitials(fullName, st.username),
      role: st.role || pol.role || "child",
      loggedIn: Boolean(st.logged_in),
      locked: Boolean(st.screen_locked),
      idlePaused: Boolean(st.idle_paused),
      enforcementState: st.enforcement_state || "ok",
      spentToday: Number(st.time_spent_today || 0),
      spentWeek: Number(st.time_spent_week || 0),
      spentMonth: Number(st.time_spent_month || 0),
      bonusToday: Number(st.bonus_today || 0),
      lastBonusNote: st.last_bonus_note || "",
      timeLeftDaemon: Number(st.time_left_today || 0),
      timeLeftContinuous: Number(
        st.time_left_continuous !== undefined ? st.time_left_continuous : st.time_left_today || 0
      ),
      nextAllowedAt: st.next_allowed_at || null,
      nextCurfewAt: st.next_curfew_at || null,
      lockoutAction: pol.lockout_action || "lock",
      weeklyLimit:
        pol.weekly_limit !== null && pol.weekly_limit !== undefined
          ? Number(pol.weekly_limit)
          : null,
      monthlyLimit:
        pol.monthly_limit !== null && pol.monthly_limit !== undefined
          ? Number(pol.monthly_limit)
          : null,
      saveGraceEnabled: pol.save_grace_enabled !== false,
      saveGrace: st.save_grace || {
        enabled: true,
        used_today: false,
        active: false,
        remaining_sec: 0,
        available: false,
      },
      pause: st.pause || pol.pause || { active: false, mode: "none", until_utc: null, reason: "" },
      homeworkMode: st.homework_mode ||
        pol.homework_mode || {
          active: false,
          effective: false,
          blocked_categories: ["Gaming", "Social"],
        },
      holidays: Array.isArray(pol.holidays) ? pol.holidays.map((h) => Object.assign({}, h)) : [],
      activeHoliday: st.active_holiday || null,
      pendingRequestCount: Number(st.pending_request_count || 0),
      parentCodeEnabled:
        st.parent_code_enabled !== undefined
          ? Boolean(st.parent_code_enabled)
          : Boolean(prev && prev.parentCodeEnabled),
      scheduleMode: schedMode,
      sameEveryDay: schedMode === "same",
      days,
      todayLimitSec: Number(
        rawTodayLim !== null && rawTodayLim !== undefined ? rawTodayLim : days[getTodayKey()].limit
      ),
      todayWindows: todayWins,
      todayOverrideActive: Boolean(st.today_override && st.today_override.active),
      appLimits: {
        enabled: Boolean(alPol.enabled),
        apps,
        categories,
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
    if (u.enforcementState === "paused" || u.enforcementState === "homework") return 86400;
    if (u.role === "parent") return 86400;
    if (state.dirtyToday.has(u.username)) {
      if (u.todayLimitSec >= 86400) return 86400;
      return Math.max(0, u.todayLimitSec - u.spentToday);
    }
    if (state.dirtyPolicies.has(u.username)) {
      if (u.todayLimitSec >= 86400) return 86400;
      return Math.max(0, u.todayLimitSec + (u.bonusToday || 0) - u.spentToday);
    }
    if (u.todayLimitSec >= 86400 && u.weeklyLimit === null && u.monthlyLimit === null) {
      return 86400;
    }
    return Math.max(0, Number(u.timeLeftDaemon || 0));
  }

  function todayCurfew(u) {
    const wins = u.todayWindows || [];
    if (!wins.length) return "Blocked all day";
    const last = wins[wins.length - 1];
    return last.endMin >= 1440 ? "None" : fmtTimeMin(last.endMin);
  }

  function currentUser() {
    if (state.nav.startsWith("user:")) {
      const uname = state.nav.slice(5);
      if (state.users[uname]) return state.users[uname];
    }
    const all = Object.values(state.users);
    const kid = all.find((u) => u.role !== "parent");
    return kid || all[0] || null;
  }

  function pendingRequestsForUser(username) {
    return (state.timeRequests || []).filter(
      (r) => r.status === "pending" && (!username || r.username === username)
    );
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
    toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
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
    bar.classList.toggle(
      "visible",
      isDirty() && state.surface === "admin" && !state.showSetupWizard
    );
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
    base.role = u.role || "child";
    base.lockout_action = u.lockoutAction;
    base.weekly_limit = u.weeklyLimit;
    base.monthly_limit = u.monthlyLimit;
    base.save_grace_enabled = Boolean(u.saveGraceEnabled);
    base.holidays = (u.holidays || []).map((h) => Object.assign({}, h));
    base.daily_limits = {};
    base.curfew_windows = {};
    for (const d of DAYS) {
      base.daily_limits[d] = u.days[d].limit;
      base.curfew_windows[d] = (u.days[d].windows || []).map((w) => ({
        start: fmtMin(w.startMin),
        end: fmtMin(w.endMin),
      }));
    }
    delete base.playtime;
    base.app_limits = {
      enabled: Boolean(u.appLimits.enabled),
      apps: u.appLimits.apps.map((a) => ({
        pattern: a.pattern,
        label: a.label,
        daily_limit_sec: a.dailyLimit,
        weekend_limit_sec: a.weekendLimit,
        category: a.category || "App",
        flatpak_id: a.flatpakId || "",
        steam_app_id: a.steamAppId || "",
        always_allowed: Boolean(a.alwaysAllowed),
        foreground_only: Boolean(a.foregroundOnly),
      })),
      category_limits: (u.appLimits.categories || []).map((c) => ({
        category: c.category,
        daily_limit_sec: c.dailyLimit,
        weekend_limit_sec: c.weekendLimit,
      })),
    };
    return base;
  }

  async function commitChanges() {
    if (!isDirty() || state.saving) return;
    state.saving = true;
    updateUnsavedBar();

    let snapUsers = {};
    try {
      snapUsers = (state.savedSnapshot && JSON.parse(state.savedSnapshot).users) || {};
    } catch {
      snapUsers = {};
    }
    const todayKey = getTodayKey();

    const policies = {};
    const today_overrides = {};
    state.dirtyPolicies.forEach((uname) => {
      const u = state.users[uname];
      if (!u) return;
      policies[uname] = serializeUserPolicy(u);
      const prevSnap = snapUsers[uname];
      const todayScheduleChanged =
        prevSnap && prevSnap.days && prevSnap.days[todayKey]
          ? !dayEqual(prevSnap.days[todayKey], u.days[todayKey])
          : false;
      if (!state.dirtyToday.has(uname) && u.todayOverrideActive && todayScheduleChanged) {
        today_overrides[uname] = { reset: true };
      }
    });

    state.dirtyToday.forEach((uname) => {
      const u = state.users[uname];
      if (!u) return;
      today_overrides[uname] = {
        limit_sec: u.todayLimitSec,
        windows: (u.todayWindows || []).map((w) => ({
          start: fmtMin(w.startMin),
          end: fmtMin(w.endMin),
        })),
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
          const existing = state.users[uname];
          const fresh = normalizeUserStatus(st, existing);
          if (existing) {
            const prevMode = existing.scheduleMode;
            const sameDays = DAYS.every((d) => dayEqual(existing.days[d], fresh.days[d]));
            Object.assign(existing, fresh);
            if (sameDays) {
              existing.scheduleMode = prevMode;
              existing.sameEveryDay = prevMode === "same";
            }
          } else {
            state.users[uname] = fresh;
          }
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

  /* ----------------- Surgical Live D-Bus Push (Zero Scroll/Focus Wipe) ----------------- */

  /**
   * Merge a live daemon UserStatus into `state.users[username]` in place so DOM event handlers
   * never hold stale object references (P0-3) and unsaved drafts on dirty users are preserved (P1-13).
   */
  function mergeLiveUserStatus(username, rawStatus) {
    if (!rawStatus) return state.users[username] || null;
    const existing = state.users[username];
    const fresh = normalizeUserStatus(rawStatus, existing);
    if (!existing) {
      state.users[username] = fresh;
      return fresh;
    }
    const dirty = state.dirtyPolicies.has(username) || state.dirtyToday.has(username);
    if (dirty) {
      existing.loggedIn = fresh.loggedIn;
      existing.locked = fresh.locked;
      existing.idlePaused = fresh.idlePaused;
      existing.enforcementState = fresh.enforcementState;
      existing.spentToday = fresh.spentToday;
      existing.spentWeek = fresh.spentWeek;
      existing.spentMonth = fresh.spentMonth;
      existing.bonusToday = fresh.bonusToday;
      existing.lastBonusNote = fresh.lastBonusNote;
      existing.timeLeftDaemon = fresh.timeLeftDaemon;
      existing.timeLeftContinuous = fresh.timeLeftContinuous;
      existing.nextAllowedAt = fresh.nextAllowedAt;
      existing.nextCurfewAt = fresh.nextCurfewAt;
      existing.pendingRequestCount = fresh.pendingRequestCount;
      existing.pause = fresh.pause;
      existing.homeworkMode = fresh.homeworkMode;
      existing.saveGrace = fresh.saveGrace;
      existing.activeHoliday = fresh.activeHoliday;
      existing.todayOverrideActive = fresh.todayOverrideActive;
      existing.parentCodeEnabled = fresh.parentCodeEnabled;
      if (!state.dirtyToday.has(username)) {
        existing.todayLimitSec = fresh.todayLimitSec;
        existing.todayWindows = fresh.todayWindows;
      }
      if (existing.rawPolicy) {
        existing.rawPolicy.pause = fresh.pause;
        existing.rawPolicy.homework_mode = fresh.homeworkMode;
      }
      const freshApps = new Map(fresh.appLimits.apps.map((a) => [a.pattern, a]));
      existing.appLimits.apps.forEach((a) => {
        const fa = freshApps.get(a.pattern);
        if (fa) {
          a.spentToday = fa.spentToday;
          a.bonusToday = fa.bonusToday;
          a.running = fa.running;
          a.blockedByHomework = fa.blockedByHomework;
        }
      });
      const freshCats = new Map(
        fresh.appLimits.categories.map((c) => [c.category.toLowerCase(), c])
      );
      existing.appLimits.categories.forEach((c) => {
        const fc = freshCats.get(c.category.toLowerCase());
        if (fc) {
          c.spentToday = fc.spentToday;
          c.bonusToday = fc.bonusToday;
          c.running = fc.running;
        }
      });
      return existing;
    }

    const prevMode = existing.scheduleMode;
    const sameDays = DAYS.every((d) => dayEqual(existing.days[d], fresh.days[d]));
    Object.assign(existing, fresh);
    if (sameDays) {
      existing.scheduleMode = prevMode;
      existing.sameEveryDay = prevMode === "same";
    }
    return existing;
  }

  function patchLiveStatusDOM(u) {
    const left = timeLeftSec(u);
    const leftStr =
      u.role === "parent"
        ? "Parent"
        : u.enforcementState === "paused"
          ? "Paused"
          : u.enforcementState === "homework"
            ? "Study"
            : u.locked
              ? "Locked"
              : fmtDur(left);
    const navLeft = document.querySelector(`[data-navleft="${CSS.escape(u.username)}"]`);
    if (navLeft) navLeft.textContent = leftStr;

    const active = currentUser();
    if (!active || active.username !== u.username) return;

    const pillLeftVal = document.getElementById("pillLeftVal");
    if (pillLeftVal) {
      pillLeftVal.textContent =
        u.enforcementState === "paused"
          ? "Paused"
          : u.enforcementState === "homework"
            ? "Study Mode"
            : fmtDur(left);
    }
    const pillLeftBox = document.getElementById("pillLeftBox");
    if (pillLeftBox) {
      const leftCls =
        u.enforcementState === "homework"
          ? "info"
          : u.enforcementState === "paused"
            ? "ok"
            : left === 0
              ? "err"
              : left <= 600
                ? "warn"
                : "primary";
      pillLeftBox.className = `stat-pill ${leftCls}`;
    }
    const pillSpentVal = document.getElementById("pillSpentVal");
    if (pillSpentVal) pillSpentVal.textContent = fmtDur(u.spentToday);
    const pillWeekVal = document.getElementById("pillWeekVal");
    if (pillWeekVal) pillWeekVal.textContent = fmtDur(u.spentWeek);

    (u.appLimits.categories || []).forEach((c, idx) => {
      const allotted = Math.max(
        0,
        (c.effectiveLimit !== undefined ? c.effectiveLimit : c.dailyLimit) + (c.bonusToday || 0)
      );
      const rem = Math.max(0, allotted - c.spentToday);
      const pct =
        allotted === 0
          ? 100
          : Math.min(100, Math.round((c.spentToday / Math.max(1, allotted)) * 100));
      const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
      const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
      const fill = document.querySelector(`[data-catfill="${idx}"]`);
      if (fill) {
        fill.style.width = `${pct}%`;
        fill.style.background = col;
      }
      const used = document.querySelector(`[data-catused="${idx}"]`);
      if (used) used.textContent = fmtDur(c.spentToday);
      const pill = document.querySelector(`[data-catpill="${idx}"]`);
      if (pill) {
        pill.className = `pill ${pCls}`;
        pill.textContent =
          allotted === 0 ? "Blocked" : rem === 0 ? "Limit reached" : `${fmtDur(rem)} left`;
      }
    });

    (u.appLimits.apps || []).forEach((a, idx) => {
      const allotted = Math.max(
        0,
        (a.effectiveLimit !== undefined ? a.effectiveLimit : a.dailyLimit) + (a.bonusToday || 0)
      );
      const rem = Math.max(0, allotted - a.spentToday);
      const pct =
        allotted === 0
          ? 100
          : Math.min(100, Math.round((a.spentToday / Math.max(1, allotted)) * 100));
      const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
      const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
      const fill = document.querySelector(`[data-appfill="${idx}"]`);
      if (fill) {
        fill.style.width = `${pct}%`;
        fill.style.background = col;
      }
      const meta = document.querySelector(`[data-appused="${idx}"]`);
      if (meta) meta.textContent = fmtDur(a.spentToday);
      const remEl = document.querySelector(`[data-apprem="${idx}"]`);
      if (remEl) remEl.textContent = fmtDur(rem);
      const pill = document.querySelector(`[data-apppill="${idx}"]`);
      if (pill) {
        pill.className = `pill ${pCls}`;
        pill.textContent =
          allotted === 0 ? "Blocked" : rem === 0 ? "Limit reached" : `${fmtDur(rem)} left`;
      }
    });
  }

  function applyLiveStatusPush(username, rawStatus) {
    if (state.surface === "my_status" && state.me && state.me.username === username) {
      const prev = state.me;
      const fresh = normalizeUserStatus(rawStatus, prev);
      state.me = fresh;
      const structural =
        prev.enforcementState !== fresh.enforcementState ||
        prev.locked !== fresh.locked ||
        prev.saveGrace.available !== fresh.saveGrace.available ||
        prev.saveGrace.active !== fresh.saveGrace.active ||
        prev.parentCodeEnabled !== fresh.parentCodeEnabled ||
        prev.bonusToday !== fresh.bonusToday ||
        prev.lastBonusNote !== fresh.lastBonusNote ||
        prev.todayLimitSec !== fresh.todayLimitSec ||
        !windowsEqual(prev.todayWindows, fresh.todayWindows) ||
        prev.appLimits.enabled !== fresh.appLimits.enabled ||
        prev.appLimits.apps.length !== fresh.appLimits.apps.length ||
        prev.appLimits.categories.length !== fresh.appLimits.categories.length ||
        fresh.appLimits.apps.some((a, i) => {
          const pa = prev.appLimits.apps[i];
          return !pa || pa.spentToday !== a.spentToday || pa.bonusToday !== a.bonusToday;
        }) ||
        fresh.appLimits.categories.some((c, i) => {
          const pc = prev.appLimits.categories[i];
          return !pc || pc.spentToday !== c.spentToday || pc.bonusToday !== c.bonusToday;
        });
      if (structural) {
        UI.render();
      } else {
        const left = timeLeftSec(fresh);
        const isHomework = fresh.enforcementState === "homework";
        const isPaused = fresh.enforcementState === "paused";
        const col = isHomework
          ? "var(--info)"
          : isPaused
            ? "var(--ok)"
            : left === 0
              ? "var(--err)"
              : left <= 600
                ? "var(--warn)"
                : "var(--p)";
        const mainTxt = isHomework ? "Study" : isPaused ? "Paused" : fmtDur(left);
        const ringMain = document.getElementById("myRingMain");
        if (ringMain) {
          ringMain.textContent = mainTxt;
          ringMain.style.color = col;
        }
        const ringArc = document.getElementById("myRingArc");
        if (ringArc) {
          const c = 2 * Math.PI * 58;
          const pct =
            isPaused || isHomework || fresh.todayLimitSec >= 86400
              ? 1
              : Math.max(0, Math.min(1, left / Math.max(1, fresh.todayLimitSec)));
          ringArc.setAttribute("stroke", col);
          ringArc.setAttribute("stroke-dashoffset", (c * (1 - pct)).toFixed(1));
        }
        const usedEl = document.getElementById("myUsedToday");
        if (usedEl) usedEl.textContent = fmtDur(fresh.spentToday);
      }
      return;
    }
    if (state.surface !== "admin" || !state.users[username]) return;

    const existing = state.users[username];
    const fresh = normalizeUserStatus(rawStatus, existing);
    const dirty = state.dirtyPolicies.has(username) || state.dirtyToday.has(username);
    const structural =
      !dirty &&
      (existing.loggedIn !== fresh.loggedIn ||
        existing.locked !== fresh.locked ||
        existing.idlePaused !== fresh.idlePaused ||
        existing.enforcementState !== fresh.enforcementState ||
        existing.pendingRequestCount !== fresh.pendingRequestCount ||
        existing.bonusToday !== fresh.bonusToday ||
        existing.lastBonusNote !== fresh.lastBonusNote);

    mergeLiveUserStatus(username, rawStatus);
    if (dirty) {
      patchLiveStatusDOM(existing);
    } else {
      if (!isDirty()) snapshotState();
      if (structural) {
        UI.render();
      } else {
        patchLiveStatusDOM(existing);
      }
    }
  }

  function applyTimeRequestPush(username, req) {
    if (!req || !req.id) return;
    if (state.surface === "my_status") {
      const targetUser = username || req.username || "";
      if (!state.me || targetUser !== state.me.username) return;
      state.myRequests = [req, ...(state.myRequests || []).filter((r) => r.id !== req.id)].slice(
        0,
        15
      );
      UI.render();
      return;
    }
    if (state.surface === "admin") {
      const rest = (state.timeRequests || []).filter((r) => r.id !== req.id);
      if (req.status === "pending") {
        state.timeRequests = [req, ...rest];
        showToast(
          `${req.username} requested +${fmtDur(req.requested_sec)} for ${req.target_label}`
        );
      } else {
        state.timeRequests = rest;
      }
      UI.render();
    }
  }

  /* ----------------- System Health (Wayland & Multi-DE Aware) ----------------- */

  function translateDoctorChecks(doc) {
    const checks = (doc && doc.checks) || [];
    const byName = new Map(checks.map((c) => [c.name, c]));
    const isOk = (name) => (byName.get(name) || {}).status === "ok";
    const sessionMode = (doc && doc.session_mode) || "none";

    const svcOk = isOk("systemd_service") || isOk("dbus_service");
    const authOk = isOk("polkit_policy") && isOk("dbus_policy");
    const lockChecks = ["dconf_lock_policy", "kde_lock_policy", "xfce_lock_policy"].filter((k) =>
      byName.has(k)
    );
    const lockProtectOk = lockChecks.length
      ? lockChecks.every((k) => isOk(k))
      : isOk("dconf_lock_policy");
    const gameLockOk =
      sessionMode === "wayland"
        ? isOk("bin_gdbus")
        : isOk("bin_gdbus") && (isOk("bin_xdotool") || isOk("bin_wmctrl"));

    const lockDetail = lockProtectOk
      ? "Protected — standard users cannot turn off screen locking"
      : "Standard users can currently turn off screen lock in desktop settings";
    const gameDetail =
      sessionMode === "wayland"
        ? "Wayland session active — compositor handles fullscreen locking natively"
        : gameLockOk
          ? "Ready to lock over fullscreen games and desktop apps"
          : "Optional X11 fullscreen helper tools (xdotool / wmctrl) are not installed yet";

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
          ? "Changes require an administrator password (walk-up grants are single-use)"
          : "Administrator protection policy files are missing",
      },
      {
        name: "Lock-Screen Protection",
        ok: lockProtectOk,
        fixable: !lockProtectOk,
        detail: lockDetail,
      },
      {
        name: "Fullscreen Game Locking",
        ok: gameLockOk,
        detail: gameDetail,
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
    WEEKDAY_KEYS,
    WEEKEND_KEYS,
    DAY_FULL,
    CATEGORIES,
    SLIDER_UNLIMITED_VAL,
    state,
    escHtml,
    getTodayKey,
    nowMinutes,
    nowHHMM,
    greetingPhrase,
    fmtMin,
    fmtTimeMin,
    fmtDur,
    fmtAppLimit,
    fmtIsoClock,
    fmtRelativeEventTime,
    secToSliderVal,
    sliderValToSec,
    normalizeWindows,
    cloneWindows,
    detectScheduleMode,
    normalizeUserStatus,
    mergeLiveUserStatus,
    buildHistorySeries,
    timeLeftSec,
    todayCurfew,
    currentUser,
    pendingRequestsForUser,
    applyThemeAndAccent,
    showToast,
    openModal,
    closeModal,
    isDirty,
    updateUnsavedBar,
    snapshotState,
    clearDirty,
    discardChanges,
    serializeUserPolicy,
    commitChanges,
    patchLiveStatusDOM,
    applyLiveStatusPush,
    applyTimeRequestPush,
    translateDoctorChecks,
    doctorNeedsLockFix,
    doctorIssueCount,
  });
})((window.CurfewUI = window.CurfewUI || {}));
