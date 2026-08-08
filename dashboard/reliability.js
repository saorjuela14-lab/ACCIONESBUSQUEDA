/**
 * UI reliability: loading / empty / error + retry, error boundary, apiRetry.
 */
(function () {
  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderState(el, kind, opts = {}) {
    if (!el) return;
    const title = opts.title || (
      kind === "loading" ? "Cargando…" :
      kind === "empty" ? "Sin datos" :
      "Algo falló"
    );
    const detail = opts.detail || "";
    const retryLabel = opts.retryLabel || "Reintentar";
    const retryAttr = opts.retryId ? ` data-retry="${escapeHtml(opts.retryId)}"` : "";
    if (kind === "loading") {
      el.innerHTML = `<div class="ui-state ui-state-loading"><div class="ui-spinner"></div><p>${escapeHtml(title)}</p>${detail ? `<p class="muted">${escapeHtml(detail)}</p>` : ""}</div>`;
      return;
    }
    if (kind === "empty") {
      el.innerHTML = `<div class="ui-state ui-state-empty"><p>${escapeHtml(title)}</p>${detail ? `<p class="muted">${escapeHtml(detail)}</p>` : ""}</div>`;
      return;
    }
    el.innerHTML = `<div class="ui-state ui-state-error"><p>${escapeHtml(title)}</p>${detail ? `<p class="muted">${escapeHtml(detail)}</p>` : ""}<button type="button" class="btn ui-retry"${retryAttr}>${escapeHtml(retryLabel)}</button></div>`;
  }

  async function apiRetry(fn, { retries = 2, delayMs = 600, label = "solicitud" } = {}) {
    let lastErr;
    for (let i = 0; i <= retries; i++) {
      try {
        return await fn();
      } catch (e) {
        lastErr = e;
        if (i === retries) break;
        await new Promise((r) => setTimeout(r, delayMs * (i + 1)));
      }
    }
    throw lastErr || new Error(`Falló ${label}`);
  }

  function reportClientError(payload) {
    try {
      const token = localStorage.getItem("nexbuy_token");
      const headers = { "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      fetch("/api/v1/auth/client-error", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => {});
    } catch { /* ignore */ }
  }

  function installErrorBoundary() {
    const banner = document.getElementById("global-error-banner");
    const show = (msg) => {
      if (!banner) return;
      banner.classList.remove("hidden");
      const text = banner.querySelector(".msg");
      if (text) text.textContent = msg;
    };
    window.addEventListener("error", (ev) => {
      const msg = ev.message || "Error de interfaz";
      show(`Error de interfaz: ${msg}`);
      reportClientError({
        message: String(msg).slice(0, 500),
        source: "window.onerror",
        url: location.href,
        stack: (ev.error && ev.error.stack) ? String(ev.error.stack).slice(0, 2000) : "",
      });
    });
    window.addEventListener("unhandledrejection", (ev) => {
      const reason = ev.reason;
      const msg = (reason && reason.message) || String(reason || "promesa rechazada");
      show(`Error: ${msg}`);
      reportClientError({
        message: String(msg).slice(0, 500),
        source: "unhandledrejection",
        url: location.href,
        stack: (reason && reason.stack) ? String(reason.stack).slice(0, 2000) : "",
      });
    });
    banner?.querySelector("[data-dismiss]")?.addEventListener("click", () => {
      banner.classList.add("hidden");
    });
    banner?.querySelector("[data-reload]")?.addEventListener("click", () => location.reload());
  }

  function bindRetries(root, handlers) {
    (root || document).addEventListener("click", (ev) => {
      const btn = ev.target.closest?.("[data-retry]");
      if (!btn) return;
      const id = btn.getAttribute("data-retry");
      if (id && typeof handlers[id] === "function") handlers[id]();
    });
  }

  window.MonarchUI = {
    renderState,
    apiRetry,
    reportClientError,
    installErrorBoundary,
    bindRetries,
    escapeHtml,
  };
})();
