/* View-scoped DOM event binders, multi-window timeline dragging & modals for curfewd-ui (Spec 006). */
(function (UI) {
  "use strict";

  function applyUserResp(username, res) {
    if (res && res.status) {
      UI.mergeLiveUserStatus(username, res.status);
    }
    if (res && res.history) {
      UI.state.histories[username] = res.history;
    }
    if (!UI.isDirty()) UI.snapshotState();
    UI.render();
  }

  function localTodayIso() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  /* ----------------- Modals ----------------- */

  async function openAddAppModal(uname) {
    const u0 = UI.state.users[uname];
    if (!u0) return;
    let allApps = UI.state.userAppsCache[uname];
    if (!allApps) {
      try {
        const resp = await UI.rpc("list_user_apps", { username: uname });
        allApps = (resp && resp.apps) || UI.state.installedApps || [];
        UI.state.userAppsCache[uname] = allApps;
      } catch {
        allApps = UI.state.installedApps || [];
      }
    }

    UI.openModal(`<h3 style="margin:0">Add App / Game Limit</h3>
      <div class="card-sub">Choose a daily limit (0m = Always Blocked) and pick an app installed for @${UI.escHtml(uname)}.</div>
      <div class="rule-row">
        <label class="card-sub" for="newAppLim">Weekday limit</label>
        <select class="text-in" id="newAppLim" style="width:auto">
          <option value="0">Blocked (0m)</option>
          <option value="900">15m / day</option>
          <option value="1800">30m / day</option>
          <option value="2700" selected>45m / day</option>
          <option value="3600">1h / day</option>
          <option value="5400">1h 30m / day</option>
          <option value="7200">2h / day</option>
        </select>
      </div>
      <div class="rule-row">
        <label class="card-sub" for="newAppWknd">Weekend limit</label>
        <select class="text-in" id="newAppWknd" style="width:auto">
          <option value="" selected>Same as weekday</option>
          <option value="0">Blocked (0m)</option>
          <option value="1800">30m / day</option>
          <option value="3600">1h / day</option>
          <option value="5400">1h 30m / day</option>
          <option value="7200">2h / day</option>
          <option value="10800">3h / day</option>
        </select>
      </div>
      <input class="text-in" id="appSearch" type="search" placeholder="Search installed apps..."/>
      <div id="appPickList" style="display:flex;flex-direction:column;gap:6px;max-height:210px;overflow-y:auto"></div>
      <div style="border-top:1px solid var(--out-var);padding-top:10px;display:flex;gap:8px;align-items:center">
        <input class="text-in" id="customAppName" type="text" placeholder="Or enter an app name (e.g. minecraft)"/>
        <button type="button" class="btn btn-tonal btn-sm" id="btnAddCustomApp">Add</button>
      </div>
      <div class="modal-acts"><button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Close</button></div>`);

    const addRule = (pattern, label, category, flatpakId = "", steamAppId = "") => {
      const u = UI.state.users[uname];
      if (!u) return;
      const lim = Number((document.getElementById("newAppLim") || {}).value ?? 2700);
      const rawWk = (document.getElementById("newAppWknd") || {}).value;
      const wkLim = rawWk === "" || rawWk === undefined ? null : Number(rawWk);
      const cleanPat = String(pattern || "")
        .trim()
        .toLowerCase();
      if (!cleanPat) return;
      if (!u.appLimits.apps.some((x) => x.pattern.toLowerCase() === cleanPat)) {
        u.appLimits.apps.push({
          pattern: cleanPat,
          label: label || pattern,
          category: category || "App",
          dailyLimit: lim,
          weekendLimit: wkLim,
          effectiveLimit: lim,
          flatpakId: flatpakId || "",
          steamAppId: steamAppId || "",
          alwaysAllowed: false,
          foregroundOnly: false,
          spentToday: 0,
          bonusToday: 0,
          running: false,
        });
        u.appLimits.enabled = true;
        UI.state.dirtyPolicies.add(uname);
        UI.updateUnsavedBar();
      }
      UI.closeModal();
      UI.render();
      UI.showToast(`Added ${label || pattern} (${UI.fmtAppLimit(lim)}/day)`);
    };

    // Surgical filter of #appPickList without resetting #newAppLim or losing search focus!
    const renderPickList = (query) => {
      const listEl = document.getElementById("appPickList");
      if (!listEl) return;
      const q = String(query || "")
        .trim()
        .toLowerCase();
      const filtered = allApps.filter(
        (a) =>
          !q || a.label.toLowerCase().includes(q) || (a.category || "").toLowerCase().includes(q)
      );
      listEl.innerHTML = filtered.length
        ? filtered
            .map(
              (
                a
              ) => `<button type="button" class="pick-row" data-pickpat="${UI.escHtml(a.pattern)}">
          <span><strong>${UI.escHtml(a.label)}</strong></span>
          <span class="pick-cat">${UI.escHtml(a.category || "App")}</span>
        </button>`
            )
            .join("")
        : `<div class="card-sub" style="padding:8px 2px">No matching installed apps found.</div>`;

      listEl.querySelectorAll("[data-pickpat]").forEach((btn) => {
        btn.onclick = () => {
          const found = allApps.find((x) => x.pattern === btn.dataset.pickpat);
          if (found) {
            addRule(
              found.pattern,
              found.label,
              found.category,
              found.flatpak_id || "",
              found.steam_app_id || ""
            );
          }
        };
      });
    };

    renderPickList("");
    const searchIn = document.getElementById("appSearch");
    if (searchIn) searchIn.oninput = () => renderPickList(searchIn.value);

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
    if (btnClose) btnClose.onclick = UI.closeModal;
  }

  function openAddCategoryModal(uname) {
    if (!UI.state.users[uname]) return;
    UI.openModal(`<h3 style="margin:0">Add Shared Category Budget</h3>
      <div class="card-sub">Pool all apps in a category under one daily timer (e.g., 1h total across all games).</div>
      <div class="rule-row">
        <label class="card-sub" for="catName">Category</label>
        <select class="text-in" id="catName" style="width:auto">
          ${UI.CATEGORIES.filter((c) => c !== "App")
            .map((c) => `<option value="${c}">${c}</option>`)
            .join("")}
        </select>
      </div>
      <div class="rule-row">
        <label class="card-sub" for="catLim">Weekday cap</label>
        <select class="text-in" id="catLim" style="width:auto">
          <option value="0">Blocked (0m)</option>
          <option value="1800">30m / day</option>
          <option value="3600" selected>1h / day</option>
          <option value="5400">1h 30m / day</option>
          <option value="7200">2h / day</option>
        </select>
      </div>
      <div class="rule-row">
        <label class="card-sub" for="catWknd">Weekend cap</label>
        <select class="text-in" id="catWknd" style="width:auto">
          <option value="">Same as weekday</option>
          <option value="3600">1h / day</option>
          <option value="5400">1h 30m / day</option>
          <option value="7200" selected>2h / day</option>
          <option value="10800">3h / day</option>
        </select>
      </div>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnSaveCat">Add Budget</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    document.getElementById("btnSaveCat").onclick = () => {
      const u = UI.state.users[uname];
      if (!u) return;
      const cat = document.getElementById("catName").value;
      const lim = Number(document.getElementById("catLim").value);
      const rawWk = document.getElementById("catWknd").value;
      const wkLim = rawWk === "" ? null : Number(rawWk);
      u.appLimits.categories = (u.appLimits.categories || []).filter(
        (c) => c.category.toLowerCase() !== cat.toLowerCase()
      );
      u.appLimits.categories.push({
        category: cat,
        dailyLimit: lim,
        weekendLimit: wkLim,
        effectiveLimit: lim,
        spentToday: 0,
        bonusToday: 0,
        running: false,
      });
      u.appLimits.enabled = true;
      UI.state.dirtyPolicies.add(uname);
      UI.closeModal();
      UI.render();
      UI.showToast(`Added ${cat} category budget (click Save Changes)`);
    };
  }

  function openBonusNoteModal(uname) {
    const u0 = UI.state.users[uname];
    if (!u0) return;
    UI.openModal(`<h3 style="margin:0">Reward Bonus Time for ${UI.escHtml(u0.fullName)}</h3>
      <div class="card-sub">Optional note is shown positively in ${UI.escHtml(u0.fullName)}'s My Screen Time view.</div>
      <div class="rule-row">
        <label class="card-sub" for="bonusDur">Amount</label>
        <select class="text-in" id="bonusDur" style="width:auto">
          <option value="900">+15 minutes</option>
          <option value="1800" selected>+30 minutes</option>
          <option value="3600">+1 hour</option>
          <option value="-900">-15 minutes</option>
        </select>
      </div>
      <input class="text-in" id="bonusNote" type="text" maxlength="140" placeholder="Note (e.g. Cleaned room, Finished reading)"/>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnSendBonus">Apply Bonus</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    const sendBtn = document.getElementById("btnSendBonus");
    sendBtn.onclick = async () => {
      const u = UI.state.users[uname];
      const sec = Number(document.getElementById("bonusDur").value);
      const note = (document.getElementById("bonusNote").value || "").trim();
      sendBtn.disabled = true;
      try {
        const res = await UI.rpc("grant_time", {
          username: uname,
          delta_seconds: sec,
          mode: "add",
          note,
        });
        UI.closeModal();
        applyUserResp(uname, res);
        UI.showToast(
          `${sec >= 0 ? "+" : ""}${UI.fmtDur(sec)} applied for ${(u && u.fullName) || uname}`
        );
      } catch (err) {
        sendBtn.disabled = false;
        UI.showToast(err.message || "Could not apply bonus");
      }
    };
  }

  function openPauseModal(uname) {
    const u0 = UI.state.users[uname];
    if (!u0) return;
    UI.openModal(`<h3 style="margin:0">Pause Limits for ${UI.escHtml(u0.fullName)}</h3>
      <div class="card-sub">Temporarily pauses limits without changing the saved weekly schedule.</div>
      <div class="rule-row">
        <label class="card-sub" for="pauseDur">Duration</label>
        <select class="text-in" id="pauseDur" style="width:auto">
          <option value="3600">1 hour</option>
          <option value="7200" selected>2 hours</option>
          <option value="today">Rest of today</option>
          <option value="indefinite">Until I resume manually</option>
        </select>
      </div>
      <input class="text-in" id="pauseReason" type="text" maxlength="100" placeholder="Optional reason (e.g. Family movie night)"/>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnOkPause">Pause Limits</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    const okBtn = document.getElementById("btnOkPause");
    okBtn.onclick = async () => {
      const u = UI.state.users[uname];
      const choice = document.getElementById("pauseDur").value;
      const reason = (document.getElementById("pauseReason").value || "").trim();
      let until_utc = "";
      if (choice === "today") {
        const d = new Date();
        const nextMid = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 0, 0, 0);
        until_utc = nextMid.toISOString();
      } else if (choice !== "indefinite") {
        until_utc = new Date(Date.now() + Number(choice) * 1000).toISOString();
      }
      okBtn.disabled = true;
      try {
        const res = await UI.rpc("pause_user", {
          username: uname,
          until_utc,
          reason,
          hide_tray: false,
        });
        UI.closeModal();
        applyUserResp(uname, res);
        UI.showToast(`Paused limits for ${(u && u.fullName) || uname}`);
      } catch (err) {
        okBtn.disabled = false;
        UI.showToast(err.message || "Could not pause");
      }
    };
  }

  function openHomeworkModal(uname) {
    const u0 = UI.state.users[uname];
    if (!u0) return;
    UI.openModal(`<h3 style="margin:0">Start Homework Mode for ${UI.escHtml(u0.fullName)}</h3>
      <div class="card-sub">Pauses the daily screen-time countdown while closing &amp; blocking Gaming and Social apps.</div>
      <div class="rule-row">
        <label class="card-sub" for="hwDur">Duration</label>
        <select class="text-in" id="hwDur" style="width:auto">
          <option value="1800">30 minutes</option>
          <option value="3600" selected>1 hour</option>
          <option value="7200">2 hours</option>
          <option value="indefinite">Until turned off</option>
        </select>
      </div>
      <input class="text-in" id="hwReason" type="text" maxlength="100" placeholder="Note (e.g. Science project)"/>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnOkHw">Start Homework Mode</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    const okBtn = document.getElementById("btnOkHw");
    okBtn.onclick = async () => {
      const u = UI.state.users[uname];
      const choice = document.getElementById("hwDur").value;
      const reason = (document.getElementById("hwReason").value || "").trim();
      const until_utc =
        choice === "indefinite" ? null : new Date(Date.now() + Number(choice) * 1000).toISOString();
      okBtn.disabled = true;
      try {
        const res = await UI.rpc("set_homework_mode", {
          username: uname,
          payload: {
            active: true,
            mode: until_utc ? "until" : "indefinite",
            until_utc,
            reason,
          },
        });
        UI.closeModal();
        applyUserResp(uname, res);
        UI.showToast(`Homework Mode active for ${(u && u.fullName) || uname}`);
      } catch (err) {
        okBtn.disabled = false;
        UI.showToast(err.message || "Could not start Homework Mode");
      }
    };
  }

  function openAddHolidayModal(uname) {
    if (!UI.state.users[uname]) return;
    const todayIso = localTodayIso();
    UI.openModal(`<h3 style="margin:0">Schedule Holiday / Vacation Override</h3>
      <div class="card-sub">Override rules automatically during school breaks without modifying weekly templates.</div>
      <input class="text-in" id="holLabel" type="text" maxlength="60" placeholder="Label (e.g. Winter Break, Snow Day)"/>
      <div class="grid-eq" style="gap:10px">
        <div><label class="card-sub" for="holStart">Start date</label><input class="text-in" id="holStart" type="date" value="${todayIso}"/></div>
        <div><label class="card-sub" for="holEnd">End date</label><input class="text-in" id="holEnd" type="date" value="${todayIso}"/></div>
      </div>
      <div class="rule-row">
        <label class="card-sub" for="holMode">Schedule behavior</label>
        <select class="text-in" id="holMode" style="width:auto">
          <option value="weekend" selected>Use Weekend Schedule</option>
          <option value="pause">Pause All Limits</option>
        </select>
      </div>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnSaveHol">Add Holiday</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    document.getElementById("btnSaveHol").onclick = () => {
      const u = UI.state.users[uname];
      if (!u) return;
      const start_date = document.getElementById("holStart").value;
      const end_date = document.getElementById("holEnd").value;
      const mode = document.getElementById("holMode").value;
      const label = (document.getElementById("holLabel").value || "Holiday").trim();
      if (!start_date || !end_date || end_date < start_date) {
        UI.showToast("End date must be on or after start date");
        return;
      }
      u.holidays.push({
        id: `${start_date}_${end_date}_${mode}`,
        label,
        start_date,
        end_date,
        mode,
      });
      UI.state.dirtyPolicies.add(uname);
      UI.closeModal();
      UI.render();
      UI.showToast(`Added "${label}" (click Save Changes)`);
    };
  }

  function openAskTimeModal() {
    const u = UI.state.me;
    if (!u) return;
    const apps = (u.appLimits && u.appLimits.apps) || [];
    const cats = (u.appLimits && u.appLimits.categories) || [];
    UI.openModal(`<h3 style="margin:0">Ask for More Time</h3>
      <div class="card-sub">Send a quick request to a parent — or have a parent approve right here.</div>
      <div class="rule-row">
        <label class="card-sub" for="askTarget">What needs more time?</label>
        <select class="text-in" id="askTarget" style="width:auto">
          <option value="daily::Daily Screen Time">Daily Screen Time</option>
          ${cats
            .map(
              (c) =>
                `<option value="category::${UI.escHtml(c.category)}::All ${UI.escHtml(c.category)}">All ${UI.escHtml(c.category)}</option>`
            )
            .join("")}
          ${apps
            .map(
              (a) =>
                `<option value="app::${UI.escHtml(a.pattern)}::${UI.escHtml(a.label)}">${UI.escHtml(a.label)}</option>`
            )
            .join("")}
        </select>
      </div>
      <div class="rule-row">
        <label class="card-sub" for="askDur">How much time?</label>
        <select class="text-in" id="askDur" style="width:auto">
          <option value="900" selected>+15 minutes</option>
          <option value="1800">+30 minutes</option>
          <option value="3600">+1 hour</option>
        </select>
      </div>
      <input class="text-in" id="askReason" type="text" maxlength="120" placeholder="Quick reason (e.g. Finishing a match / homework)"/>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnSendAsk">Send Request</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    const sendBtn = document.getElementById("btnSendAsk");
    sendBtn.onclick = async () => {
      const parts = (
        document.getElementById("askTarget").value || "daily::Daily Screen Time"
      ).split("::");
      const kind = parts[0];
      const target = kind === "app" || kind === "category" ? parts[1] : "";
      const target_label =
        kind === "app" || kind === "category" ? parts[2] || parts[1] : "Daily Screen Time";
      const requested_sec = Number(document.getElementById("askDur").value || 900);
      const reason = (document.getElementById("askReason").value || "").trim();
      sendBtn.disabled = true;
      try {
        const res = await UI.rpc("request_more_time", {
          kind,
          target,
          target_label,
          requested_sec,
          reason,
        });
        if (res && res.my_requests) UI.state.myRequests = res.my_requests;
        UI.closeModal();
        UI.render();
        UI.showToast("Request sent!");
      } catch (err) {
        sendBtn.disabled = false;
        UI.showToast(err.message || "Could not send request");
      }
    };
  }

  function openParentCodeModal(pendingReq = null) {
    const u = UI.state.me;
    if (!u) return;
    const apps = (u.appLimits && u.appLimits.apps) || [];
    const cats = (u.appLimits && u.appLimits.categories) || [];
    const reqSummary = pendingReq
      ? `<div class="rule-row">
          <span class="card-sub">Approving request</span>
          <strong>+${UI.fmtDur(pendingReq.requested_sec)} · ${UI.escHtml(pendingReq.target_label || "Daily Screen Time")}</strong>
        </div>`
      : `<div class="rule-row">
          <label class="card-sub" for="pcAction">Grant</label>
          <select class="text-in" id="pcAction" style="width:auto">
            <option value="grant_daily::::900">+15m Daily Time</option>
            <option value="grant_daily::::1800">+30m Daily Time</option>
            <option value="grant_daily::::3600">+1h Daily Time</option>
            <option value="homework::::3600">Homework Mode (1h)</option>
            <option value="pause::::3600">Pause Limits (1h)</option>
            ${cats
              .map(
                (c) =>
                  `<option value="grant_category::${UI.escHtml(c.category)}::900">+15m All ${UI.escHtml(c.category)}</option>`
              )
              .join("")}
            ${apps
              .map(
                (a) =>
                  `<option value="grant_app::${UI.escHtml(a.pattern)}::900">+15m ${UI.escHtml(a.label)}</option>`
              )
              .join("")}
          </select>
        </div>`;

    UI.openModal(`<h3 style="margin:0">Walk-Up Parent Approval</h3>
      <div class="card-sub">Enter the 6-digit Parent Access Code from your authenticator app — grants time cleanly without typing your Linux password.</div>
      ${reqSummary}
      <input class="text-in" id="pcCodeIn" type="text" inputmode="numeric" maxlength="6" placeholder="6-digit code (e.g. 123456)" style="font-family:var(--font-mono);font-size:18px;letter-spacing:0.2em;text-align:center"/>
      <div class="modal-acts">
        <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
        <button type="button" class="btn btn-filled btn-sm" id="btnPcVerify">Approve</button>
      </div>`);
    document.getElementById("btnCloseModal").onclick = UI.closeModal;
    const verifyBtn = document.getElementById("btnPcVerify");
    verifyBtn.onclick = async () => {
      const code = (document.getElementById("pcCodeIn").value || "").trim();
      let payload;
      if (pendingReq) {
        const reqAction =
          pendingReq.kind === "app"
            ? "grant_app"
            : pendingReq.kind === "category"
              ? "grant_category"
              : "grant_daily";
        payload = {
          code,
          action: reqAction,
          target: pendingReq.target || "",
          seconds: Number(pendingReq.requested_sec || 900),
          request_id: pendingReq.id,
        };
      } else {
        const [action, target, secStr] = (
          document.getElementById("pcAction").value || "grant_daily::::900"
        ).split("::");
        payload = {
          code,
          action,
          target: target || "",
          seconds: Number(secStr || 900),
        };
      }
      verifyBtn.disabled = true;
      try {
        const res = await UI.rpc("approve_with_parent_code", payload);
        if (res && res.status) UI.state.me = UI.normalizeUserStatus(res.status, UI.state.me);
        if (res && res.my_requests) UI.state.myRequests = res.my_requests;
        UI.closeModal();
        UI.render();
        UI.showToast("Parent approval applied!");
      } catch (err) {
        verifyBtn.disabled = false;
        UI.showToast(err.message || "Invalid Parent Access Code");
      }
    };
  }

  /* ----------------- Multi-Window Timeline Helpers ----------------- */

  function getWindowsForKey(u, key) {
    if (key === "today") return u.todayWindows;
    if (key === "all" || key === "weekdays") return u.days.mon.windows;
    if (key === "weekends") return u.days.sat.windows;
    return u.days[key].windows;
  }

  function setWindowsForKey(u, key, wins) {
    const todayKey = UI.getTodayKey();
    const copy = UI.cloneWindows(wins);
    if (key === "today") {
      u.todayWindows = copy;
      UI.state.dirtyToday.add(u.username);
      return;
    }
    const targetDays =
      key === "all"
        ? UI.DAYS
        : key === "weekdays"
          ? UI.WEEKDAY_KEYS
          : key === "weekends"
            ? UI.WEEKEND_KEYS
            : [key];
    targetDays.forEach((d) => {
      u.days[d].windows = UI.cloneWindows(copy);
    });
    if (targetDays.includes(todayKey) && !UI.state.dirtyToday.has(u.username)) {
      u.todayWindows = UI.cloneWindows(copy);
    }
    UI.state.dirtyPolicies.add(u.username);
  }

  function wireTimelineBars(uname) {
    if (!uname) return;
    const getU = () => UI.state.users[uname];

    document.querySelectorAll("[data-addbreak]").forEach((btn) => {
      btn.onclick = () => {
        const u = getU();
        if (!u) return;
        const key = btn.dataset.addbreak;
        const wins = UI.cloneWindows(getWindowsForKey(u, key));
        if (wins.length === 0) {
          setWindowsForKey(u, key, [{ startMin: 480, endMin: 1260 }]);
        } else {
          // Split the largest segment into two segments with a 60m gap in the middle
          let bestIdx = 0;
          wins.forEach((w, i) => {
            if (w.endMin - w.startMin > wins[bestIdx].endMin - wins[bestIdx].startMin) bestIdx = i;
          });
          const seg = wins[bestIdx];
          if (seg.endMin - seg.startMin < 180) {
            UI.showToast("Window is too narrow to split further");
            return;
          }
          const mid = Math.round((seg.startMin + seg.endMin) / 2 / 15) * 15;
          wins.splice(
            bestIdx,
            1,
            { startMin: seg.startMin, endMin: mid - 30 },
            { startMin: mid + 30, endMin: seg.endMin }
          );
          setWindowsForKey(u, key, wins);
        }
        UI.render();
      };
    });

    document.querySelectorAll("[data-toggleblock]").forEach((btn) => {
      btn.onclick = () => {
        const u = getU();
        if (!u) return;
        const key = btn.dataset.toggleblock;
        const wins = getWindowsForKey(u, key);
        if (wins.length === 0) {
          setWindowsForKey(u, key, [{ startMin: 480, endMin: 1260 }]);
        } else {
          setWindowsForKey(u, key, []);
        }
        UI.render();
      };
    });

    document.querySelectorAll("[data-winbar]").forEach((bar) => {
      const key = bar.dataset.winbar;

      const syncSegUI = (segIdx) => {
        const u = getU();
        if (!u) return;
        const wins = getWindowsForKey(u, key);
        const w = wins[segIdx];
        if (!w) return;
        const sp = (w.startMin / 1440) * 100;
        const ep = (w.endMin / 1440) * 100;
        const segEl = bar.querySelector(`.timeline-seg[data-seg="${segIdx}"]`);
        if (segEl) {
          segEl.style.left = `${sp}%`;
          segEl.style.width = `${Math.max(0, ep - sp)}%`;
        }
        const lh = bar.querySelector(`.timeline-handle[data-seg="${segIdx}"][data-edge="start"]`);
        const rh = bar.querySelector(`.timeline-handle[data-seg="${segIdx}"][data-edge="end"]`);
        if (lh) {
          lh.style.left = `${sp}%`;
          lh.querySelector(".handle-tip").textContent = UI.fmtTimeMin(w.startMin);
        }
        if (rh) {
          rh.style.left = `${ep}%`;
          rh.querySelector(".handle-tip").textContent = UI.fmtTimeMin(w.endMin);
        }
        const hdrCurfew = document.getElementById("hdrCurfew");
        if (hdrCurfew) hdrCurfew.textContent = UI.todayCurfew(u);
      };

      const setEdge = (segIdx, edge, raw) => {
        const u = getU();
        if (!u) return;
        const sn = Math.round(raw / 15) * 15;
        const wins = UI.cloneWindows(getWindowsForKey(u, key));
        const cur = wins[segIdx];
        if (!cur) return;
        const minBound = segIdx > 0 ? wins[segIdx - 1].endMin + 15 : 0;
        const maxBound = segIdx + 1 < wins.length ? wins[segIdx + 1].startMin - 15 : 1440;
        if (edge === "start") {
          cur.startMin = Math.max(minBound, Math.min(cur.endMin - 30, sn));
        } else {
          cur.endMin = Math.max(cur.startMin + 30, Math.min(maxBound, sn));
        }
        setWindowsForKey(u, key, wins);
        syncSegUI(segIdx);
        UI.updateUnsavedBar();
      };

      bar.querySelectorAll(".timeline-handle").forEach((h) => {
        const edge = h.dataset.edge;
        const segIdx = Number(h.dataset.seg || 0);
        h.onpointerdown = (e) => {
          e.preventDefault();
          h.setPointerCapture(e.pointerId);
          h.classList.add("dragging");
          const mv = (ev) => {
            const r = bar.getBoundingClientRect();
            setEdge(segIdx, edge, Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * 1440);
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
            const u = getU();
            if (!u) return;
            const cur = getWindowsForKey(u, key)[segIdx];
            if (!cur) return;
            setEdge(
              segIdx,
              edge,
              (edge === "start" ? cur.startMin : cur.endMin) + (e.key === "ArrowRight" ? 15 : -15)
            );
          }
        };
      });
    });
  }

  /* ----------------- View Event Binders ----------------- */

  function wireWizardEvents() {
    if (!UI.state.showSetupWizard) return;
    document.querySelectorAll("[data-wizkid]").forEach((cb) => {
      cb.onchange = () => {
        const uname = cb.dataset.wizkid;
        if (cb.checked) UI.state.wizardKids.add(uname);
        else UI.state.wizardKids.delete(uname);
        UI.render();
      };
    });
    document.querySelectorAll("[data-wizpreset]").forEach((b) => {
      b.onclick = () => {
        UI.state.wizardPreset = b.dataset.wizpreset;
        UI.render();
      };
    });
    const tog = document.getElementById("wizToggleLock");
    if (tog) {
      tog.onclick = () => {
        UI.state.wizardProtectLock = !UI.state.wizardProtectLock;
        UI.render();
      };
    }
    const skip = document.getElementById("btnWizSkip");
    if (skip) {
      skip.onclick = async () => {
        skip.disabled = true;
        try {
          const res = await UI.rpc("complete_setup", { policies: {}, apply_fix: false });
          if (res && res.config) UI.state.config = res.config;
          UI.state.showSetupWizard = false;
          UI.snapshotState();
          UI.clearDirty();
          UI.render();
        } catch (err) {
          skip.disabled = false;
          UI.showToast(err.message || "Could not skip setup");
        }
      };
    }
    const finish = document.getElementById("btnWizFinish");
    if (finish) {
      finish.onclick = async () => {
        const presetDefs = {
          elementary: { wd: 3600, we: 7200, wdEnd: "19:30", weEnd: "20:30" },
          middle: { wd: 5400, we: 9000, wdEnd: "21:00", weEnd: "22:00" },
          teen: { wd: 9000, we: 14400, wdEnd: "22:30", weEnd: "23:30" },
        };
        const p = presetDefs[UI.state.wizardPreset] || presetDefs.middle;
        const policies = {};
        Object.values(UI.state.users).forEach((u) => {
          const isKid = UI.state.wizardKids.has(u.username);
          const pol = UI.serializeUserPolicy(u);
          pol.role = isKid ? "child" : "parent";
          if (isKid) {
            UI.WEEKDAY_KEYS.forEach((d) => {
              pol.daily_limits[d] = p.wd;
              pol.curfew_windows[d] = [{ start: "07:00", end: p.wdEnd }];
            });
            UI.WEEKEND_KEYS.forEach((d) => {
              pol.daily_limits[d] = p.we;
              pol.curfew_windows[d] = [{ start: "07:30", end: p.weEnd }];
            });
          }
          policies[u.username] = pol;
        });
        finish.disabled = true;
        try {
          const res = await UI.rpc("complete_setup", {
            policies,
            apply_fix: Boolean(UI.state.wizardProtectLock),
          });
          if (res && res.users) {
            for (const [uname, st] of Object.entries(res.users)) {
              UI.state.users[uname] = UI.normalizeUserStatus(st);
            }
          }
          if (res && res.config) UI.state.config = res.config;
          if (res && res.doctor) UI.state.doctor = res.doctor;
          UI.state.showSetupWizard = false;
          UI.snapshotState();
          UI.clearDirty();
          UI.render();
          UI.showToast("Setup complete!");
        } catch (err) {
          finish.disabled = false;
          UI.showToast(err.message || "Could not complete setup");
        }
      };
    }
  }

  function wireNavigationEvents() {
    document.querySelectorAll("[data-nav]").forEach((b) => {
      b.onclick = () => {
        const nextNav = b.dataset.nav;
        if (nextNav !== "settings") {
          UI.state.parentCodeEnrollment = null;
        }
        UI.state.nav = nextNav;
        UI.render();
      };
    });
    const togOther = document.getElementById("btnToggleOtherUsers");
    if (togOther) {
      togOther.onclick = () => {
        UI.state.showOtherAccounts = !UI.state.showOtherAccounts;
        UI.render();
      };
    }
    const disChild = document.getElementById("btnDismissChildWarn");
    if (disChild) {
      disChild.onclick = () => {
        UI.state.childSessionBannerDismissed = true;
        UI.render();
      };
    }
    document.querySelectorAll("[data-utab]").forEach((b) => {
      b.onclick = () => {
        UI.state.userTab = b.dataset.utab;
        UI.render();
      };
    });
    document.querySelectorAll("[data-hspan]").forEach((b) => {
      b.onclick = async () => {
        const span = Number(b.dataset.hspan);
        UI.state.historySpan = span;
        UI.render();
        const active = UI.currentUser();
        if (!active) return;
        try {
          const res = await UI.rpc("get_user_bundle", { username: active.username, days: span });
          if (res && res.history && UI.state.historySpan === span) {
            UI.state.histories[active.username] = res.history;
            UI.render();
          }
        } catch {
          // Non-fatal: chart already rendered from cached history
        }
      };
    });

    const btnAuth = document.getElementById("btnAuth");
    if (btnAuth) btnAuth.onclick = () => UI.bootstrap("admin");
    const btnSelf = document.getElementById("btnSelf");
    if (btnSelf) btnSelf.onclick = () => UI.bootstrap("my_status");
    const btnRetry = document.getElementById("btnRetry");
    if (btnRetry) btnRetry.onclick = () => UI.bootstrap(UI.initialMode());
  }

  function wireOverviewEvents(uname) {
    if (!uname) return;
    const getU = () => UI.state.users[uname];

    const doQuickGrant = async (sec, btn) => {
      if (btn) btn.disabled = true;
      try {
        const res = await UI.rpc("grant_time", {
          username: uname,
          delta_seconds: sec,
          mode: "add",
          note: "",
        });
        applyUserResp(uname, res);
        const cur = getU();
        UI.showToast(`+${UI.fmtDur(sec)} added for ${(cur && cur.fullName) || uname}`);
      } catch (err) {
        if (btn) btn.disabled = false;
        UI.showToast(err.message || "Could not grant time");
      }
    };
    const q15 = document.getElementById("btnQuick15");
    if (q15) q15.onclick = () => doQuickGrant(900, q15);
    const q30 = document.getElementById("btnQuick30");
    if (q30) q30.onclick = () => doQuickGrant(1800, q30);
    const bn = document.getElementById("btnGrantNote");
    if (bn) bn.onclick = () => openBonusNoteModal(uname);
    const pm = document.getElementById("btnPauseModal");
    if (pm) pm.onclick = () => openPauseModal(uname);
    const ru = document.getElementById("btnResumeUser");
    if (ru) {
      ru.onclick = async () => {
        ru.disabled = true;
        try {
          const res = await UI.rpc("resume_user", { username: uname });
          applyUserResp(uname, res);
          const cur = getU();
          UI.showToast(`Resumed schedule for ${(cur && cur.fullName) || uname}`);
        } catch (err) {
          ru.disabled = false;
          UI.showToast(err.message || "Could not resume");
        }
      };
    }
    const hwm = document.getElementById("btnHomeworkModal");
    if (hwm) hwm.onclick = () => openHomeworkModal(uname);
    const hwo = document.getElementById("btnHomeworkOff");
    if (hwo) {
      hwo.onclick = async () => {
        hwo.disabled = true;
        try {
          const res = await UI.rpc("set_homework_mode", {
            username: uname,
            payload: { active: false },
          });
          applyUserResp(uname, res);
          const cur = getU();
          UI.showToast(`Ended Homework Mode for ${(cur && cur.fullName) || uname}`);
        } catch (err) {
          hwo.disabled = false;
          UI.showToast(err.message || "Could not end Homework Mode");
        }
      };
    }
    const rstToday = document.getElementById("btnResetToday");
    if (rstToday) {
      rstToday.onclick = async () => {
        rstToday.disabled = true;
        try {
          const res = await UI.rpc("reset_today", { username: uname });
          UI.state.dirtyToday.delete(uname);
          applyUserResp(uname, res);
          UI.showToast("Reverted today to weekly schedule");
        } catch (err) {
          rstToday.disabled = false;
          UI.showToast(err.message || "Could not reset today");
        }
      };
    }

    document.querySelectorAll("[data-reqapprove]").forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const res = await UI.rpc("resolve_time_request", {
            request_id: Number(btn.dataset.reqapprove),
            approve: true,
            grant_seconds: Number(btn.dataset.reqsec || 900),
            note: "Approved",
          });
          if (res && res.pending_requests) UI.state.timeRequests = res.pending_requests;
          applyUserResp(uname, res);
          UI.showToast("Request approved");
        } catch (err) {
          btn.disabled = false;
          UI.showToast(err.message || "Could not approve request");
        }
      };
    });
    document.querySelectorAll("[data-reqdeny]").forEach((btn) => {
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const res = await UI.rpc("resolve_time_request", {
            request_id: Number(btn.dataset.reqdeny),
            approve: false,
            grant_seconds: 0,
            note: "Declined",
          });
          if (res && res.pending_requests) UI.state.timeRequests = res.pending_requests;
          const cur = getU();
          if (cur) cur.pendingRequestCount = UI.pendingRequestsForUser(uname).length;
          UI.render();
          UI.showToast("Request declined");
        } catch (err) {
          btn.disabled = false;
          UI.showToast(err.message || "Could not decline request");
        }
      };
    });

    document.querySelectorAll("[data-quicklimit]").forEach((btn) => {
      btn.onclick = () => {
        const u = getU();
        if (!u) return;
        const rawKey = String(btn.dataset.quicklimit || "").trim();
        if (!rawKey) return;
        const isSteam = rawKey.toLowerCase().startsWith("steam:");
        const steamAppId = isSteam ? rawKey.slice(6).trim() : "";
        const pat = isSteam ? `steam_app_${steamAppId}` : rawKey;
        const lbl = btn.dataset.qllabel || rawKey;
        const cat = btn.dataset.qlcat || "App";
        const exists = u.appLimits.apps.some(
          (a) =>
            a.pattern.toLowerCase() === pat.toLowerCase() ||
            (steamAppId && String(a.steamAppId || "").toLowerCase() === steamAppId.toLowerCase())
        );
        if (!exists) {
          u.appLimits.apps.push({
            pattern: pat,
            label: lbl,
            category: cat,
            dailyLimit: 2700,
            weekendLimit: null,
            effectiveLimit: 2700,
            flatpakId: "",
            steamAppId,
            alwaysAllowed: false,
            foregroundOnly: false,
            spentToday: 0,
            bonusToday: 0,
            running: false,
          });
          u.appLimits.enabled = true;
          UI.state.dirtyPolicies.add(uname);
          UI.state.userTab = "apps";
          UI.render();
          UI.showToast(`Added ${lbl} (45m/day) — click Save Changes`);
        }
      };
    });

    const tLimSl = document.getElementById("todayLimSlider");
    if (tLimSl) {
      tLimSl.oninput = () => {
        const u = getU();
        if (!u) return;
        u.todayLimitSec = UI.sliderValToSec(tLimSl.value);
        UI.state.dirtyToday.add(uname);
        const left = UI.timeLeftSec(u);
        const tv = document.getElementById("todayLimVal");
        if (tv) tv.textContent = UI.fmtDur(u.todayLimitSec);
        const pv = document.getElementById("pillLeftVal");
        if (pv) {
          pv.textContent =
            u.enforcementState === "paused"
              ? "Paused"
              : u.enforcementState === "homework"
                ? "Study Mode"
                : UI.fmtDur(left);
        }
        const pb = document.getElementById("pillLeftBox");
        if (pb) {
          pb.className = `stat-pill ${u.enforcementState === "homework" ? "info" : u.enforcementState === "paused" ? "ok" : left === 0 ? "err" : left <= 600 ? "warn" : "primary"}`;
        }
        const hl = document.getElementById("hdrLimit");
        if (hl) hl.textContent = UI.fmtDur(u.todayLimitSec);
        const nl = document.querySelector(`[data-navleft="${CSS.escape(uname)}"]`);
        if (nl && !u.locked && u.role !== "parent") {
          nl.textContent =
            u.enforcementState === "paused"
              ? "Paused"
              : u.enforcementState === "homework"
                ? "Study"
                : UI.fmtDur(left);
        }
        UI.updateUnsavedBar();
      };
    }

    const btnLock = document.getElementById("btnLockModal");
    if (btnLock) {
      btnLock.onclick = () => {
        const u0 = getU();
        if (!u0) return;
        UI.openModal(`<h3 style="margin:0">Lock ${UI.escHtml(u0.fullName)}'s screen now?</h3>
          <div class="card-sub">Locks the screen immediately without closing open apps.</div>
          <div class="modal-acts">
            <button type="button" class="btn btn-outlined btn-sm" id="cancelLock">Cancel</button>
            <button type="button" class="btn btn-danger btn-sm" id="okLock">${UI.ICONS.lock} Lock Screen</button>
          </div>`);
        document.getElementById("cancelLock").onclick = UI.closeModal;
        document.getElementById("okLock").onclick = async () => {
          const okBtn = document.getElementById("okLock");
          okBtn.disabled = true;
          try {
            const res = await UI.rpc("lock_user", { username: uname });
            if (res && res.status) UI.mergeLiveUserStatus(uname, res.status);
            const cur = getU();
            if (res && res.result && res.result.locked) {
              if (cur) cur.locked = true;
              UI.showToast(`Locked ${(cur && cur.fullName) || uname}'s screen`);
            } else {
              UI.showToast(
                (res && res.result && res.result.detail) ||
                  `No active session for ${(cur && cur.fullName) || uname}`
              );
            }
            if (!UI.isDirty()) UI.snapshotState();
            UI.closeModal();
            UI.render();
          } catch (err) {
            UI.closeModal();
            UI.showToast(
              err.code === "permission_denied"
                ? "Administrator approval cancelled"
                : `Could not lock: ${err.message}`
            );
          }
        };
      };
    }
  }

  function wireScheduleEvents(uname) {
    if (!uname) return;
    const getU = () => UI.state.users[uname];
    const todayKey = UI.getTodayKey();
    const syncTodayHeaderIfClean = (u) => {
      if (!UI.state.dirtyToday.has(uname) && u.days && u.days[todayKey]) {
        u.todayLimitSec = u.days[todayKey].limit;
        u.todayWindows = UI.cloneWindows(u.days[todayKey].windows);
      }
    };

    document.querySelectorAll("[data-schedmode]").forEach((btn) => {
      btn.onclick = () => {
        const u = getU();
        if (!u) return;
        const nextMode = btn.dataset.schedmode;
        u.scheduleMode = nextMode;
        u.sameEveryDay = nextMode === "same";
        if (nextMode === "same") {
          const ref = u.days.mon;
          UI.DAYS.forEach((d) => {
            u.days[d] = { limit: ref.limit, windows: UI.cloneWindows(ref.windows) };
          });
          syncTodayHeaderIfClean(u);
          UI.state.dirtyPolicies.add(uname);
        } else if (nextMode === "weekday_weekend") {
          const wdRef = u.days.mon;
          const weRef = u.days.sat;
          UI.WEEKDAY_KEYS.forEach((d) => {
            u.days[d] = { limit: wdRef.limit, windows: UI.cloneWindows(wdRef.windows) };
          });
          UI.WEEKEND_KEYS.forEach((d) => {
            u.days[d] = { limit: weRef.limit, windows: UI.cloneWindows(weRef.windows) };
          });
          syncTodayHeaderIfClean(u);
          UI.state.dirtyPolicies.add(uname);
        }
        UI.render();
      };
    });

    document.querySelectorAll("[data-copyday]").forEach((btn) => {
      btn.onclick = () => {
        const u0 = getU();
        if (!u0) return;
        const src = btn.dataset.copyday;
        const ref = {
          limit: u0.days[src].limit,
          windows: UI.cloneWindows(u0.days[src].windows),
        };
        UI.openModal(`<h3 style="margin:0">Copy ${UI.escHtml(UI.DAY_FULL[src])} Schedule</h3>
          <div class="card-sub">Apply ${UI.fmtDur(ref.limit)} and its allowed windows to:</div>
          <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px">
            <button type="button" class="btn btn-tonal btn-sm" id="cpWeekdays">All School Days (Mon–Fri)</button>
            <button type="button" class="btn btn-tonal btn-sm" id="cpWeekends">Weekends (Sat–Sun)</button>
            <button type="button" class="btn btn-tonal btn-sm" id="cpAll">All 7 Days</button>
          </div>
          <div class="modal-acts"><button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button></div>`);
        const applyCopy = (targets) => {
          const u = getU();
          if (!u) return;
          targets.forEach((d) => {
            u.days[d] = { limit: ref.limit, windows: UI.cloneWindows(ref.windows) };
          });
          if (targets.includes(todayKey)) syncTodayHeaderIfClean(u);
          UI.state.dirtyPolicies.add(uname);
          UI.closeModal();
          UI.render();
          UI.showToast("Schedule copied (click Save Changes)");
        };
        document.getElementById("btnCloseModal").onclick = UI.closeModal;
        document.getElementById("cpWeekdays").onclick = () => applyCopy(UI.WEEKDAY_KEYS);
        document.getElementById("cpWeekends").onclick = () => applyCopy(UI.WEEKEND_KEYS);
        document.getElementById("cpAll").onclick = () => applyCopy(UI.DAYS);
      };
    });

    document.querySelectorAll("[data-daylim]").forEach((sl) => {
      const key = sl.dataset.daylim;
      sl.oninput = () => {
        const u = getU();
        if (!u) return;
        const sec = UI.sliderValToSec(sl.value);
        const targets =
          key === "all"
            ? UI.DAYS
            : key === "weekdays"
              ? UI.WEEKDAY_KEYS
              : key === "weekends"
                ? UI.WEEKEND_KEYS
                : [key];
        targets.forEach((d) => {
          u.days[d].limit = sec;
        });
        if (targets.includes(todayKey) && !UI.state.dirtyToday.has(uname)) {
          u.todayLimitSec = sec;
        }
        UI.state.dirtyPolicies.add(uname);
        const valEl = document.querySelector(`[data-daylimval="${key}"]`);
        if (valEl) valEl.textContent = UI.fmtDur(sec);
        const hdrLim = document.getElementById("hdrLimit");
        if (hdrLim) hdrLim.textContent = UI.fmtDur(u.todayLimitSec);
        UI.updateUnsavedBar();
      };
    });

    const btnHol = document.getElementById("btnAddHoliday");
    if (btnHol) btnHol.onclick = () => openAddHolidayModal(uname);
    document.querySelectorAll("[data-rmhol]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        u.holidays.splice(Number(b.dataset.rmhol), 1);
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });

    const selWk = document.getElementById("selWeeklyCap");
    if (selWk) {
      selWk.onchange = () => {
        const u = getU();
        if (!u) return;
        const v = Number(selWk.value);
        u.weeklyLimit = v > 0 ? v : null;
        UI.state.dirtyPolicies.add(uname);
        UI.updateUnsavedBar();
      };
    }
    const selMo = document.getElementById("selMonthlyCap");
    if (selMo) {
      selMo.onchange = () => {
        const u = getU();
        if (!u) return;
        const v = Number(selMo.value);
        u.monthlyLimit = v > 0 ? v : null;
        UI.state.dirtyPolicies.add(uname);
        UI.updateUnsavedBar();
      };
    }
    document.querySelectorAll("[data-setrole]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        u.role = b.dataset.setrole;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });
    const togGrace = document.getElementById("toggleSaveGrace");
    if (togGrace) {
      togGrace.onclick = () => {
        const u = getU();
        if (!u) return;
        u.saveGraceEnabled = !u.saveGraceEnabled;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    }
    document.querySelectorAll("[data-lockout]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        u.lockoutAction = b.dataset.lockout;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });
  }

  function wireAppLimitsEvents(uname) {
    if (!uname) return;
    const getU = () => UI.state.users[uname];

    const tog = document.getElementById("toggleApps");
    if (tog) {
      tog.onclick = () => {
        const u = getU();
        if (!u) return;
        u.appLimits.enabled = !u.appLimits.enabled;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    }
    const btnCat = document.getElementById("btnAddCategory");
    if (btnCat) btnCat.onclick = () => openAddCategoryModal(uname);
    document.querySelectorAll("[data-rmcat]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        u.appLimits.categories.splice(Number(b.dataset.rmcat), 1);
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });
    document.querySelectorAll("[data-catgrant]").forEach((b) => {
      b.onclick = async () => {
        const u = getU();
        if (!u) return;
        const c = u.appLimits.categories[Number(b.dataset.catgrant)];
        if (!c) return;
        b.disabled = true;
        try {
          const res = await UI.rpc("grant_category_time", {
            username: uname,
            category: c.category,
            delta_seconds: 900,
          });
          applyUserResp(uname, res);
          UI.showToast(`+15m added for All ${c.category}`);
        } catch (err) {
          b.disabled = false;
          UI.showToast(err.message || "Could not add category time");
        }
      };
    });

    // Surgical per-app slider drag (NO full UI.render() during oninput!)
    document.querySelectorAll("[data-applimit]").forEach((s) => {
      s.oninput = () => {
        const u = getU();
        if (!u) return;
        const idx = Number(s.dataset.applimit);
        const app = u.appLimits.apps[idx];
        if (!app) return;
        app.dailyLimit = Number(s.value) * 60;
        app.effectiveLimit = app.dailyLimit;
        UI.state.dirtyPolicies.add(uname);
        const allotted = Math.max(0, app.dailyLimit + (app.bonusToday || 0));
        const rem = Math.max(0, allotted - app.spentToday);
        const pct =
          allotted === 0
            ? 100
            : Math.min(100, Math.round((app.spentToday / Math.max(1, allotted)) * 100));
        const col = rem === 0 ? "var(--err)" : rem <= 600 ? "var(--warn)" : "var(--p)";
        const pCls = rem === 0 ? "p-err" : rem <= 600 ? "p-warn" : "p-ok";
        const limLbl = document.querySelector(`[data-applimlbl="${idx}"]`);
        if (limLbl) limLbl.textContent = UI.fmtAppLimit(app.dailyLimit);
        const remEl = document.querySelector(`[data-apprem="${idx}"]`);
        if (remEl) remEl.textContent = UI.fmtDur(rem);
        const pill = document.querySelector(`[data-apppill="${idx}"]`);
        if (pill) {
          pill.className = `pill ${pCls}`;
          pill.textContent =
            allotted === 0 ? "Blocked" : rem === 0 ? "Limit reached" : `${UI.fmtDur(rem)} left`;
        }
        const fill = document.querySelector(`[data-appfill="${idx}"]`);
        if (fill) {
          fill.style.width = `${pct}%`;
          fill.style.background = col;
        }
        UI.updateUnsavedBar();
      };
    });

    document.querySelectorAll("[data-appfg]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        const app = u.appLimits.apps[Number(b.dataset.appfg)];
        if (!app) return;
        app.foregroundOnly = !app.foregroundOnly;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });
    document.querySelectorAll("[data-appallow]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        const app = u.appLimits.apps[Number(b.dataset.appallow)];
        if (!app) return;
        app.alwaysAllowed = !app.alwaysAllowed;
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });

    document.querySelectorAll("[data-appgrant]").forEach((b) => {
      b.onclick = async () => {
        const u = getU();
        if (!u) return;
        const app = u.appLimits.apps[Number(b.dataset.appgrant)];
        if (!app) return;
        b.disabled = true;
        try {
          const res = await UI.rpc("grant_app_time", {
            username: uname,
            pattern: app.pattern,
            delta_seconds: 900,
          });
          applyUserResp(uname, res);
          UI.showToast(`+15m added for ${app.label} today`);
        } catch (err) {
          b.disabled = false;
          UI.showToast(
            err.code === "permission_denied"
              ? "Administrator approval cancelled"
              : `Could not add time: ${err.message}`
          );
        }
      };
    });

    document.querySelectorAll("[data-rmapp]").forEach((b) => {
      b.onclick = () => {
        const u = getU();
        if (!u) return;
        u.appLimits.apps.splice(Number(b.dataset.rmapp), 1);
        UI.state.dirtyPolicies.add(uname);
        UI.render();
      };
    });

    const btnAdd = document.getElementById("btnAddApp");
    if (btnAdd) btnAdd.onclick = () => openAddAppModal(uname);
  }

  function wireDoctorEvents() {
    const doFix = async () => {
      if (UI.state.busyFix) return;
      UI.state.busyFix = true;
      UI.render();
      try {
        const res = await UI.rpc("apply_doctor_fix", {});
        if (res && res.doctor) UI.state.doctor = res.doctor;
        UI.showToast("Lock-screen protection enabled");
      } catch (err) {
        UI.showToast(
          err.code === "permission_denied"
            ? "Administrator approval cancelled"
            : `Could not apply protection: ${err.message}`
        );
      } finally {
        UI.state.busyFix = false;
        UI.render();
      }
    };
    const b1 = document.getElementById("btnBanFix");
    if (b1) b1.onclick = doFix;
    const b2 = document.getElementById("btnDocFix");
    if (b2) b2.onclick = doFix;

    const bRef = document.getElementById("btnDocRefresh");
    if (bRef) {
      bRef.onclick = async () => {
        bRef.disabled = true;
        try {
          UI.state.doctor = await UI.rpc("refresh_doctor", {});
          UI.render();
          UI.showToast("Health checks refreshed");
        } catch (err) {
          bRef.disabled = false;
          UI.showToast(`Could not refresh: ${err.message}`);
        }
      };
    }

    const bTest = document.getElementById("btnTestLock");
    if (bTest) {
      bTest.onclick = () => {
        const loggedIn = Object.values(UI.state.users).filter((usr) => usr.loggedIn);
        if (!loggedIn.length) return;
        UI.openModal(`<h3 style="margin:0">Test Screen Lock</h3>
          <div class="card-sub">Locks the selected active desktop session right now so you can verify lock enforcement works cleanly on this desktop environment.</div>
          <select class="text-in" id="testLockUser">
            ${loggedIn
              .map(
                (usr) =>
                  `<option value="${UI.escHtml(usr.username)}">${UI.escHtml(usr.fullName)} (@${UI.escHtml(usr.username)})</option>`
              )
              .join("")}
          </select>
          <div class="modal-acts">
            <button type="button" class="btn btn-outlined btn-sm" id="btnCloseModal">Cancel</button>
            <button type="button" class="btn btn-filled btn-sm" id="btnRunTestLock">${UI.ICONS.lock} Run Test Lock</button>
          </div>`);
        document.getElementById("btnCloseModal").onclick = UI.closeModal;
        document.getElementById("btnRunTestLock").onclick = async () => {
          const uname = document.getElementById("testLockUser").value;
          const runBtn = document.getElementById("btnRunTestLock");
          runBtn.disabled = true;
          try {
            const res = await UI.rpc("test_lock", { username: uname });
            UI.closeModal();
            UI.showToast(
              res && res.locked
                ? `Test lock succeeded (${res.verified ? "verified" : "invoked"})`
                : `Test lock failed: ${(res && res.detail) || "check diagnostics"}`
            );
          } catch (err) {
            UI.closeModal();
            UI.showToast(err.message || "Test lock failed");
          }
        };
      };
    }

    const bd = document.getElementById("btnBanDismiss");
    if (bd) {
      bd.onclick = () => {
        UI.state.bannerDismissed = true;
        UI.render();
      };
    }
  }

  function wireSettingsEvents() {
    document.querySelectorAll("[data-cbool]").forEach((b) => {
      b.onclick = () => {
        if (!UI.state.config) return;
        const key = b.dataset.cbool;
        UI.state.config[key] = !UI.state.config[key];
        UI.state.dirtyConfig = true;
        UI.render();
      };
    });

    const idleSel = document.getElementById("cfgIdleSelect");
    if (idleSel) {
      idleSel.onchange = () => {
        if (!UI.state.config) return;
        UI.state.config.idle_pause_sec = Number(idleSel.value);
        UI.state.dirtyConfig = true;
        UI.updateUnsavedBar();
      };
    }

    const cdSl = document.getElementById("cfgCountdownSlider");
    if (cdSl) {
      cdSl.oninput = () => {
        if (!UI.state.config) return;
        UI.state.config.final_countdown_sec = Number(cdSl.value);
        UI.state.dirtyConfig = true;
        const valEl = document.getElementById("cfgCountdownVal");
        if (valEl) valEl.textContent = `${UI.state.config.final_countdown_sec}s`;
        UI.updateUnsavedBar();
      };
    }

    const setupPc = async (enable, rotate, btn) => {
      if (btn) btn.disabled = true;
      try {
        const res = await UI.rpc("setup_parent_code", { enable, rotate });
        UI.state.parentCode = { enabled: Boolean(res.enabled), created_utc: res.created_utc || "" };
        UI.state.parentCodeEnrollment = res.enabled && res.qr_svg ? res : null;
        UI.render();
        UI.showToast(
          res.enabled ? "Parent Access Code ready — scan QR now" : "Parent Access Code disabled"
        );
      } catch (err) {
        if (btn) btn.disabled = false;
        UI.showToast(err.message || "Could not update Parent Access Code");
      }
    };
    const pcEn = document.getElementById("btnPcEnable");
    if (pcEn) pcEn.onclick = () => setupPc(true, false, pcEn);
    const pcRot = document.getElementById("btnPcRotate");
    if (pcRot) pcRot.onclick = () => setupPc(true, true, pcRot);
    const pcDis = document.getElementById("btnPcDisable");
    if (pcDis) pcDis.onclick = () => setupPc(false, false, pcDis);

    document.querySelectorAll("[data-settheme]").forEach((b) => {
      b.onclick = async () => {
        UI.state.prefs.theme = b.dataset.settheme;
        UI.applyThemeAndAccent();
        UI.render();
        try {
          await UI.rpc("save_prefs", { theme: UI.state.prefs.theme });
        } catch {}
      };
    });
    document.querySelectorAll("[data-settf]").forEach((b) => {
      b.onclick = async () => {
        UI.state.prefs.time_format = b.dataset.settf;
        UI.render();
        try {
          await UI.rpc("save_prefs", { time_format: UI.state.prefs.time_format });
        } catch {}
      };
    });
    document.querySelectorAll("[data-accent]").forEach((b) => {
      b.onclick = async () => {
        UI.state.prefs.accent = b.dataset.accent;
        UI.applyThemeAndAccent();
        UI.render();
        try {
          await UI.rpc("save_prefs", { accent: UI.state.prefs.accent });
        } catch {}
      };
    });
  }

  function wireMyStatusEvents() {
    if (UI.state.surface !== "my_status") return;
    const ask = document.getElementById("btnMyAskTime");
    if (ask) ask.onclick = openAskTimeModal;
    const pc = document.getElementById("btnMyParentCode");
    if (pc) pc.onclick = () => openParentCodeModal(null);
    const grace = document.getElementById("btnMySaveGrace");
    if (grace) {
      grace.onclick = async () => {
        grace.disabled = true;
        try {
          const res = await UI.rpc("claim_save_grace", {});
          if (res && res.status) UI.state.me = UI.normalizeUserStatus(res.status, UI.state.me);
          UI.render();
          UI.showToast("2-minute Save Grace active!");
        } catch (err) {
          grace.disabled = false;
          UI.showToast(err.message || "Save grace unavailable");
        }
      };
    }
    document.querySelectorAll("[data-quickcode]").forEach((b) => {
      b.onclick = () => {
        const reqId = Number(b.dataset.quickcode);
        const found = (UI.state.myRequests || []).find((r) => r.id === reqId);
        openParentCodeModal(found || null);
      };
    });
    document.querySelectorAll("[data-quickpolkit]").forEach((b) => {
      b.onclick = async () => {
        b.disabled = true;
        try {
          const res = await UI.rpc("quick_approve_request", {
            request_id: Number(b.dataset.quickpolkit),
            grant_seconds: 0,
            note: "Walk-up approval",
          });
          if (res && res.status) UI.state.me = UI.normalizeUserStatus(res.status, UI.state.me);
          if (res && res.request) {
            UI.state.myRequests = (UI.state.myRequests || []).map((r) =>
              r.id === res.request.id ? res.request : r
            );
          }
          UI.render();
          UI.showToast("Extra time approved!");
        } catch (err) {
          b.disabled = false;
          UI.showToast(err.message || "Approval cancelled");
        }
      };
    });
  }

  function wireEvents() {
    const u = UI.currentUser();
    const uname = u ? u.username : null;
    wireWizardEvents();
    wireNavigationEvents();
    wireOverviewEvents(uname);
    wireScheduleEvents(uname);
    wireTimelineBars(uname);
    wireAppLimitsEvents(uname);
    wireDoctorEvents();
    wireSettingsEvents();
    wireMyStatusEvents();
  }

  function wireGlobalEvents() {
    document.getElementById("btnSave").onclick = UI.commitChanges;
    document.getElementById("btnDiscard").onclick = UI.discardChanges;
    document.getElementById("modalBg").onclick = (e) => {
      if (e.target.id === "modalBg") UI.closeModal();
    };
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") UI.closeModal();
    });
  }

  Object.assign(UI, {
    wireEvents,
    wireGlobalEvents,
    openAskTimeModal,
    openParentCodeModal,
  });
})((window.CurfewUI = window.CurfewUI || {}));
