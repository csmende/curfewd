/* Pure Material Design 3 HTML component renderers for curfewd-ui (Spec 005). */
(function (UI) {
  "use strict";

  function renderRing(u, size = 152) {
    const left = UI.timeLeftSec(u);
    const lim = u.todayLimitSec;
    const r = 58;
    const c = 2 * Math.PI * r;
    const pct = lim >= 86400 ? 1 : Math.max(0, Math.min(1, left / Math.max(1, lim)));
    const col = left === 0 ? "var(--err)" : left <= 600 ? "var(--warn)" : "var(--p)";
    const subTxt = lim >= 86400 ? "no daily limit" : `left of ${UI.fmtDur(lim)}`;
    return `<div class="ring-box" style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}" viewBox="0 0 148 148">
        <circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--s3)" stroke-width="11"/>
        <circle cx="74" cy="74" r="${r}" fill="none" stroke="${col}" stroke-width="11" stroke-linecap="round"
          stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - pct)).toFixed(1)}" transform="rotate(-90 74 74)"/>
      </svg>
      <div class="ring-center">
        <div class="ring-main" style="color:${col}">${UI.fmtDur(left)}</div>
        <div class="ring-sub">${subTxt}</div>
      </div>
    </div>`;
  }

  function renderUsageChart(histObj, spanDays) {
    const rows = UI.buildHistorySeries(histObj, spanDays);
    const maxVal = Math.max(60, ...rows.map((r) => r.act));
    const w = 640;
    const h = 158;
    const padL = 36;
    const padB = 26;
    const padT = 12;
    const cw = w - padL - 8;
    const ch = h - padB - padT;
    const step = cw / Math.max(1, rows.length);
    const bw = Math.min(34, step * 0.52);

    const bars = rows
      .map((row, idx) => {
        const x = padL + idx * step + (step - bw) / 2;
        const bh = row.act > 0 ? Math.max(4, (row.act / maxVal) * ch) : 2;
        const y = padT + ch - bh;
        const pth = row.pt > 0 ? Math.min(bh, Math.max(3, (row.pt / maxVal) * ch)) : 0;
        const pty = padT + ch - pth;
        return `<g><title>${UI.escHtml(row.d)}: ${row.act}m total${row.pt > 0 ? ` (${row.pt}m App Limits)` : ""}</title>
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="6" fill="var(--p)" opacity="${row.act > 0 ? ".78" : ".22"}"/>
        ${pth > 0 ? `<rect x="${(x + 3).toFixed(1)}" y="${pty.toFixed(1)}" width="${Math.max(4, bw - 6).toFixed(1)}" height="${pth.toFixed(1)}" rx="4" fill="var(--on-pc)" opacity=".5"/>` : ""}
        <text x="${(x + bw / 2).toFixed(1)}" y="${h - 7}" text-anchor="middle" fill="var(--fg-muted)" font-size="10.5" font-family="var(--font-mono)">${UI.escHtml(row.d)}</text>
      </g>`;
      })
      .join("");

    return `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${padT}" x2="${w - 8}" y2="${padT}" stroke="var(--out-var)" stroke-dasharray="3 4"/>
      <line x1="${padL}" y1="${padT + ch / 2}" x2="${w - 8}" y2="${padT + ch / 2}" stroke="var(--out-var)" stroke-dasharray="3 4"/>
      <line x1="${padL}" y1="${padT + ch}" x2="${w - 8}" y2="${padT + ch}" stroke="var(--out-var)"/>
      <text x="2" y="${padT + 4}" fill="var(--fg-muted)" font-size="10" font-family="var(--font-mono)">${maxVal}m</text>
      <text x="2" y="${padT + ch / 2 + 4}" fill="var(--fg-muted)" font-size="10" font-family="var(--font-mono)">${Math.round(maxVal / 2)}m</text>
      ${bars}
    </svg>`;
  }

  function renderWindowBar(
    key,
    startMin,
    endMin,
    { editable = false, showNow = false, compact = false, showTicks = true } = {}
  ) {
    const sp = (startMin / 1440) * 100;
    const ep = (endMin / 1440) * 100;
    const wp = Math.max(0, ep - sp);
    const nowPct = (UI.nowMinutes() / 1440) * 100;
    const handles = editable
      ? `
      <button type="button" class="timeline-handle" data-edge="start" style="left:${sp}%" aria-label="Adjust start time">
        <span class="handle-tip">${UI.fmtMin(startMin)}</span>
      </button>
      <button type="button" class="timeline-handle" data-edge="end" style="left:${ep}%" aria-label="Adjust end time">
        <span class="handle-tip">${UI.fmtMin(endMin)}</span>
      </button>`
      : "";

    return `<div>
      <div class="timeline-bar ${editable ? "editable" : ""} ${compact ? "compact" : ""}" ${editable ? `data-winbar="${UI.escHtml(key)}"` : ""}>
        <div class="timeline-track"><div class="timeline-seg" style="left:${sp}%;width:${wp}%"></div></div>
        ${showNow ? `<div class="timeline-needle" style="left:${nowPct.toFixed(2)}%" title="Current time (${UI.nowHHMM()})"></div>` : ""}
        ${handles}
      </div>
      ${showTicks ? `<div class="timeline-ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>` : ""}
    </div>`;
  }

  function renderOverviewTab(u) {
    const left = UI.timeLeftSec(u);
    const leftCls = left === 0 ? "err" : left <= 600 ? "warn" : "primary";
    const alertPill = u.locked
      ? `<span class="pill p-err">Screen Locked</span>`
      : !u.loggedIn
        ? `<span class="pill p-muted">Offline</span>`
        : "";

    return `<div class="card">
      <div class="card-hdr">
        <div style="display:flex;align-items:center;gap:10px">
          <h3 class="card-title">Current Status</h3>
          ${alertPill}
        </div>
        <button type="button" class="btn btn-danger btn-sm" id="btnLockModal">${UI.ICONS.lock} Lock Now</button>
      </div>

      <div class="stat-pills">
        <div class="stat-pill ${leftCls}" id="pillLeftBox">
          <span class="stat-pill-lbl">Time Left Today</span>
          <span class="stat-pill-val" id="pillLeftVal">${UI.fmtDur(left)}</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-lbl">Spent Today</span>
          <span class="stat-pill-val">${UI.fmtDur(u.spentToday)}</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-lbl">Spent This Week</span>
          <span class="stat-pill-val">${UI.fmtDur(u.spentWeek)}</span>
        </div>
      </div>

      <div class="card-section">
        <div class="sec-hdr">
          <h4 class="sec-title">Today's Time and Schedule</h4>
          <span class="card-sub">Now: ${UI.nowHHMM()}</span>
        </div>
        <div class="day-row">
          <div class="day-top">
            <div class="day-hours-wrap">
              <span class="day-hours-lbl">Time allowed today</span>
              <input class="thin-slider" type="range" min="0" max="${UI.SLIDER_UNLIMITED_VAL}" step="15" value="${UI.secToSliderVal(u.todayLimitSec)}" id="todayLimSlider" aria-label="Time allowed today"/>
              <span class="day-hours-val" id="todayLimVal">${UI.fmtDur(u.todayLimitSec)}</span>
            </div>
          </div>
          ${renderWindowBar("today", u.todayStartMin, u.todayEndMin, { editable: true, showNow: true, compact: false, showTicks: true })}
        </div>
      </div>

      <div class="card-section">
        <div class="sec-hdr">
          <h4 class="sec-title">Screen-Time &amp; App Limits History</h4>
          <div class="segmented">
            <button type="button" class="seg-btn ${UI.state.historySpan === 7 ? "active" : ""}" data-hspan="7">7 Days</button>
            <button type="button" class="seg-btn ${UI.state.historySpan === 14 ? "active" : ""}" data-hspan="14">14 Days</button>
          </div>
        </div>
        ${renderUsageChart(UI.state.histories[u.username], UI.state.historySpan)}
      </div>
    </div>`;
  }

  function renderScheduleTab(u) {
    const renderDayRow = (key, label, dObj, showTicks) => `
      <div class="day-row">
        <div class="day-top">
          <div class="day-name">${UI.escHtml(label)}</div>
          <div class="day-hours-wrap">
            <span class="day-hours-lbl">Daily time limit</span>
            <input class="thin-slider" type="range" min="0" max="${UI.SLIDER_UNLIMITED_VAL}" step="15" value="${UI.secToSliderVal(dObj.limit)}" data-daylim="${UI.escHtml(key)}" aria-label="${UI.escHtml(label)} daily time limit"/>
            <span class="day-hours-val" data-daylimval="${UI.escHtml(key)}">${UI.fmtDur(dObj.limit)}</span>
          </div>
        </div>
        ${renderWindowBar(key, dObj.startMin, dObj.endMin, { editable: true, showNow: false, compact: true, showTicks })}
      </div>`;

    const rowsHtml = u.sameEveryDay
      ? renderDayRow("all", "Every Day", u.days.mon, true)
      : UI.DAYS.map((d, i) =>
          renderDayRow(d, UI.DAY_FULL[d], u.days[d], i === UI.DAYS.length - 1)
        ).join("");

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
        ]
          .map(
            ([
              v,
              t,
              d,
            ]) => `<button type="button" class="lockout-opt ${u.lockoutAction === v ? "active" : ""}" data-lockout="${v}">
          <div style="font-weight:600;margin-bottom:3px">${t}</div>
          <div style="font-size:12px;opacity:.85">${d}</div>
        </button>`
          )
          .join("")}
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
        <button type="button" class="btn btn-tonal btn-sm" id="btnAddApp">${UI.ICONS.plus} Add App / Game</button>
      </div>
      <div class="app-list" style="${al.enabled ? "" : "opacity:.55"}">
        ${
          al.apps.length
            ? al.apps
                .map((a, idx) => {
                  const allotted = Math.max(0, a.dailyLimit + (a.bonusToday || 0));
                  const rem = Math.max(0, allotted - a.spentToday);
                  const pct = Math.min(
                    100,
                    Math.round((a.spentToday / Math.max(1, allotted)) * 100)
                  );
                  const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
                  const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
                  return `<div class="app-card">
            <div class="app-top">
              <div>
                <div style="font-weight:600;display:flex;align-items:center;gap:6px">${UI.escHtml(a.label)} ${a.running ? `<span class="pill p-info">Running</span>` : ""}</div>
                <div class="card-sub" style="font-size:12px">${UI.escHtml(a.category || "App")}</div>
              </div>
              <span class="pill ${pCls}">${rem === 0 ? "Limit reached" : `${UI.fmtDur(rem)} left`}</span>
            </div>
            <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
            <div class="app-meta">
              <span>Allotted: <strong>${UI.fmtDur(allotted)}</strong>${a.bonusToday ? ` <span style="color:var(--ok)">(+${UI.fmtDur(a.bonusToday)} bonus)</span>` : ""}</span>
              <span>Used: <strong>${UI.fmtDur(a.spentToday)}</strong> · Left: <strong>${UI.fmtDur(rem)}</strong></span>
            </div>
            <div class="app-ctrl">
              <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
                <span class="card-sub" style="white-space:nowrap">Daily limit</span>
                <input class="slider" style="width:100%;max-width:130px" type="range" min="15" max="240" step="15" value="${Math.max(15, Math.min(240, Math.round(a.dailyLimit / 60)))}" data-applimit="${idx}" ${al.enabled ? "" : "disabled"}/>
              </div>
              <div style="display:flex;align-items:center;gap:6px">
                <button type="button" class="btn btn-tonal btn-sm" data-appgrant="${idx}" ${al.enabled ? "" : "disabled"}>+15m</button>
                <button type="button" class="btn btn-outlined btn-sm" data-rmapp="${idx}" title="Remove">${UI.ICONS.trash}</button>
              </div>
            </div>
          </div>`;
                })
                .join("")
            : `<div class="card-sub">No limited apps configured yet. Click "Add App / Game" to pick an installed app and set its daily limit.</div>`
        }
      </div>
    </div>`;
  }

  function renderDoctorView() {
    const checks = UI.translateDoctorChecks(UI.state.doctor);
    const needsFix = UI.doctorNeedsLockFix();
    const allOk = checks.every((c) => c.ok);
    return `<div class="page-hdr">
      <div><h1 class="page-title">System Health</h1><div class="page-sub">Quick checks to make sure limits and screen locking work reliably</div></div>
      ${
        allOk
          ? `<span class="pill p-ok">All Checks Passing</span>`
          : needsFix
            ? `<button type="button" class="btn btn-filled" id="btnDocFix" ${UI.state.busyFix ? "disabled" : ""}>${UI.ICONS.doctor} ${UI.state.busyFix ? "Protecting..." : "Protect Lock Screen"}</button>`
            : `<button type="button" class="btn btn-outlined" id="btnDocRefresh">Re-check</button>`
      }
    </div>
    <div class="card"><div class="check-list">${checks
      .map(
        (c) => `<div class="check-row">
      <div><div style="font-weight:600;font-size:13.5px">${UI.escHtml(c.name)}</div><div class="card-sub">${UI.escHtml(c.detail)}</div></div>
      <span class="pill ${c.ok ? "p-ok" : "p-warn"}">${c.ok ? "OK" : "Action Needed"}</span>
    </div>`
      )
      .join("")}</div></div>`;
  }

  function renderSettingsView() {
    const cfg = UI.state.config || {
      session_lock_fallback: true,
      release_x11_grab_on_lock: true,
      final_countdown_sec: 15,
    };
    const curTheme = UI.state.prefs.theme || "auto";
    const curAcc = UI.state.prefs.accent || "indigo";

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
            ${[
              ["indigo", "#536dfe"],
              ["emerald", "#158f5e"],
              ["slate", "#3870a8"],
              ["amber", "#c27b0e"],
            ]
              .map(
                ([k, hx]) =>
                  `<button type="button" class="swatch ${curAcc === k ? "active" : ""}" data-accent="${k}" title="${k}" style="background:${hx}"></button>`
              )
              .join("")}
          </div>
        </div>
      </div>
    </div>`;
  }

  function renderAdminShell() {
    const u = UI.currentUser();
    const userList = Object.values(UI.state.users);
    const issues = UI.doctorIssueCount();
    const needsLockFix = UI.doctorNeedsLockFix();

    const items = userList
      .map((usr) => {
        const active = UI.state.nav === `user:${usr.username}`;
        const left = UI.timeLeftSec(usr);
        const dot = !usr.loggedIn
          ? "dot-off"
          : usr.locked
            ? "dot-lock"
            : left <= 600
              ? "dot-warn"
              : "dot-ok";
        const rt = usr.locked ? "Locked" : UI.fmtDur(left);
        return `<button type="button" class="nav-item ${active ? "active" : ""}" data-nav="user:${UI.escHtml(usr.username)}">
        <div class="avatar">${UI.escHtml(usr.initials)}<span class="sdot ${dot}"></span></div>
        <div class="nav-meta">
          <div class="nav-uname"><span>${UI.escHtml(usr.fullName)}</span><span style="font-size:12px;font-family:var(--font-mono)" data-navleft="${UI.escHtml(usr.username)}">${rt}</span></div>
          <div class="nav-usub">@${UI.escHtml(usr.username)} · ${usr.loggedIn ? "Signed in" : "Offline"}</div>
        </div>
      </button>`;
      })
      .join("");

    let content = "";
    if (UI.state.nav === "doctor") {
      content = renderDoctorView();
    } else if (UI.state.nav === "settings") {
      content = renderSettingsView();
    } else if (!u) {
      content = `<div class="card"><h3 class="card-title">No Users Found</h3><div class="card-sub">No desktop user accounts were detected on this computer.</div></div>`;
    } else {
      content = `
        ${
          needsLockFix && !UI.state.bannerDismissed
            ? `<div class="doctor-banner">
          <div class="doctor-left">${UI.ICONS.warn}<div><div style="font-weight:700;font-size:13.5px">Lock-screen protection needed</div><div style="font-size:12.5px;opacity:.9">Standard users can currently turn off screen locking in system settings. Protect this setting with one click.</div></div></div>
          <div style="display:flex;gap:8px;flex-shrink:0"><button type="button" class="btn btn-filled btn-sm" id="btnBanFix" ${UI.state.busyFix ? "disabled" : ""}>${UI.state.busyFix ? "Protecting..." : "Protect Now"}</button><button type="button" class="btn btn-outlined btn-sm" id="btnBanDismiss">Dismiss</button></div>
        </div>`
            : ""
        }
        <div class="page-hdr"><div>
          <h1 class="page-title">${UI.escHtml(u.fullName)} <span style="font-weight:500;font-size:16px;color:var(--fg-muted)">@${UI.escHtml(u.username)}</span></h1>
          <div class="page-sub">Daily allowance <span id="hdrLimit">${UI.fmtDur(u.todayLimitSec)}</span> · Curfew <span id="hdrCurfew">${UI.todayCurfew(u)}</span></div>
        </div></div>
        <div class="m3-tabs">
          <button type="button" class="m3-tab ${UI.state.userTab === "overview" ? "active" : ""}" data-utab="overview">${UI.ICONS.overview} Overview</button>
          <button type="button" class="m3-tab ${UI.state.userTab === "schedule" ? "active" : ""}" data-utab="schedule">${UI.ICONS.schedule} Schedule</button>
          <button type="button" class="m3-tab ${UI.state.userTab === "apps" ? "active" : ""}" data-utab="apps">${UI.ICONS.apps} App Limits</button>
        </div>
        ${UI.state.userTab === "overview" ? renderOverviewTab(u) : UI.state.userTab === "schedule" ? renderScheduleTab(u) : renderAppLimitsTab(u)}`;
    }

    return `<div class="app-shell">
      <aside class="nav-drawer">
        <div class="brand-row">${UI.ICONS.brand}<div><div class="brand-title">curfewd</div><div class="brand-sub">Screen Time Control</div></div></div>
        <div><div class="nav-sec">Users</div><div class="nav-list">${items}</div></div>
        <div class="nav-spacer"></div><div class="nav-div"></div>
        <div class="nav-list">
          <button type="button" class="nav-item ${UI.state.nav === "doctor" ? "active" : ""}" data-nav="doctor">${UI.ICONS.doctor}<span style="flex:1">System Health</span><span class="pill ${issues === 0 ? "p-ok" : "p-warn"}">${issues === 0 ? "OK" : issues}</span></button>
          <button type="button" class="nav-item ${UI.state.nav === "settings" ? "active" : ""}" data-nav="settings">${UI.ICONS.settings}<span>Settings</span></button>
        </div>
      </aside>
      <main class="main-pane">${content}</main>
    </div>`;
  }

  function renderMyScreenTime() {
    const u = UI.state.me;
    if (!u) return renderUnavailable("Unable to load your screen-time status.");
    const firstName = (u.fullName || u.username).split(/\s+/)[0];
    const appRows =
      u.appLimits.enabled && u.appLimits.apps.length
        ? `<div>
      <div class="card-sub" style="font-weight:600;margin-bottom:6px">My App Limits</div>
      <div class="mini-app-list">${u.appLimits.apps
        .map((a) => {
          const allotted = Math.max(0, a.dailyLimit + (a.bonusToday || 0));
          const rem = Math.max(0, allotted - a.spentToday);
          const pct = Math.min(100, Math.round((a.spentToday / Math.max(1, allotted)) * 100));
          const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
          return `<div class="mini-app-row">
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px"><strong>${UI.escHtml(a.label)}</strong><span style="font-family:var(--font-mono);font-size:12px"><strong>${UI.fmtDur(rem)}</strong> left of ${UI.fmtDur(allotted)}</span></div>
          <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
        </div>`;
        })
        .join("")}</div></div>`
        : "";

    const curfewTxt = UI.todayCurfew(u);
    const subInfo =
      curfewTxt === "None" && u.todayLimitSec >= 86400
        ? "No screen-time limits are currently active for your account."
        : `Your screen locks automatically at <strong>${curfewTxt}</strong> or when your daily allowance reaches 0m. Open apps stay saved!`;

    return `<div class="centered-view"><div class="my-card">
      <div style="display:flex;align-items:center;gap:10px">${UI.ICONS.brand}<div><div style="font-size:17px;font-weight:700">My Screen Time</div><div class="card-sub">Signed in as @${UI.escHtml(u.username)}</div></div></div>
      <div class="hero-wrap" style="justify-content:center;padding:4px 0">
        ${renderRing(u, 152)}
        <div class="hero-stats">
          <div style="font-size:15px;font-weight:600">${UI.greetingPhrase()}, ${UI.escHtml(firstName)}</div>
          <div class="card-sub">${subInfo}</div>
          <div class="metric-grid">
            <div class="metric-box"><div class="metric-lbl">Used Today</div><div class="metric-val">${UI.fmtDur(u.spentToday)}</div></div>
            <div class="metric-box"><div class="metric-lbl">Curfew</div><div class="metric-val">${curfewTxt}</div></div>
          </div>
        </div>
      </div>
      <div><div class="card-sub" style="font-weight:600;margin-bottom:6px">Today's Allowed Hours</div>${renderWindowBar("my", u.todayStartMin, u.todayEndMin, { editable: false, showNow: true })}</div>
      ${appRows}
      <div><div class="card-sub" style="font-weight:600;margin-bottom:4px">My Past 7 Days</div>${renderUsageChart(UI.state.myHistory7, 7)}</div>
    </div></div>`;
  }

  function renderAuthGate() {
    return `<div class="centered-view"><div class="auth-card">
      ${UI.ICONS.shieldKey}
      <h2 style="margin:4px 0 4px;font-size:20px">Authentication Required</h2>
      <div style="display:flex;flex-direction:column;gap:8px;width:100%;margin-top:6px">
        <button type="button" class="btn btn-filled" id="btnAuth">Authenticate as Administrator</button>
        <button type="button" class="btn btn-outlined" id="btnSelf">View My Own Screen Time (No Password)</button>
      </div>
    </div></div>`;
  }

  function renderUnavailable(msg) {
    return `<div class="centered-view"><div class="auth-card">
      ${UI.ICONS.shieldKey}
      <h2 style="margin:4px 0 4px;font-size:20px">Service Unavailable</h2>
      <div class="card-sub">${UI.escHtml(msg || "The screen-time service is not currently running on this computer.")}</div>
      <div style="display:flex;flex-direction:column;gap:8px;width:100%;margin-top:6px">
        <button type="button" class="btn btn-filled" id="btnRetry">Retry Connection</button>
      </div>
    </div></div>`;
  }

  Object.assign(UI, {
    renderAdminShell,
    renderMyScreenTime,
    renderAuthGate,
    renderUnavailable,
  });
})((window.CurfewUI = window.CurfewUI || {}));
