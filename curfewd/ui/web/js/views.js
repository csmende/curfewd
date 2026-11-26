/* Primary Material Design 3 HTML renderers for curfewd-ui (Admin Shell, Overview, Wizard & Kid View) (Spec 006). */
(function (UI) {
  "use strict";

  function renderRing(u, size = 152) {
    const left = UI.timeLeftSec(u);
    const lim = u.todayLimitSec;
    const isPaused = u.enforcementState === "paused";
    const isHomework = u.enforcementState === "homework";
    const r = 58;
    const c = 2 * Math.PI * r;
    const pct =
      isPaused || isHomework || lim >= 86400
        ? 1
        : Math.max(0, Math.min(1, left / Math.max(1, lim)));
    const col = isHomework
      ? "var(--info)"
      : isPaused
        ? "var(--ok)"
        : left === 0
          ? "var(--err)"
          : left <= 600
            ? "var(--warn)"
            : "var(--p)";
    const mainTxt = isHomework ? "Study" : isPaused ? "Paused" : UI.fmtDur(left);
    const subTxt = isHomework
      ? "homework mode"
      : isPaused
        ? "limits paused"
        : lim >= 86400
          ? "no daily limit"
          : `left of ${UI.fmtDur(lim)}`;
    return `<div class="ring-box" style="width:${size}px;height:${size}px">
      <svg width="${size}" height="${size}" viewBox="0 0 148 148">
        <circle cx="74" cy="74" r="${r}" fill="none" stroke="var(--s3)" stroke-width="11"/>
        <circle id="myRingArc" cx="74" cy="74" r="${r}" fill="none" stroke="${col}" stroke-width="11" stroke-linecap="round"
          stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - pct)).toFixed(1)}" transform="rotate(-90 74 74)"/>
      </svg>
      <div class="ring-center">
        <div class="ring-main" id="myRingMain" style="color:${col}">${mainTxt}</div>
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
    const bw = Math.min(34, step * 0.56);
    const labelEvery = spanDays > 14 ? 4 : spanDays > 7 ? 2 : 1;

    const bars = rows
      .map((row, idx) => {
        const x = padL + idx * step + (step - bw) / 2;
        const bh = row.act > 0 ? Math.max(4, (row.act / maxVal) * ch) : 2;
        const y = padT + ch - bh;
        const pth = row.pt > 0 ? Math.min(bh, Math.max(3, (row.pt / maxVal) * ch)) : 0;
        const pty = padT + ch - pth;
        const showLbl = idx % labelEvery === 0 || idx === rows.length - 1;
        return `<g><title>${UI.escHtml(row.d)}: ${row.act}m total${row.pt > 0 ? ` (${row.pt}m limited apps)` : ""}</title>
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="5" fill="var(--p)" opacity="${row.act > 0 ? ".78" : ".22"}"/>
        ${pth > 0 ? `<rect x="${(x + 2).toFixed(1)}" y="${pty.toFixed(1)}" width="${Math.max(3, bw - 4).toFixed(1)}" height="${pth.toFixed(1)}" rx="3" fill="var(--on-pc)" opacity=".5"/>` : ""}
        ${showLbl ? `<text x="${(x + bw / 2).toFixed(1)}" y="${h - 7}" text-anchor="middle" fill="var(--fg-muted)" font-size="10.5" font-family="var(--font-mono)">${UI.escHtml(row.d)}</text>` : ""}
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

  /** Multi-window draggable 24h allowed-hours timeline bar. */
  function renderWindowBar(
    key,
    windows,
    { editable = false, showNow = false, compact = false, showTicks = true } = {}
  ) {
    const wins = Array.isArray(windows) ? windows : [];
    const nowPct = (UI.nowMinutes() / 1440) * 100;

    const segsHtml = wins
      .map((w, idx) => {
        const sp = (w.startMin / 1440) * 100;
        const ep = (w.endMin / 1440) * 100;
        const wp = Math.max(0, ep - sp);
        return `<div class="timeline-seg" data-seg="${idx}" style="left:${sp}%;width:${wp}%"></div>`;
      })
      .join("");

    const handlesHtml = editable
      ? wins
          .map((w, idx) => {
            const sp = (w.startMin / 1440) * 100;
            const ep = (w.endMin / 1440) * 100;
            return `
          <button type="button" class="timeline-handle" data-seg="${idx}" data-edge="start" style="left:${sp}%" aria-label="Adjust window ${idx + 1} start">
            <span class="handle-tip">${UI.escHtml(UI.fmtTimeMin(w.startMin))}</span>
          </button>
          <button type="button" class="timeline-handle" data-seg="${idx}" data-edge="end" style="left:${ep}%" aria-label="Adjust window ${idx + 1} end">
            <span class="handle-tip">${UI.escHtml(UI.fmtTimeMin(w.endMin))}</span>
          </button>`;
          })
          .join("")
      : "";

    const blockedOverlay =
      wins.length === 0 ? `<div class="timeline-blocked-lbl">Blocked all day</div>` : "";

    const ticks = [0, 360, 720, 1080, 1440]
      .map((m) => `<span>${UI.escHtml(m === 1440 ? UI.fmtTimeMin(0) : UI.fmtTimeMin(m))}</span>`)
      .join("");

    return `<div>
      <div class="timeline-bar ${editable ? "editable" : ""} ${compact ? "compact" : ""}" ${editable ? `data-winbar="${UI.escHtml(key)}"` : ""}>
        <div class="timeline-track">${segsHtml}${blockedOverlay}</div>
        ${showNow ? `<div class="timeline-needle" style="left:${nowPct.toFixed(2)}%" title="Current time (${UI.escHtml(UI.nowHHMM())})"></div>` : ""}
        ${handlesHtml}
      </div>
      ${showTicks ? `<div class="timeline-ticks">${ticks}</div>` : ""}
    </div>`;
  }

  function formatAuditEvent(ev) {
    const d = ev.detail || {};
    const by = ev.actor && ev.actor !== "daemon" ? ` by ${ev.actor}` : "";
    switch (ev.kind) {
      case "grant": {
        const dur = UI.fmtDur(Math.abs(Number(d.applied_sec || 0)));
        const sign = Number(d.applied_sec || 0) >= 0 ? "+" : "-";
        const note = d.note ? ` — "${d.note}"` : "";
        return { tone: "p-ok", label: "Bonus", text: `${sign}${dur} screen time${by}${note}` };
      }
      case "app_grant":
        return {
          tone: "p-ok",
          label: "App Bonus",
          text: `+${UI.fmtDur(Number(d.applied_sec || 0))} for ${d.label || d.pattern || "app"}${by}`,
        };
      case "category_grant":
        return {
          tone: "p-ok",
          label: "Category",
          text: `+${UI.fmtDur(Number(d.applied_sec || 0))} for ${d.category || "category"}${by}`,
        };
      case "lockout":
        return { tone: "p-err", label: "Locked", text: `Time expired (${d.action || "lock"})` };
      case "lock_manual":
        return { tone: "p-err", label: "Locked", text: `Screen locked manually${by}` };
      case "lock_test":
        return { tone: "p-info", label: "Test Lock", text: `Lock screen verified${by}` };
      case "app_limit_kill": {
        const apps = (d.apps || []).join(", ") || "App";
        const why = d.reason === "homework_mode" ? "during Homework Mode" : "reached daily limit";
        return { tone: "p-warn", label: "App Closed", text: `${apps} ${why}` };
      }
      case "pause":
        return {
          tone: "p-info",
          label: "Paused",
          text: `Limits paused${by}${d.reason ? ` (${d.reason})` : ""}`,
        };
      case "resume":
        return { tone: "p-muted", label: "Resumed", text: `Regular schedule resumed${by}` };
      case "homework_start":
        return {
          tone: "p-info",
          label: "Homework",
          text: `Homework Mode started${by}${d.reason ? ` (${d.reason})` : ""}`,
        };
      case "homework_end":
        return { tone: "p-muted", label: "Homework", text: `Homework Mode ended${by}` };
      case "save_grace":
        return { tone: "p-warn", label: "Save Grace", text: "Used 2-minute Save Your Work grace" };
      case "time_request":
        return {
          tone: "p-info",
          label: "Request",
          text: `Asked for +${UI.fmtDur(Number(d.requested_sec || 900))} (${d.target_label || "Screen Time"})${d.reason ? `: "${d.reason}"` : ""}`,
        };
      case "time_request_approved":
        return {
          tone: "p-ok",
          label: "Approved",
          text: `Approved +${UI.fmtDur(Number(d.granted_sec || 0))}${by}`,
        };
      case "time_request_denied":
        return { tone: "p-muted", label: "Declined", text: `Declined time request${by}` };
      case "today_override":
        return {
          tone: "p-info",
          label: "Today",
          text: d.reset ? `Today's schedule reset${by}` : `Today's schedule adjusted${by}`,
        };
      case "policy_update":
        return { tone: "p-muted", label: "Schedule", text: `Schedule updated${by}` };
      default:
        return { tone: "p-muted", label: ev.kind || "Event", text: `${ev.kind}${by}` };
    }
  }

  function renderPendingRequestsBanner(u) {
    const reqs = UI.pendingRequestsForUser(u.username);
    if (!reqs.length) return "";
    return `<div class="request-banner">
      <div class="card-hdr" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;font-weight:700">${UI.ICONS.bell} ${UI.escHtml(u.fullName)} asked for more time (${reqs.length})</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${reqs
          .map(
            (r) => `<div class="request-row">
          <div style="flex:1;min-width:180px">
            <div><strong>+${UI.fmtDur(r.requested_sec)}</strong> for <strong>${UI.escHtml(r.target_label || "Daily Screen Time")}</strong></div>
            <div class="card-sub">${r.reason ? `"${UI.escHtml(r.reason)}" · ` : ""}${UI.escHtml(UI.fmtRelativeEventTime(r.created_utc))}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button type="button" class="btn btn-filled btn-sm" data-reqapprove="${r.id}" data-reqsec="${r.requested_sec}">Approve +${UI.fmtDur(r.requested_sec)}</button>
            ${r.requested_sec !== 900 ? `<button type="button" class="btn btn-tonal btn-sm" data-reqapprove="${r.id}" data-reqsec="900">+15m</button>` : ""}
            <button type="button" class="btn btn-outlined btn-sm" data-reqdeny="${r.id}">Decline</button>
          </div>
        </div>`
          )
          .join("")}
      </div>
    </div>`;
  }

  function renderTopAppsCard(u, histObj, editable = true) {
    const topApps = (histObj && histObj.top_apps) || [];
    if (!topApps.length) return "";
    const limitedPats = new Set();
    (u.appLimits.apps || []).forEach((a) => {
      if (a.pattern) limitedPats.add(a.pattern.toLowerCase());
      if (a.steamAppId) limitedPats.add(`steam:${String(a.steamAppId).toLowerCase()}`);
      if (a.flatpakId) limitedPats.add(String(a.flatpakId).toLowerCase());
    });
    const maxSec = Math.max(60, ...topApps.map((a) => Number(a.active_sec || 0)));
    const spanDays = Number((histObj && histObj.days) || UI.state.historySpan || 7);

    return `<div class="card-section">
      <div class="sec-hdr">
        <h4 class="sec-title">Top Apps Used (${spanDays} Days)</h4>
        <span class="card-sub">Passive usage insights</span>
      </div>
      <div class="insights-list">
        ${topApps
          .slice(0, 8)
          .map((a) => {
            const pct = Math.max(4, Math.round((Number(a.active_sec || 0) / maxSec) * 100));
            const isLimited = limitedPats.has(String(a.app_key || "").toLowerCase());
            return `<div class="insight-row">
            <div style="flex:1;min-width:0">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px">
                <div style="display:flex;align-items:center;gap:6px;min-width:0">
                  <strong style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${UI.escHtml(a.label)}</strong>
                  <span class="pill p-muted" style="font-size:10.5px">${UI.escHtml(a.category || "App")}</span>
                </div>
                <span style="font-family:var(--font-mono);font-size:12px;white-space:nowrap">${UI.fmtDur(a.active_sec)} <span style="opacity:.7">(${UI.fmtDur(a.daily_avg_sec)}/day)</span></span>
              </div>
              <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:var(--p)"></div></div>
            </div>
            ${
              editable
                ? isLimited
                  ? `<span class="pill p-ok" style="flex-shrink:0">Limited</span>`
                  : `<button type="button" class="btn btn-tonal btn-sm" data-quicklimit="${UI.escHtml(a.app_key)}" data-qllabel="${UI.escHtml(a.label)}" data-qlcat="${UI.escHtml(a.category || "App")}">Set Limit</button>`
                : ""
            }
          </div>`;
          })
          .join("")}
      </div>
    </div>`;
  }

  function renderOverviewTab(u) {
    const left = UI.timeLeftSec(u);
    const isPaused = u.enforcementState === "paused";
    const isHomework = u.enforcementState === "homework";
    const leftCls = isHomework
      ? "info"
      : isPaused
        ? "ok"
        : left === 0
          ? "err"
          : left <= 600
            ? "warn"
            : "primary";

    const pills = [];
    if (u.locked) pills.push(`<span class="pill p-err">Screen Locked</span>`);
    if (isPaused) {
      const until =
        u.pause && u.pause.until_utc ? ` until ${UI.fmtIsoClock(u.pause.until_utc)}` : "";
      pills.push(`<span class="pill p-ok">Paused${UI.escHtml(until)}</span>`);
    }
    if (isHomework) pills.push(`<span class="pill p-info">Homework Mode</span>`);
    if (u.idlePaused) pills.push(`<span class="pill p-muted">Idle (Timer Paused)</span>`);
    if (u.activeHoliday) {
      pills.push(
        `<span class="pill p-info">Holiday: ${UI.escHtml(u.activeHoliday.label || u.activeHoliday.mode)}</span>`
      );
    }
    if (!u.loggedIn) pills.push(`<span class="pill p-muted">Offline</span>`);
    if (u.lastBonusNote && u.bonusToday > 0) {
      pills.push(
        `<span class="pill p-ok">+${UI.fmtDur(u.bonusToday)} · ${UI.escHtml(u.lastBonusNote)}</span>`
      );
    }

    const histObj = UI.state.histories[u.username];
    const events = ((histObj && histObj.events) || []).slice(0, 8);
    const todayWins = u.todayWindows || [];

    return `${renderPendingRequestsBanner(u)}
    <div class="card">
      <div class="card-hdr">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <h3 class="card-title">Current Status</h3>
          ${pills.join("")}
        </div>
        <div class="quick-actions">
          <button type="button" class="btn btn-tonal btn-sm" id="btnQuick15">+15m</button>
          <button type="button" class="btn btn-tonal btn-sm" id="btnQuick30">+30m</button>
          <button type="button" class="btn btn-outlined btn-sm" id="btnGrantNote">+ Bonus</button>
          ${
            isPaused
              ? `<button type="button" class="btn btn-filled btn-sm" id="btnResumeUser">${UI.ICONS.play} Resume</button>`
              : `<button type="button" class="btn btn-outlined btn-sm" id="btnPauseModal">${UI.ICONS.pause} Pause</button>`
          }
          ${
            isHomework
              ? `<button type="button" class="btn btn-filled btn-sm" id="btnHomeworkOff">${UI.ICONS.book} End Homework</button>`
              : `<button type="button" class="btn btn-outlined btn-sm" id="btnHomeworkModal">${UI.ICONS.book} Homework</button>`
          }
          <button type="button" class="btn btn-danger btn-sm" id="btnLockModal">${UI.ICONS.lock} Lock</button>
        </div>
      </div>

      <div class="stat-pills">
        <div class="stat-pill ${leftCls}" id="pillLeftBox">
          <span class="stat-pill-lbl">Time Left Today</span>
          <span class="stat-pill-val" id="pillLeftVal">${isPaused ? "Paused" : isHomework ? "Study Mode" : UI.fmtDur(left)}</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-lbl">Spent Today</span>
          <span class="stat-pill-val" id="pillSpentVal">${UI.fmtDur(u.spentToday)}</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-lbl">Spent This Week</span>
          <span class="stat-pill-val" id="pillWeekVal">${UI.fmtDur(u.spentWeek)}</span>
        </div>
      </div>

      <div class="card-section">
        <div class="sec-hdr">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <h4 class="sec-title">Today's Time and Schedule</h4>
            ${u.todayOverrideActive ? `<span class="pill p-info">Today Override</span><button type="button" class="btn btn-outlined btn-sm" id="btnResetToday" style="padding:2px 10px;font-size:11.5px">Reset to Weekly</button>` : ""}
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="card-sub" id="todayNowLabel">Now: ${UI.escHtml(UI.nowHHMM())}</span>
            ${todayWins.length < 3 ? `<button type="button" class="btn btn-outlined btn-sm" data-addbreak="today" style="padding:3px 10px;font-size:11.5px">+ Break</button>` : ""}
            <button type="button" class="btn btn-outlined btn-sm" data-toggleblock="today" style="padding:3px 10px;font-size:11.5px">${todayWins.length === 0 ? "Allow Today" : "Block Today"}</button>
          </div>
        </div>
        <div class="day-row">
          <div class="day-top">
            <div class="day-hours-wrap">
              <span class="day-hours-lbl">Time allowed today</span>
              <input class="thin-slider" type="range" min="0" max="${UI.SLIDER_UNLIMITED_VAL}" step="15" value="${UI.secToSliderVal(u.todayLimitSec)}" id="todayLimSlider" aria-label="Time allowed today"/>
              <span class="day-hours-val" id="todayLimVal">${UI.fmtDur(u.todayLimitSec)}</span>
            </div>
          </div>
          ${renderWindowBar("today", todayWins, { editable: true, showNow: true, compact: false, showTicks: true })}
        </div>
      </div>

      <div class="card-section">
        <div class="sec-hdr">
          <h4 class="sec-title">Screen-Time &amp; App Limits History</h4>
          <div class="segmented">
            <button type="button" class="seg-btn ${UI.state.historySpan === 7 ? "active" : ""}" data-hspan="7">7 Days</button>
            <button type="button" class="seg-btn ${UI.state.historySpan === 14 ? "active" : ""}" data-hspan="14">14 Days</button>
            <button type="button" class="seg-btn ${UI.state.historySpan === 30 ? "active" : ""}" data-hspan="30">30 Days</button>
          </div>
        </div>
        ${renderUsageChart(histObj, UI.state.historySpan)}
      </div>

      ${renderTopAppsCard(u, histObj, true)}

      <div class="card-section">
        <div class="sec-hdr">
          <h4 class="sec-title">Recent Activity</h4>
          <span class="card-sub">Enforcement &amp; parent actions</span>
        </div>
        ${
          events.length
            ? `<div class="activity-list">${events
                .map((ev) => {
                  const f = formatAuditEvent(ev);
                  return `<div class="activity-row">
                <span class="pill ${f.tone}" style="min-width:78px;justify-content:center">${UI.escHtml(f.label)}</span>
                <span style="flex:1;font-size:13px">${UI.escHtml(f.text)}</span>
                <span class="card-sub" style="font-family:var(--font-mono);font-size:11.5px;white-space:nowrap">${UI.escHtml(UI.fmtRelativeEventTime(ev.ts_utc))}</span>
              </div>`;
                })
                .join("")}</div>`
            : `<div class="card-sub">No activity events recorded yet.</div>`
        }
      </div>
    </div>`;
  }

  function renderSetupWizard() {
    const allUsers = Object.values(UI.state.users);
    if (!UI.state.wizardInitialized) {
      UI.state.wizardInitialized = true;
      UI.state.wizardKids.clear();
      allUsers.forEach((u) => {
        if (u.role !== "parent" && u.username !== UI.state.currentUser) {
          UI.state.wizardKids.add(u.username);
        }
      });
    }
    const presets = [
      ["elementary", "Elementary", "1h school days · 2h weekends · 7:30 PM bedtime"],
      ["middle", "Middle School", "1h 30m school days · 2h 30m weekends · 9:00 PM curfew"],
      ["teen", "Teen", "2h 30m school days · 4h weekends · 10:30 PM curfew"],
    ];
    return `<div class="card" style="max-width:720px;margin:20px auto">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
        ${UI.ICONS.brand}
        <div>
          <h2 class="page-title" style="font-size:21px">Welcome to curfewd</h2>
          <div class="card-sub">Let's set up screen-time protection in under a minute.</div>
        </div>
      </div>

      <div class="card-section" style="margin-top:10px">
        <h4 class="sec-title" style="margin-bottom:8px">1. Which accounts belong to kids?</h4>
        <div class="wizard-users">
          ${allUsers
            .map((u) => {
              const checked = UI.state.wizardKids.has(u.username);
              return `<label class="m3-check wizard-user-pill ${checked ? "active" : ""}">
              <input type="checkbox" data-wizkid="${UI.escHtml(u.username)}" ${checked ? "checked" : ""}/>
              <span><strong>${UI.escHtml(u.fullName)}</strong> <span class="card-sub">@${UI.escHtml(u.username)}</span></span>
            </label>`;
            })
            .join("")}
        </div>
      </div>

      <div class="card-section">
        <h4 class="sec-title" style="margin-bottom:8px">2. Pick a starter schedule</h4>
        <div class="lockout-grid">
          ${presets
            .map(
              ([
                id,
                title,
                desc,
              ]) => `<button type="button" class="lockout-opt ${UI.state.wizardPreset === id ? "active" : ""}" data-wizpreset="${id}">
            <div style="font-weight:600;margin-bottom:3px">${title}</div>
            <div style="font-size:12px;opacity:.85">${desc}</div>
          </button>`
            )
            .join("")}
        </div>
      </div>

      <div class="card-section">
        <div class="rule-row">
          <div>
            <strong>3. Protect Lock-Screen Settings</strong>
            <div class="card-sub">Prevents standard accounts from disabling screen locking in desktop settings</div>
          </div>
          <button type="button" class="m3-switch ${UI.state.wizardProtectLock ? "on" : ""}" id="wizToggleLock" aria-label="Protect lock screen"></button>
        </div>
      </div>

      <div class="modal-acts" style="margin-top:18px">
        <button type="button" class="btn btn-outlined" id="btnWizSkip">Skip Setup</button>
        <button type="button" class="btn btn-filled" id="btnWizFinish">Apply &amp; Finish Setup</button>
      </div>
    </div>`;
  }

  function renderNavUserButton(usr) {
    const active = UI.state.nav === `user:${usr.username}`;
    const left = UI.timeLeftSec(usr);
    const dot = !usr.loggedIn
      ? "dot-off"
      : usr.locked
        ? "dot-lock"
        : usr.enforcementState === "homework" || usr.enforcementState === "paused"
          ? "dot-info"
          : left <= 600
            ? "dot-warn"
            : "dot-ok";
    const rt =
      usr.role === "parent"
        ? "Parent"
        : usr.enforcementState === "paused"
          ? "Paused"
          : usr.enforcementState === "homework"
            ? "Study"
            : usr.locked
              ? "Locked"
              : UI.fmtDur(left);
    const reqCount = UI.pendingRequestsForUser(usr.username).length;
    const reqBadge =
      reqCount > 0
        ? `<span class="pill p-warn" style="padding:1px 6px;font-size:10.5px">${reqCount}</span>`
        : "";
    return `<button type="button" class="nav-item ${active ? "active" : ""}" data-nav="user:${UI.escHtml(usr.username)}">
      <div class="avatar">${UI.escHtml(usr.initials)}<span class="sdot ${dot}"></span></div>
      <div class="nav-meta">
        <div class="nav-uname"><span>${UI.escHtml(usr.fullName)}</span><span style="display:flex;align-items:center;gap:4px">${reqBadge}<span style="font-size:12px;font-family:var(--font-mono)" data-navleft="${UI.escHtml(usr.username)}">${rt}</span></span></div>
        <div class="nav-usub">@${UI.escHtml(usr.username)} · ${usr.loggedIn ? "Signed in" : "Offline"}</div>
      </div>
    </button>`;
  }

  function renderAdminShell() {
    if (UI.state.showSetupWizard) {
      return `<div class="main-pane" style="max-width:860px">${renderSetupWizard()}</div>`;
    }

    const u = UI.currentUser();
    const allUsers = Object.values(UI.state.users);
    const managedUsers = allUsers.filter((usr) => usr.role !== "parent");
    const otherUsers = allUsers.filter((usr) => usr.role === "parent");
    const issues = UI.doctorIssueCount();
    const needsLockFix = UI.doctorNeedsLockFix();
    const totalPending = UI.pendingRequestsForUser("").length;

    const currentRecord = UI.state.users[UI.state.currentUser];
    const inChildSession =
      currentRecord && currentRecord.role !== "parent" && !UI.state.childSessionBannerDismissed;

    let content = "";
    if (UI.state.nav === "doctor") {
      content = UI.renderDoctorView();
    } else if (UI.state.nav === "settings") {
      content = UI.renderSettingsView();
    } else if (!u) {
      content = `<div class="card"><h3 class="card-title">No Users Found</h3><div class="card-sub">No desktop user accounts were detected on this computer.</div></div>`;
    } else {
      content = `
        ${
          inChildSession
            ? `<div class="doctor-banner" style="background:var(--info-c);border-color:rgba(138,180,248,.35);color:var(--on-info-c)">
          <div class="doctor-left">${UI.ICONS.key}<div><div style="font-weight:700;font-size:13px">Running inside a managed child's session</div><div style="font-size:12px;opacity:.9">Close this window when finished — or set up an offline <strong>Parent Access Code</strong> in Settings for zero-password walk-up approvals.</div></div></div>
          <button type="button" class="btn btn-outlined btn-sm" id="btnDismissChildWarn">Got it</button>
        </div>`
            : ""
        }
        ${
          needsLockFix && !UI.state.bannerDismissed
            ? `<div class="doctor-banner">
          <div class="doctor-left">${UI.ICONS.warn}<div><div style="font-weight:700;font-size:13.5px">Lock-screen protection needed</div><div style="font-size:12.5px;opacity:.9">Standard users can currently turn off screen locking in system settings. Protect this setting with one click.</div></div></div>
          <div style="display:flex;gap:8px;flex-shrink:0"><button type="button" class="btn btn-filled btn-sm" id="btnBanFix" ${UI.state.busyFix ? "disabled" : ""}>${UI.state.busyFix ? "Protecting..." : "Protect Now"}</button><button type="button" class="btn btn-outlined btn-sm" id="btnBanDismiss">Dismiss</button></div>
        </div>`
            : ""
        }
        <div class="page-hdr"><div>
          <h1 class="page-title">${UI.escHtml(u.fullName)} <span style="font-weight:500;font-size:16px;color:var(--fg-muted)">@${UI.escHtml(u.username)}</span>${u.role === "parent" ? ` <span class="pill p-muted" style="vertical-align:middle">Parent / Unmanaged</span>` : ""}</h1>
          <div class="page-sub">Daily allowance <span id="hdrLimit">${UI.fmtDur(u.todayLimitSec)}</span> · Curfew <span id="hdrCurfew">${UI.escHtml(UI.todayCurfew(u))}</span></div>
        </div></div>
        <div class="m3-tabs">
          <button type="button" class="m3-tab ${UI.state.userTab === "overview" ? "active" : ""}" data-utab="overview">${UI.ICONS.overview} Overview</button>
          <button type="button" class="m3-tab ${UI.state.userTab === "schedule" ? "active" : ""}" data-utab="schedule">${UI.ICONS.schedule} Schedule</button>
          <button type="button" class="m3-tab ${UI.state.userTab === "apps" ? "active" : ""}" data-utab="apps">${UI.ICONS.apps} App Limits</button>
        </div>
        ${UI.state.userTab === "overview" ? renderOverviewTab(u) : UI.state.userTab === "schedule" ? UI.renderScheduleTab(u) : UI.renderAppLimitsTab(u)}`;
    }

    return `<div class="app-shell">
      <aside class="nav-drawer">
        <div class="brand-row">${UI.ICONS.brand}<div><div class="brand-title">curfewd</div><div class="brand-sub">Screen Time Control${totalPending ? ` · ${totalPending} req` : ""}</div></div></div>
        <div>
          <div class="nav-sec">Kids (${managedUsers.length})</div>
          <div class="nav-list">${managedUsers.length ? managedUsers.map(renderNavUserButton).join("") : `<div class="card-sub" style="padding:6px 12px">No managed kids yet</div>`}</div>
        </div>
        ${
          otherUsers.length
            ? `<div>
          <button type="button" class="nav-sec-toggle" id="btnToggleOtherUsers">
            <span>Other Accounts (${otherUsers.length})</span>
            <span>${UI.state.showOtherAccounts ? "▾" : "▸"}</span>
          </button>
          ${UI.state.showOtherAccounts ? `<div class="nav-list">${otherUsers.map(renderNavUserButton).join("")}</div>` : ""}
        </div>`
            : ""
        }
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
      u.appLimits.enabled && (u.appLimits.apps.length || u.appLimits.categories.length)
        ? `<div>
      <div class="card-sub" style="font-weight:600;margin-bottom:6px">My App &amp; Category Limits</div>
      <div class="mini-app-list">
      ${u.appLimits.categories
        .map((c) => {
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
          return `<div class="mini-app-row">
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px"><strong>All ${UI.escHtml(c.category)}</strong><span style="font-family:var(--font-mono);font-size:12px"><strong>${UI.fmtDur(rem)}</strong> left of ${UI.fmtAppLimit(allotted)}</span></div>
            <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
          </div>`;
        })
        .join("")}
      ${u.appLimits.apps
        .map((a) => {
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
          return `<div class="mini-app-row">
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px"><strong>${UI.escHtml(a.label)}</strong><span style="font-family:var(--font-mono);font-size:12px"><strong>${UI.fmtDur(rem)}</strong> left of ${UI.fmtAppLimit(allotted)}</span></div>
          <div class="app-prog"><div class="app-prog-fill" style="width:${pct}%;background:${col}"></div></div>
        </div>`;
        })
        .join("")}</div></div>`
        : "";

    const curfewTxt = UI.todayCurfew(u);
    let subInfo = "";
    if (u.enforcementState === "homework") {
      subInfo =
        "Homework Mode is active — your screen-time countdown is paused while games & social apps stay closed.";
    } else if (u.enforcementState === "paused") {
      subInfo = "Your screen-time limits are temporarily paused.";
    } else if (curfewTxt === "None" && u.todayLimitSec >= 86400) {
      subInfo = "No screen-time limits are currently active for your account.";
    } else if (u.enforcementState === "locked_out" && u.nextAllowedAt) {
      subInfo = `Screen time is up for now — next available at <strong>${UI.escHtml(UI.fmtIsoClock(u.nextAllowedAt))}</strong>.`;
    } else {
      subInfo = `Your screen locks automatically at <strong>${UI.escHtml(curfewTxt)}</strong> or when your daily allowance reaches 0m. Open apps stay saved!`;
    }

    const graceAvail =
      u.saveGrace &&
      u.saveGrace.available &&
      ["warning", "countdown", "locked_out"].includes(u.enforcementState);
    const myReqs = (UI.state.myRequests || []).slice(0, 4);

    return `<div class="centered-view"><div class="my-card">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:10px">${UI.ICONS.brand}<div><div style="font-size:17px;font-weight:700">My Screen Time</div><div class="card-sub">Signed in as @${UI.escHtml(u.username)}</div></div></div>
        ${u.lastBonusNote && u.bonusToday > 0 ? `<span class="pill p-ok">+${UI.fmtDur(u.bonusToday)} · ${UI.escHtml(u.lastBonusNote)}</span>` : ""}
      </div>
      <div class="hero-wrap" style="justify-content:center;padding:4px 0">
        ${renderRing(u, 152)}
        <div class="hero-stats">
          <div style="font-size:15px;font-weight:600">${UI.greetingPhrase()}, ${UI.escHtml(firstName)}</div>
          <div class="card-sub">${subInfo}</div>
          <div class="metric-grid">
            <div class="metric-box"><div class="metric-lbl">Used Today</div><div class="metric-val" id="myUsedToday">${UI.fmtDur(u.spentToday)}</div></div>
            <div class="metric-box"><div class="metric-lbl">Curfew</div><div class="metric-val">${UI.escHtml(curfewTxt)}</div></div>
          </div>
        </div>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="btn btn-tonal btn-sm" id="btnMyAskTime">${UI.ICONS.bell} Ask for More Time</button>
        ${u.parentCodeEnabled ? `<button type="button" class="btn btn-outlined btn-sm" id="btnMyParentCode">${UI.ICONS.key} Parent Code</button>` : ""}
        ${graceAvail ? `<button type="button" class="btn btn-filled btn-sm" id="btnMySaveGrace">Use 2-Min Save Grace</button>` : ""}
      </div>

      ${
        myReqs.length
          ? `<div>
        <div class="card-sub" style="font-weight:600;margin-bottom:6px">My Recent Requests</div>
        <div class="activity-list">${myReqs
          .map((r) => {
            const pCls =
              r.status === "approved" ? "p-ok" : r.status === "denied" ? "p-err" : "p-warn";
            const approveBtn =
              r.status === "pending"
                ? u.parentCodeEnabled
                  ? `<button type="button" class="btn btn-outlined btn-sm" data-quickcode="${r.id}" style="padding:2px 8px;font-size:11px">Approve w/ Code</button>`
                  : `<button type="button" class="btn btn-outlined btn-sm" data-quickpolkit="${r.id}" style="padding:2px 8px;font-size:11px">Parent Approve</button>`
                : "";
            return `<div class="activity-row">
            <span class="pill ${pCls}" style="text-transform:capitalize">${UI.escHtml(r.status)}</span>
            <span style="flex:1;font-size:12.5px">+${UI.fmtDur(r.requested_sec)} · ${UI.escHtml(r.target_label)}${r.reason ? ` ("${UI.escHtml(r.reason)}")` : ""}</span>
            ${approveBtn}
          </div>`;
          })
          .join("")}</div>
      </div>`
          : ""
      }

      <div><div class="card-sub" style="font-weight:600;margin-bottom:6px">Today's Allowed Hours</div>${renderWindowBar("my", u.todayWindows, { editable: false, showNow: true })}</div>
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
    renderWindowBar,
    renderAdminShell,
    renderMyScreenTime,
    renderAuthGate,
    renderUnavailable,
  });
})((window.CurfewUI = window.CurfewUI || {}));
