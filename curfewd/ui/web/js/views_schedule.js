/* Schedule, App Limits, Category Budgets, System Health & Settings renderers for curfewd-ui (Spec 006). */
(function (UI) {
  "use strict";

  function renderScheduleDayRow(key, label, dObj, showTicks, showCopy = false) {
    const wins = dObj.windows || [];
    return `<div class="day-row">
      <div class="day-top">
        <div class="day-name">${UI.escHtml(label)}</div>
        <div class="day-hours-wrap">
          <span class="day-hours-lbl">Daily limit</span>
          <input class="thin-slider" type="range" min="0" max="${UI.SLIDER_UNLIMITED_VAL}" step="15" value="${UI.secToSliderVal(dObj.limit)}" data-daylim="${UI.escHtml(key)}" aria-label="${UI.escHtml(label)} daily time limit"/>
          <span class="day-hours-val" data-daylimval="${UI.escHtml(key)}">${UI.fmtDur(dObj.limit)}</span>
        </div>
        <div style="display:flex;gap:5px;flex-shrink:0">
          ${wins.length < 3 ? `<button type="button" class="btn btn-outlined btn-sm" data-addbreak="${UI.escHtml(key)}" style="padding:3px 9px;font-size:11.5px">+ Break</button>` : ""}
          <button type="button" class="btn btn-outlined btn-sm" data-toggleblock="${UI.escHtml(key)}" style="padding:3px 9px;font-size:11.5px">${wins.length === 0 ? "Allow" : "Block"}</button>
          ${showCopy ? `<button type="button" class="btn btn-outlined btn-sm" data-copyday="${UI.escHtml(key)}" style="padding:3px 9px;font-size:11.5px">Copy...</button>` : ""}
        </div>
      </div>
      ${UI.renderWindowBar(key, wins, { editable: true, showNow: false, compact: true, showTicks })}
    </div>`;
  }

  function renderScheduleTab(u) {
    const mode = u.scheduleMode || UI.detectScheduleMode(u.days);
    let rowsHtml = "";
    if (mode === "same") {
      rowsHtml = renderScheduleDayRow("all", "Every Day", u.days.mon, true, false);
    } else if (mode === "weekday_weekend") {
      rowsHtml =
        renderScheduleDayRow("weekdays", "School Days (Mon–Fri)", u.days.mon, false, false) +
        renderScheduleDayRow("weekends", "Weekends (Sat–Sun)", u.days.sat, true, false);
    } else {
      rowsHtml = UI.DAYS.map((d, i) =>
        renderScheduleDayRow(d, UI.DAY_FULL[d], u.days[d], i === UI.DAYS.length - 1, true)
      ).join("");
    }

    const weekHours = u.weeklyLimit ? Math.round(u.weeklyLimit / 3600) : 0;
    const monthHours = u.monthlyLimit ? Math.round(u.monthlyLimit / 3600) : 0;

    return `<div class="card">
      <div class="card-hdr">
        <div>
          <h3 class="card-title">Weekly Schedule</h3>
          <div class="card-sub">Supports split allowed windows (+ Break for school/dinner hours) and stays queued until Save</div>
        </div>
        <div class="segmented" role="group" aria-label="Schedule grouping">
          <button type="button" class="seg-btn ${mode === "same" ? "active" : ""}" data-schedmode="same">Every Day</button>
          <button type="button" class="seg-btn ${mode === "weekday_weekend" ? "active" : ""}" data-schedmode="weekday_weekend">School &amp; Weekend</button>
          <button type="button" class="seg-btn ${mode === "custom" ? "active" : ""}" data-schedmode="custom">Custom</button>
        </div>
      </div>
      <div class="day-stack">${rowsHtml}</div>
    </div>

    <div class="grid-eq">
      <div class="card" style="display:flex;flex-direction:column;gap:12px">
        <div class="card-hdr" style="margin-bottom:0">
          <h3 class="card-title">Holidays &amp; Vacation Overrides</h3>
          <button type="button" class="btn btn-tonal btn-sm" id="btnAddHoliday">${UI.ICONS.plus} Add Holiday</button>
        </div>
        <div class="card-sub">Automatically switch to Weekend limits or pause limits during school breaks</div>
        ${
          u.holidays.length
            ? `<div class="check-list">${u.holidays
                .map(
                  (h, idx) => `<div class="check-row" style="padding:10px 12px">
              <div>
                <div style="font-weight:600;font-size:13px">${UI.escHtml(h.label || "Holiday")} <span class="pill p-info" style="font-size:10.5px">${UI.escHtml(h.mode)}</span></div>
                <div class="card-sub" style="font-family:var(--font-mono);font-size:11.5px">${UI.escHtml(h.start_date)} → ${UI.escHtml(h.end_date)}</div>
              </div>
              <button type="button" class="btn btn-outlined btn-sm" data-rmhol="${idx}" title="Remove">${UI.ICONS.trash}</button>
            </div>`
                )
                .join("")}</div>`
            : `<div class="card-sub">No upcoming holiday overrides scheduled.</div>`
        }
      </div>

      <div class="card" style="display:flex;flex-direction:column;gap:14px">
        <h3 class="card-title">Weekly Caps &amp; Account Options</h3>
        <div class="rule-row">
          <div><strong>Weekly Limit Cap</strong><div class="card-sub">Optional total hours across Mon–Sun</div></div>
          <select class="text-in" id="selWeeklyCap" style="width:auto">
            <option value="0" ${weekHours === 0 ? "selected" : ""}>Off</option>
            ${[5, 7, 10, 12, 14, 15, 20, 25, 30]
              .map(
                (h) =>
                  `<option value="${h * 3600}" ${weekHours === h ? "selected" : ""}>${h}h / week</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="rule-row">
          <div><strong>Monthly Limit Cap</strong><div class="card-sub">Optional total hours per calendar month</div></div>
          <select class="text-in" id="selMonthlyCap" style="width:auto">
            <option value="0" ${monthHours === 0 ? "selected" : ""}>Off</option>
            ${[20, 30, 40, 50, 60, 80, 100]
              .map(
                (h) =>
                  `<option value="${h * 3600}" ${monthHours === h ? "selected" : ""}>${h}h / month</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="rule-row">
          <div><strong>Account Role</strong><div class="card-sub">Parents stay unmanaged &amp; grouped under Other Accounts</div></div>
          <div class="segmented">
            <button type="button" class="seg-btn ${u.role !== "parent" ? "active" : ""}" data-setrole="child">Managed Child</button>
            <button type="button" class="seg-btn ${u.role === "parent" ? "active" : ""}" data-setrole="parent">Parent</button>
          </div>
        </div>
        <div class="rule-row">
          <div><strong>2-Minute Save Grace</strong><div class="card-sub">Lets child claim one 2-minute window/day to save work cleanly</div></div>
          <button type="button" class="m3-switch ${u.saveGraceEnabled ? "on" : ""}" id="toggleSaveGrace" aria-label="Toggle 2-Minute Save Grace"></button>
        </div>
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
        <div>
          <h3 class="card-title">Shared Category Budgets</h3>
          <div class="card-sub">Cap an entire category together (e.g., 1h total across Steam, Minecraft &amp; Roblox)</div>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span class="card-sub">${al.enabled ? "Active" : "Disabled"}</span>
          <button type="button" class="m3-switch ${al.enabled ? "on" : ""}" id="toggleApps" aria-label="Toggle App Limits"></button>
          <button type="button" class="btn btn-tonal btn-sm" id="btnAddCategory">${UI.ICONS.plus} Add Category</button>
        </div>
      </div>
      <div class="app-list" style="${al.enabled ? "" : "opacity:.55"}">
        ${
          al.categories.length
            ? al.categories
                .map((c, idx) => {
                  const base = c.effectiveLimit !== undefined ? c.effectiveLimit : c.dailyLimit;
                  const allotted = Math.max(0, base + (c.bonusToday || 0));
                  const rem = Math.max(0, allotted - c.spentToday);
                  const pct =
                    allotted === 0
                      ? 100
                      : Math.min(100, Math.round((c.spentToday / Math.max(1, allotted)) * 100));
                  const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
                  return `<div class="app-card">
            <div class="app-top">
              <div>
                <div style="font-weight:600;display:flex;align-items:center;gap:6px">All ${UI.escHtml(c.category)} ${c.running ? `<span class="pill p-info">Running</span>` : ""}</div>
                <div class="card-sub" style="font-size:12px">Weekday: ${UI.fmtAppLimit(c.dailyLimit)}${c.weekendLimit !== null ? ` · Weekend: ${UI.fmtAppLimit(c.weekendLimit)}` : ""}</div>
              </div>
              <span class="pill ${rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok"}" data-catpill="${idx}">${allotted === 0 ? "Blocked" : rem === 0 ? "Limit reached" : `${UI.fmtDur(rem)} left`}</span>
            </div>
            <div class="app-prog"><div class="app-prog-fill" data-catfill="${idx}" style="width:${pct}%;background:${col}"></div></div>
            <div class="app-ctrl">
              <span class="card-sub">Used: <strong data-catused="${idx}">${UI.fmtDur(c.spentToday)}</strong> / ${UI.fmtAppLimit(allotted)}</span>
              <div style="display:flex;gap:6px">
                <button type="button" class="btn btn-tonal btn-sm" data-catgrant="${idx}" ${al.enabled ? "" : "disabled"}>+15m</button>
                <button type="button" class="btn btn-outlined btn-sm" data-rmcat="${idx}" title="Remove">${UI.ICONS.trash}</button>
              </div>
            </div>
          </div>`;
                })
                .join("")
            : `<div class="card-sub">No shared category budgets configured. Add one to pool all games or social apps under a single timer.</div>`
        }
      </div>
    </div>

    <div class="card">
      <div class="card-hdr" style="margin-bottom:12px">
        <div>
          <h3 class="card-title">Individual App Limits (${al.apps.length})</h3>
          <div class="card-sub">Slide to 0m to block an app completely; parents get advance 5m/1m kid warnings before closing</div>
        </div>
        <button type="button" class="btn btn-tonal btn-sm" id="btnAddApp">${UI.ICONS.plus} Add App / Game</button>
      </div>
      <div class="app-list" style="${al.enabled ? "" : "opacity:.55"}">
        ${
          al.apps.length
            ? al.apps
                .map((a, idx) => {
                  const base = a.effectiveLimit !== undefined ? a.effectiveLimit : a.dailyLimit;
                  const allotted = Math.max(0, base + (a.bonusToday || 0));
                  const rem = Math.max(0, allotted - a.spentToday);
                  const pct =
                    allotted === 0
                      ? 100
                      : Math.min(100, Math.round((a.spentToday / Math.max(1, allotted)) * 100));
                  const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
                  const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
                  return `<div class="app-card">
            <div class="app-top">
              <div>
                <div style="font-weight:600;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                  ${UI.escHtml(a.label)}
                  ${a.running ? `<span class="pill p-info">Running</span>` : ""}
                  ${a.blockedByHomework ? `<span class="pill p-warn">Homework Blocked</span>` : ""}
                </div>
                <div class="card-sub" style="font-size:12px">${UI.escHtml(a.category || "App")}${a.weekendLimit !== null ? ` · Weekend: ${UI.fmtAppLimit(a.weekendLimit)}` : ""}</div>
              </div>
              <span class="pill ${pCls}" data-apppill="${idx}">${allotted === 0 ? "Blocked" : rem === 0 ? "Limit reached" : `${UI.fmtDur(rem)} left`}</span>
            </div>
            <div class="app-prog"><div class="app-prog-fill" data-appfill="${idx}" style="width:${pct}%;background:${col}"></div></div>
            <div class="app-meta">
              <span>Limit: <strong data-applimlbl="${idx}">${UI.fmtAppLimit(a.dailyLimit)}</strong>${a.bonusToday ? ` <span style="color:var(--ok)">(+${UI.fmtDur(a.bonusToday)})</span>` : ""}</span>
              <span>Used: <strong data-appused="${idx}">${UI.fmtDur(a.spentToday)}</strong> · Left: <strong data-apprem="${idx}">${UI.fmtDur(rem)}</strong></span>
            </div>
            <div class="app-ctrl">
              <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:120px">
                <span class="card-sub" style="white-space:nowrap">Weekday</span>
                <input class="slider" style="width:100%;max-width:130px" type="range" min="0" max="240" step="15" value="${Math.max(0, Math.min(240, Math.round(a.dailyLimit / 60)))}" data-applimit="${idx}" ${al.enabled ? "" : "disabled"}/>
              </div>
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                <button type="button" class="chip-btn ${a.foregroundOnly ? "active" : ""}" data-appfg="${idx}" title="Only count time while window is focused (prevents minimized Steam drain)">Focused Only</button>
                <button type="button" class="chip-btn ${a.alwaysAllowed ? "active" : ""}" data-appallow="${idx}" title="Allow during Homework Mode">Study OK</button>
                <button type="button" class="btn btn-tonal btn-sm" data-appgrant="${idx}" ${al.enabled ? "" : "disabled"}>+15m</button>
                <button type="button" class="btn btn-outlined btn-sm" data-rmapp="${idx}" title="Remove">${UI.ICONS.trash}</button>
              </div>
            </div>
          </div>`;
                })
                .join("")
            : `<div class="card-sub">No limited apps configured yet. Click "Add App / Game" to pick an installed app from this user's desktop.</div>`
        }
      </div>
    </div>`;
  }

  function renderDoctorView() {
    const checks = UI.translateDoctorChecks(UI.state.doctor);
    const needsFix = UI.doctorNeedsLockFix();
    const allOk = checks.every((c) => c.ok);
    const sessionMode = (UI.state.doctor && UI.state.doctor.session_mode) || "none";
    const activeUsers = Object.values(UI.state.users).filter((u) => u.loggedIn);

    return `<div class="page-hdr">
      <div>
        <h1 class="page-title">System Health</h1>
        <div class="page-sub">Multi-DE lock protection &amp; diagnostics${sessionMode !== "none" ? ` · Display: ${UI.escHtml(sessionMode.toUpperCase())}` : ""}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="btn btn-tonal" id="btnTestLock" ${activeUsers.length ? "" : "disabled"}>${UI.ICONS.lock} Test Lock Screen</button>
        ${
          allOk
            ? `<span class="pill p-ok">All Checks Passing</span>`
            : needsFix
              ? `<button type="button" class="btn btn-filled" id="btnDocFix" ${UI.state.busyFix ? "disabled" : ""}>${UI.ICONS.doctor} ${UI.state.busyFix ? "Protecting..." : "Protect Lock Screen"}</button>`
              : `<button type="button" class="btn btn-outlined" id="btnDocRefresh">Re-check</button>`
        }
      </div>
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
      idle_pause_sec: 300,
      warning_chime_enabled: true,
      countdown_hud_enabled: true,
      passive_app_insights_enabled: true,
    };
    const curTheme = UI.state.prefs.theme || "auto";
    const curAcc = UI.state.prefs.accent || "indigo";
    const curTF = UI.state.prefs.time_format || "auto";
    const pc = UI.state.parentCode || { enabled: false };
    const enroll = UI.state.parentCodeEnrollment;

    return `<div class="page-hdr"><div><h1 class="page-title">Settings</h1><div class="page-sub">Screen locking, kid fairness, offline Parent Access Code &amp; appearance</div></div></div>
    <div class="grid-eq">
      <div class="card" style="display:flex;flex-direction:column;gap:14px">
        <h3 class="card-title">Screen Locking &amp; Fairness</h3>
        <div class="rule-row">
          <div><strong>Enhanced Screen Lock</strong><div class="card-sub">Uses extra fallback checks so the lock screen always engages</div></div>
          <button type="button" class="m3-switch ${cfg.session_lock_fallback ? "on" : ""}" data-cbool="session_lock_fallback" aria-label="Enhanced Screen Lock"></button>
        </div>
        <div class="rule-row">
          <div><strong>Fullscreen Game Compatibility</strong><div class="card-sub">Safely releases X11 fullscreen games before locking without closing them</div></div>
          <button type="button" class="m3-switch ${cfg.release_x11_grab_on_lock ? "on" : ""}" data-cbool="release_x11_grab_on_lock" aria-label="Fullscreen Game Compatibility"></button>
        </div>
        <div class="rule-row">
          <div><strong>Pause Timer When Idle</strong><div class="card-sub">Stops draining screen time if the child walks away from the keyboard</div></div>
          <select class="text-in" id="cfgIdleSelect" style="width:auto">
            ${[
              [0, "Off"],
              [120, "After 2m"],
              [300, "After 5m"],
              [600, "After 10m"],
              [900, "After 15m"],
            ]
              .map(
                ([v, l]) =>
                  `<option value="${v}" ${Number(cfg.idle_pause_sec ?? 300) === v ? "selected" : ""}>${l}</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="rule-row">
          <div><strong>Warning Audio Chimes</strong><div class="card-sub">Plays a soft chime at 5m &amp; 1m left (audible over fullscreen games)</div></div>
          <button type="button" class="m3-switch ${cfg.warning_chime_enabled !== false ? "on" : ""}" data-cbool="warning_chime_enabled" aria-label="Warning Audio Chimes"></button>
        </div>
        <div class="rule-row">
          <div><strong>60-Second Corner Countdown HUD</strong><div class="card-sub">Compact non-intrusive countdown pill during the final minute</div></div>
          <button type="button" class="m3-switch ${cfg.countdown_hud_enabled !== false ? "on" : ""}" data-cbool="countdown_hud_enabled" aria-label="Countdown HUD"></button>
        </div>
        <div class="rule-row">
          <div><strong>Top Apps Usage Insights</strong><div class="card-sub">Records daily per-app summaries so you can see where time goes</div></div>
          <button type="button" class="m3-switch ${cfg.passive_app_insights_enabled !== false ? "on" : ""}" data-cbool="passive_app_insights_enabled" aria-label="Passive App Insights"></button>
        </div>
        <div class="rule-row">
          <div><strong>Final Warning Countdown</strong><div class="card-sub">Alert duration right before the screen locks</div></div>
          <div style="display:flex;align-items:center;gap:10px">
            <input class="thin-slider" style="width:110px" type="range" min="0" max="60" step="5" value="${Number(cfg.final_countdown_sec ?? 15)}" id="cfgCountdownSlider" aria-label="Final Warning Countdown"/>
            <span class="pill p-info" id="cfgCountdownVal">${Number(cfg.final_countdown_sec ?? 15)}s</span>
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card" style="display:flex;flex-direction:column;gap:12px">
          <div class="card-hdr" style="margin-bottom:0">
            <h3 class="card-title">Offline Parent Access Code</h3>
            <span class="pill ${pc.enabled ? "p-ok" : "p-muted"}">${pc.enabled ? "Enabled" : "Not Set Up"}</span>
          </div>
          <div class="card-sub">Approve walk-up +15m requests on a child's desktop using a 6-digit code from your phone's authenticator app — 100% offline, never leaks your Linux password.</div>
          ${
            enroll && enroll.enabled && enroll.qr_svg
              ? `<div class="qr-enroll-box">
            <div class="qr-frame">${enroll.qr_svg}</div>
            <div style="flex:1;min-width:160px">
              <div style="font-weight:600;font-size:13px;margin-bottom:4px">Scan with Google Authenticator, Apple Passwords, or Aegis</div>
              <div class="card-sub" style="margin-bottom:6px">Or enter this setup key manually:</div>
              <code class="setup-key">${UI.escHtml(enroll.secret_b32)}</code>
            </div>
          </div>`
              : ""
          }
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${
              pc.enabled
                ? `<button type="button" class="btn btn-tonal btn-sm" id="btnPcRotate">${UI.ICONS.key} Rotate &amp; Show QR</button>
                   <button type="button" class="btn btn-outlined btn-sm" id="btnPcDisable">Disable</button>`
                : `<button type="button" class="btn btn-filled btn-sm" id="btnPcEnable">${UI.ICONS.key} Set Up Parent Code</button>`
            }
          </div>
        </div>

        <div class="card" style="display:flex;flex-direction:column;gap:14px">
          <h3 class="card-title">Appearance &amp; Display</h3>
          <div class="rule-row">
            <div><strong>Color Mode</strong><div class="card-sub">Follows system Light/Dark by default</div></div>
            <div class="segmented">
              <button type="button" class="seg-btn ${curTheme === "auto" ? "active" : ""}" data-settheme="auto">Auto</button>
              <button type="button" class="seg-btn ${curTheme === "light" ? "active" : ""}" data-settheme="light">Light</button>
              <button type="button" class="seg-btn ${curTheme === "dark" ? "active" : ""}" data-settheme="dark">Dark</button>
            </div>
          </div>
          <div class="rule-row">
            <div><strong>Time Format</strong><div class="card-sub">12-hour AM/PM or 24-hour clock</div></div>
            <div class="segmented">
              <button type="button" class="seg-btn ${curTF === "auto" ? "active" : ""}" data-settf="auto">Auto</button>
              <button type="button" class="seg-btn ${curTF === "12h" ? "active" : ""}" data-settf="12h">12h</button>
              <button type="button" class="seg-btn ${curTF === "24h" ? "active" : ""}" data-settf="24h">24h</button>
            </div>
          </div>
          <div class="rule-row">
            <div><strong>Accent Color</strong><div class="card-sub">Choose highlight palette</div></div>
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
      </div>
    </div>`;
  }

  Object.assign(UI, {
    renderScheduleTab,
    renderAppLimitsTab,
    renderDoctorView,
    renderSettingsView,
  });
})((window.CurfewUI = window.CurfewUI || {}));
