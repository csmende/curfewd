/* Thin entrypoint & bootstrap orchestrator for curfewd-ui (Spec 005). */
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
      } else if (UI.state.surface === "admin") {
        UI.state.users = {};
        for (const [uname, st] of Object.entries(data.users || {})) {
          UI.state.users[uname] = UI.normalizeUserStatus(st);
        }
        UI.state.histories = data.histories || {};
        UI.state.config = data.config || null;
        UI.state.doctor = data.doctor || null;
        UI.state.installedApps = data.installed_apps || [];
        const names = Object.keys(UI.state.users);
        // Prefer first non-current or configured restricted user so parent lands on a kid account first
        const preferred = names.find((n) => n !== UI.state.currentUser) || names[0] || "";
        UI.state.nav = preferred ? `user:${preferred}` : "doctor";
        UI.snapshotState();
        UI.clearDirty();
      }
      render();
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

  // Refresh the live `Now` needle once per minute
  setInterval(() => {
    if (UI.state.surface === "admin" && UI.state.userTab === "overview" && !UI.isDirty()) render();
    else if (UI.state.surface === "my_status") render();
  }, 60000);

  bootstrap(initialMode());
})((window.CurfewUI = window.CurfewUI || {}));
