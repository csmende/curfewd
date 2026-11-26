/* Thin entrypoint & bootstrap orchestrator for curfewd-ui (Spec 005 + Spec 006). */
(function (UI) {
  "use strict";

  function render() {
    UI.applyThemeAndAccent();
    const root = document.getElementById("root");
    if (UI.state.surface === "admin") {
      root.innerHTML = UI.renderAdminShell();
    } else if (UI.state.surface === "my_status") {
      root.innerHTML = UI.renderMyScreenTime();
    } else if (UI.state.surface === "auth") {
      root.innerHTML = UI.renderAuthGate();
    } else if (UI.state.surface === "unavailable") {
      root.innerHTML = UI.renderUnavailable(UI.state.errorMsg);
    }
    UI.updateUnsavedBar();
    UI.wireEvents();
  }

  function initialMode() {
    const qs = new URLSearchParams(window.location.search);
    return qs.get("mode") === "my_status" ? "my_status" : "admin";
  }

  function initialAskTime() {
    const qs = new URLSearchParams(window.location.search);
    return qs.get("ask") === "1";
  }

  async function bootstrap(mode) {
    try {
      const data = await UI.rpc("bootstrap", { mode });
      UI.state.prefs = data.prefs || UI.state.prefs;
      UI.state.version = data.version || "";
      UI.state.currentUser = data.current_user || "";
      UI.state.surface = data.surface || "unavailable";
      UI.state.errorMsg = data.error || "";

      if (UI.state.surface === "my_status") {
        UI.state.me = data.me ? UI.normalizeUserStatus(data.me) : null;
        UI.state.myHistory7 = data.history7 || null;
        UI.state.myRequests = data.my_requests || [];
      } else if (UI.state.surface === "admin") {
        UI.state.users = {};
        for (const [uname, st] of Object.entries(data.users || {})) {
          UI.state.users[uname] = UI.normalizeUserStatus(st);
        }
        UI.state.histories = data.histories || {};
        UI.state.config = data.config || null;
        UI.state.doctor = data.doctor || null;
        UI.state.timeRequests = data.time_requests || [];
        UI.state.parentCode = data.parent_code || { enabled: false, created_utc: "" };
        UI.state.installedApps = data.installed_apps || [];
        UI.state.userAppsCache = {};
        UI.state.showSetupWizard = Boolean(
          UI.state.config && UI.state.config.setup_completed === false
        );
        UI.state.wizardInitialized = false;

        const allUsers = Object.values(UI.state.users);
        const kids = allUsers.filter((u) => u.role !== "parent");
        const preferred =
          kids.find((u) => u.username !== UI.state.currentUser) ||
          kids[0] ||
          allUsers.find((u) => u.username !== UI.state.currentUser) ||
          allUsers[0] ||
          null;
        UI.state.nav = preferred ? `user:${preferred.username}` : "doctor";
        UI.snapshotState();
        UI.clearDirty();
      }
      render();
      if (UI.state.surface === "my_status" && initialAskTime() && UI.openAskTimeModal) {
        UI.openAskTimeModal();
      }
    } catch (err) {
      UI.state.surface = "unavailable";
      UI.state.errorMsg = err.message || "Unable to connect to screen-time service.";
      render();
    }
  }

  Object.assign(UI, {
    render,
    initialMode,
    bootstrap,
  });

  UI.wireGlobalEvents();

  // Refresh only the live Now needle once per minute without tearing down the DOM
  setInterval(() => {
    const pct = `${((UI.nowMinutes() / 1440) * 100).toFixed(2)}%`;
    document.querySelectorAll(".timeline-needle").forEach((el) => {
      el.style.left = pct;
      el.title = `Current time (${UI.fmtTimeMin(UI.nowMinutes())})`;
    });
    const nowLbl = document.getElementById("todayNowLabel");
    if (nowLbl) nowLbl.textContent = `Now: ${UI.fmtTimeMin(UI.nowMinutes())}`;
  }, 60000);

  bootstrap(initialMode());
})((window.CurfewUI = window.CurfewUI || {}));
