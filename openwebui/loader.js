(function () {
  const POLL_MS = 2500;
  const DETAILS_SCAN_MS = 1500;
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

  function ensureAdminDetailsStyles() {
    if (document.getElementById("codex-admin-details-style")) return;

    const style = document.createElement("style");
    style.id = "codex-admin-details-style";
    style.textContent = [
      ".codex-admin-output { white-space: pre-wrap; }",
      ".codex-admin-details { margin: 0.65rem 0; border: 1px solid rgba(120,120,120,.24); border-radius: 8px; overflow: hidden; background: rgba(120,120,120,.06); }",
      ".codex-admin-details > summary { cursor: pointer; padding: .55rem .75rem; font-weight: 600; user-select: none; list-style: disclosure-closed; }",
      ".codex-admin-details[open] > summary { border-bottom: 1px solid rgba(120,120,120,.18); list-style: disclosure-open; }",
      ".codex-admin-details pre { margin: 0; padding: .75rem; overflow: auto; max-height: 34rem; background: transparent; white-space: pre-wrap; }",
      ".codex-admin-details code { white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .86em; }",
    ].join("\n");
    document.head.appendChild(style);
  }

  function decodeEntities(value) {
    const textarea = document.createElement("textarea");
    textarea.innerHTML = value;
    return textarea.value;
  }

  function appendText(container, text) {
    if (!text) return;
    const div = document.createElement("div");
    div.className = "codex-admin-output";
    div.textContent = decodeEntities(text).replace(/\n{3,}/g, "\n\n").trim();
    if (div.textContent) container.appendChild(div);
  }

  function appendDetails(container, title, body) {
    const details = document.createElement("details");
    details.className = "codex-admin-details";

    const summary = document.createElement("summary");
    summary.textContent = decodeEntities(title || "details");

    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = decodeEntities(body || "(empty)").trim();
    pre.appendChild(code);

    details.appendChild(summary);
    details.appendChild(pre);
    container.appendChild(details);
  }

  function renderDetailsText(text) {
    const pattern = /<details><summary>([\s\S]*?)<\/summary>\s*(?:<pre><code>)?([\s\S]*?)(?:<\/code><\/pre>\s*)?<\/details>/gi;
    const container = document.createElement("div");
    let cursor = 0;
    let matched = false;
    let match;

    while ((match = pattern.exec(text)) !== null) {
      matched = true;
      appendText(container, text.slice(cursor, match.index));
      appendDetails(container, match[1], match[2]);
      cursor = pattern.lastIndex;
    }

    if (!matched) return null;
    appendText(container, text.slice(cursor));
    return container;
  }

  function shouldSkipDetailsRoot(el) {
    if (!el || el.dataset.codexDetailsRendered === "1") return true;
    if (el.closest("textarea,input,script,style")) return true;
    if (el.closest(".codex-admin-details")) return true;
    const text = el.textContent || "";
    if (!text.includes("<details><summary>") || !text.includes("</details>")) return true;
    return Array.from(el.children || []).some(function (child) {
      const childText = child.textContent || "";
      return childText.includes("<details><summary>") && childText.includes("</details>");
    });
  }

  function enhanceAdminDetails() {
    ensureAdminDetailsStyles();

    const roots = Array.from(document.querySelectorAll("div, p, section, article")).filter(function (el) {
      return !shouldSkipDetailsRoot(el);
    });

    roots.forEach(function (el) {
      const rendered = renderDetailsText(el.textContent || "");
      if (!rendered) return;
      el.dataset.codexDetailsRendered = "1";
      el.replaceChildren.apply(el, Array.from(rendered.childNodes));
    });
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

  setInterval(enhanceAdminDetails, DETAILS_SCAN_MS);
  new MutationObserver(enhanceAdminDetails).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  enhanceAdminDetails();
  tick("init");
})();
