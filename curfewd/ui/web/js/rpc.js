/* In-process WebKitGTK JSON RPC bridge for curfewd-ui (Spec 005). */
(function (UI) {
  "use strict";

  let rpcSeq = 0;
  const rpcPending = new Map();

  function rpc(action, params = {}) {
    return new Promise((resolve, reject) => {
      const id = String(++rpcSeq);
      rpcPending.set(id, { resolve, reject });
      const handler =
        window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.curfewd;
      if (!handler) {
        rpcPending.delete(id);
        reject(new Error("Desktop bridge unavailable"));
        return;
      }
      handler.postMessage(JSON.stringify({ id, action, params }));
    });
  }

  window.__curfewdResolve = function (id, resp) {
    const entry = rpcPending.get(String(id));
    if (!entry) return;
    rpcPending.delete(String(id));
    if (resp && resp.ok) {
      entry.resolve(resp.data);
    } else {
      const err = new Error((resp && resp.message) || "Request failed");
      err.code = (resp && resp.code) || "error";
      entry.reject(err);
    }
  };

  window.__curfewdEvent = function (eventName, payload) {
    if (
      eventName === "user_status" &&
      payload &&
      payload.username &&
      payload.status &&
      typeof UI.applyLiveStatusPush === "function"
    ) {
      UI.applyLiveStatusPush(payload.username, payload.status);
    } else if (
      eventName === "time_request" &&
      payload &&
      payload.request &&
      typeof UI.applyTimeRequestPush === "function"
    ) {
      UI.applyTimeRequestPush(payload.username || "", payload.request);
    }
  };

  UI.rpc = rpc;
})((window.CurfewUI = window.CurfewUI || {}));
