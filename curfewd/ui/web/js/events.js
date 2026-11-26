/* View-scoped DOM event binders and Add App modal for curfewd-ui (Spec 005). */
(function (UI) {
  "use strict";

  function openAddAppModal(u) {
    const allApps = UI.state.installedApps || [];
    let filterText = "";

    const drawModal = () => {
      const q = filterText.trim().toLowerCase();
      const filtered = allApps.filter(
        (a) => !q || a.label.toLowerCase().includes(q) || a.category.toLowerCase().includes(q)
      );
      UI.openModal(`<h3 style="margin:0">Add App / Game Limit</h3>
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
        <input class="text-in" id="appSearch" type="search" placeholder="Search installed apps..." value="${UI.escHtml(filterText)}"/>
        <div id="appPickList" style="display:flex;flex-direction:column;gap:6px;max-height:210px;overflow-y:auto">
          ${
            filtered.length
              ? filtered
                  .map(
                    (
                      a,
                      idx
                    ) => `<button type="button" class="pick-row" data-pickpat="${UI.escHtml(a.pattern)}" data-pickidx="${idx}">
            <span><strong>${UI.escHtml(a.label)}</strong></span>
            <span class="pick-cat">${UI.escHtml(a.category || "App")}</span>
          </button>`
                  )
                  .join("")
              : `<div class="card-sub" style="padding:8px 2px">No matching installed apps found.</div>`
          }
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
            if (pos !== null && pos !== undefined) next.setSelectionRange(pos, pos);
          }
        };
      }

      const addRule = (pattern, label, category) => {
        const lim = Number((limSel && limSel.value) || 2700);
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
            spentToday: 0,
            bonusToday: 0,
            running: false,
          });
          u.appLimits.enabled = true;
          UI.state.dirtyPolicies.add(u.username);
          UI.updateUnsavedBar();
        }
        UI.closeModal();
        UI.render();
        UI.showToast(`Added ${label || pattern} (${UI.fmtDur(lim)}/day)`);
      };

      document.querySelectorAll("[data-pickpat]").forEach((btn) => {
        btn.onclick = () => {
          const pat = btn.dataset.pickpat;
          const found = allApps.find((x) => x.pattern === pat);
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
      if (btnClose) btnClose.onclick = UI.closeModal;
    };

    drawModal();
  }

  function wireNavigationEvents() {
    document.querySelectorAll("[data-nav]").forEach((b) => {
      b.onclick = () => {
        UI.state.nav = b.dataset.nav;
        UI.render();
      };
    });
    document.querySelectorAll("[data-utab]").forEach((b) => {
      b.onclick = () => {
        UI.state.userTab = b.dataset.utab;
        UI.render();
      };
    });
    document.querySelectorAll("[data-hspan]").forEach((b) => {
      b.onclick = () => {
        UI.state.historySpan = Number(b.dataset.hspan);
        UI.render();
      };
    });

    const btnAuth = document.getElementById("btnAuth");
    if (btnAuth) btnAuth.onclick = () => UI.bootstrap("admin");
    const btnSelf = document.getElementById("btnSelf");
    if (btnSelf) btnSelf.onclick = () => UI.bootstrap("my_status");
    const btnRetry = document.getElementById("btnRetry");
    if (btnRetry) btnRetry.onclick = () => UI.bootstrap(UI.initialMode());
  }

  function wireOverviewEvents(u) {
    if (!u) return;

    const tLimSl = document.getElementById("todayLimSlider");
    if (tLimSl) {
      tLimSl.oninput = () => {
        u.todayLimitSec = UI.sliderValToSec(tLimSl.value);
        UI.state.dirtyToday.add(u.username);
        const left = UI.timeLeftSec(u);
        const tv = document.getElementById("todayLimVal");
        if (tv) tv.textContent = UI.fmtDur(u.todayLimitSec);
        const pv = document.getElementById("pillLeftVal");
        if (pv) pv.textContent = UI.fmtDur(left);
        const pb = document.getElementById("pillLeftBox");
        if (pb) pb.className = `stat-pill ${left === 0 ? "err" : left <= 600 ? "warn" : "primary"}`;
        const hl = document.getElementById("hdrLimit");
        if (hl) hl.textContent = UI.fmtDur(u.todayLimitSec);
        const nl = document.querySelector(`[data-navleft="${CSS.escape(u.username)}"]`);
        if (nl && !u.locked) nl.textContent = UI.fmtDur(left);
        UI.updateUnsavedBar();
      };
    }

    const btnLock = document.getElementById("btnLockModal");
    if (btnLock) {
      btnLock.onclick = () => {
        UI.openModal(`<h3 style="margin:0">Lock ${UI.escHtml(u.fullName)}'s screen now?</h3>
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
            const res = await UI.rpc("lock_user", { username: u.username });
            if (res && res.status) UI.state.users[u.username] = UI.normalizeUserStatus(res.status);
            if (res && res.result && res.result.locked) {
              UI.state.users[u.username].locked = true;
              UI.showToast(`Locked ${u.fullName}'s screen`);
            } else {
              UI.showToast(
                (res && res.result && res.result.detail) || `No active session for ${u.fullName}`
              );
            }
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

  function wireTimelineBars(u) {
    if (!u) return;
    const todayKey = UI.getTodayKey();
    const hdrCurfew = document.getElementById("hdrCurfew");

    document.querySelectorAll("[data-winbar]").forEach((bar) => {
      const key = bar.dataset.winbar;
      const seg = bar.querySelector(".timeline-seg");

      const getRange = () =>
        key === "today"
          ? { startMin: u.todayStartMin, endMin: u.todayEndMin }
          : key === "all"
            ? u.days.mon
            : u.days[key];

      const setRange = (s, e) => {
        if (key === "today") {
          u.todayStartMin = s;
          u.todayEndMin = e;
          UI.state.dirtyToday.add(u.username);
        } else if (key === "all") {
          UI.DAYS.forEach((d) => {
            u.days[d].startMin = s;
            u.days[d].endMin = e;
          });
          if (!UI.state.dirtyToday.has(u.username)) {
            u.todayStartMin = s;
            u.todayEndMin = e;
          }
          UI.state.dirtyPolicies.add(u.username);
        } else {
          u.days[key].startMin = s;
          u.days[key].endMin = e;
          if (key === todayKey && !UI.state.dirtyToday.has(u.username)) {
            u.todayStartMin = s;
            u.todayEndMin = e;
          }
          UI.state.dirtyPolicies.add(u.username);
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
        lh.querySelector(".handle-tip").textContent = UI.fmtMin(startMin);
        rh.style.left = `${ep}%`;
        rh.querySelector(".handle-tip").textContent = UI.fmtMin(endMin);
        if (hdrCurfew) hdrCurfew.textContent = UI.todayCurfew(u);
      };

      const setEdge = (edge, raw) => {
        const sn = Math.round(raw / 15) * 15;
        const cur = getRange();
        const s = edge === "start" ? Math.max(0, Math.min(cur.endMin - 60, sn)) : cur.startMin;
        const e = edge === "end" ? Math.max(cur.startMin + 60, Math.min(1440, sn)) : cur.endMin;
        setRange(s, e);
        syncUI();
        UI.updateUnsavedBar();
      };

      bar.querySelectorAll(".timeline-handle").forEach((h) => {
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
            setEdge(
              edge,
              (edge === "start" ? cur.startMin : cur.endMin) + (e.key === "ArrowRight" ? 15 : -15)
            );
          }
        };
      });
    });
  }

  function wireScheduleEvents(u) {
    if (!u) return;

    const chkSame = document.getElementById("chkSameAll");
    if (chkSame) {
      chkSame.onchange = () => {
        u.sameEveryDay = chkSame.checked;
        if (u.sameEveryDay) {
          const ref = Object.assign({}, u.days.mon);
          UI.DAYS.forEach((d) => {
            u.days[d] = Object.assign({}, ref);
          });
          if (!UI.state.dirtyToday.has(u.username)) {
            u.todayLimitSec = ref.limit;
            u.todayStartMin = ref.startMin;
            u.todayEndMin = ref.endMin;
          }
          UI.state.dirtyPolicies.add(u.username);
          UI.updateUnsavedBar();
        }
        UI.render();
      };
    }

    const todayKey = UI.getTodayKey();
    document.querySelectorAll("[data-daylim]").forEach((sl) => {
      const key = sl.dataset.daylim;
      sl.oninput = () => {
        const sec = UI.sliderValToSec(sl.value);
        if (key === "all") {
          UI.DAYS.forEach((d) => {
            u.days[d].limit = sec;
          });
          if (!UI.state.dirtyToday.has(u.username)) u.todayLimitSec = sec;
        } else {
          u.days[key].limit = sec;
          if (key === todayKey && !UI.state.dirtyToday.has(u.username)) u.todayLimitSec = sec;
        }
        UI.state.dirtyPolicies.add(u.username);
        const valEl = document.querySelector(`[data-daylimval="${key}"]`);
        if (valEl) valEl.textContent = UI.fmtDur(sec);
        const hdrLim = document.getElementById("hdrLimit");
        if (hdrLim) hdrLim.textContent = UI.fmtDur(u.todayLimitSec);
        const nl = document.querySelector(`[data-navleft="${CSS.escape(u.username)}"]`);
        if (nl && !u.locked) nl.textContent = UI.fmtDur(UI.timeLeftSec(u));
        UI.updateUnsavedBar();
      };
    });

    document.querySelectorAll("[data-lockout]").forEach((b) => {
      b.onclick = () => {
        u.lockoutAction = b.dataset.lockout;
        UI.state.dirtyPolicies.add(u.username);
        UI.render();
      };
    });
  }

  function wireAppLimitsEvents(u) {
    if (!u) return;

    const tog = document.getElementById("toggleApps");
    if (tog) {
      tog.onclick = () => {
        u.appLimits.enabled = !u.appLimits.enabled;
        UI.state.dirtyPolicies.add(u.username);
        UI.render();
      };
    }

    document.querySelectorAll("[data-applimit]").forEach((s) => {
      s.oninput = () => {
        const app = u.appLimits.apps[Number(s.dataset.applimit)];
        if (!app) return;
        app.dailyLimit = Number(s.value) * 60;
        UI.state.dirtyPolicies.add(u.username);
        UI.render();
      };
    });

    document.querySelectorAll("[data-appgrant]").forEach((b) => {
      b.onclick = async () => {
        const app = u.appLimits.apps[Number(b.dataset.appgrant)];
        if (!app) return;
        b.disabled = true;
        try {
          const res = await UI.rpc("grant_app_time", {
            username: u.username,
            pattern: app.pattern,
            delta_seconds: 900,
          });
          if (res && res.status && !UI.state.dirtyPolicies.has(u.username)) {
            UI.state.users[u.username] = UI.normalizeUserStatus(res.status);
            UI.snapshotState();
          } else {
            app.bonusToday = (app.bonusToday || 0) + 900;
          }
          UI.render();
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
        u.appLimits.apps.splice(Number(b.dataset.rmapp), 1);
        UI.state.dirtyPolicies.add(u.username);
        UI.render();
      };
    });

    const btnAdd = document.getElementById("btnAddApp");
    if (btnAdd) btnAdd.onclick = () => openAddAppModal(u);
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
        try {
          UI.state.doctor = await UI.rpc("refresh_doctor", {});
          UI.render();
          UI.showToast("Health checks refreshed");
        } catch (err) {
          UI.showToast(`Could not refresh: ${err.message}`);
        }
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

  function wireEvents() {
    const u = UI.currentUser();
    wireNavigationEvents();
    wireOverviewEvents(u);
    wireScheduleEvents(u);
    wireTimelineBars(u);
    wireAppLimitsEvents(u);
    wireDoctorEvents();
    wireSettingsEvents();
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
  });
})((window.CurfewUI = window.CurfewUI || {}));
