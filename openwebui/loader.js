(function () {
  const POLL_MS = 2500;
  const TAB_SIGNAL_KEY = "codex_loader_chat_signal_v1";
  const DEBUG = false;

  let pending = false;
  let lastChatId = null;
  let lastSignature = null;
  let focusRefreshArmed = false;
  let lastRefreshAt = 0;

  function log() {
    if (DEBUG) console.log("[codex-loader]", ...arguments);
  }

  function now() {
    return Date.now();
  }

  function currentChatId() {
    const match = window.location.pathname.match(/^\/c\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function getToken() {
    try {
      return localStorage.getItem("token");
    } catch (_) {
      return null;
    }
  }

  function messageCount(chat) {
    try {
      return Object.keys(chat?.history?.messages || {}).length;
    } catch (_) {
      return 0;
    }
  }

  function buildSignature(chatId, payload) {
    const chat = payload?.chat || payload || {};
    const currentId = chat?.history?.currentId || "";
    const updatedAt = chat?.updated_at || chat?.updatedAt || chat?.timestamp || "";
    const count = messageCount(chat);
    return [chatId, currentId, updatedAt, count].join("|");
  }

  async function fetchChat(chatId) {
    const url = `/api/v1/chats/${encodeURIComponent(chatId)}`;
    let res = await fetch(url, {
      credentials: "include",
      cache: "no-store",
    });

    if (!res.ok) {
      const token = getToken();
      if (!token) return null;
      res = await fetch(url, {
        credentials: "include",
        cache: "no-store",
        headers: { Authorization: `Bearer ${token}` },
      });
    }

    if (!res.ok) return null;
    return await res.json();
  }

  function publishSignal(signature) {
    try {
      localStorage.setItem(
        TAB_SIGNAL_KEY,
        JSON.stringify({ signature, ts: now(), href: location.href })
      );
    } catch (_) {}
  }

  function hardReload() {
    if (now() - lastRefreshAt < 3000) return;
    lastRefreshAt = now();
    location.reload();
  }

  async function tick(reason) {
    if (pending) return;

    const chatId = currentChatId();
    if (!chatId) {
      lastChatId = null;
      lastSignature = null;
      return;
    }

    pending = true;
    try {
      const payload = await fetchChat(chatId);
      if (!payload) return;

      const signature = buildSignature(chatId, payload);

      if (chatId !== lastChatId) {
        lastChatId = chatId;
        lastSignature = signature;
        return;
      }

      if (lastSignature && signature !== lastSignature) {
        log("change", reason, { old: lastSignature, newSig: signature });
        lastSignature = signature;
        publishSignal(signature);

        if (document.visibilityState === "visible") {
          focusRefreshArmed = true;
          return;
        }

        hardReload();
        return;
      }

      lastSignature = signature;
    } catch (err) {
      log("tick error", err);
    } finally {
      pending = false;
    }
  }

  window.addEventListener("storage", function (event) {
    if (event.key !== TAB_SIGNAL_KEY || !event.newValue) return;

    try {
      const data = JSON.parse(event.newValue);
      if (!data?.signature) return;
      if (data.signature === lastSignature) return;

      focusRefreshArmed = true;

      if (document.visibilityState !== "visible") {
        hardReload();
      }
    } catch (_) {}
  });

  window.addEventListener("focus", function () {
    if (focusRefreshArmed) {
      focusRefreshArmed = false;
      hardReload();
      return;
    }
    tick("focus");
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      if (focusRefreshArmed) {
        focusRefreshArmed = false;
        hardReload();
        return;
      }
      tick("visible");
    }
  });

  setInterval(function () {
    tick("poll");
  }, POLL_MS);

  tick("init");
})();
