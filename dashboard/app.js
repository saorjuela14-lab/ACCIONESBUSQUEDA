const API = "/api/v1";
const REFRESH_MS = 120000;
const LOCALE = "es";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const SECTOR_ES = {
  Technology: "Tecnología",
  Healthcare: "Salud",
  Financials: "Financiero",
  Energy: "Energía",
  Industrials: "Industrial",
  "Consumer Disc.": "Consumo Discrecional",
  "Consumer Staples": "Consumo Básico",
  Utilities: "Servicios Públicos",
  Materials: "Materiales",
  "Real Estate": "Inmobiliario",
  Communication: "Comunicaciones",
};

const INDEX_ES = {
  "S&P 500": "S&P 500",
  "Nasdaq 100": "Nasdaq 100",
  "Dow Jones": "Dow Jones",
  "Russell 2000": "Russell 2000",
  VIX: "VIX",
  "US Dollar Index": "Índice del Dólar",
};

const AGENT_ES = {
  news: "Noticias",
  technical: "Técnico",
  sentiment: "Sentimiento",
  market_dependency: "Dependencias",
  macro: "Macro",
  valuation: "Valoración",
  risk: "Riesgo",
  options: "Opciones",
  investment_memory: "Memoria",
  fundamental: "Fundamental",
  portfolio: "Portafolio",
  watchlist: "Watchlist",
  alert: "Alertas",
  company_risk: "Riesgo co.",
  country_risk: "Riesgo país",
  corporate_actions: "Corp. actions",
};

const charts = {};
let lwCharts = { candle: null, rsi: null, macd: null };
let lastProposal = null;
let lastPortfolioId = null;
let lastNewsItems = [];
let lastAllocationPlan = null;
let lastDiscoveryReport = null;
let lastThesis = null;

const BUCKET_ES = {
  cash: "Efectivo",
  emerging: "Emergentes",
  core: "Núcleo",
  momentum: "Momentum",
};

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNewsDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function trSector(name) {
  return SECTOR_ES[name] || name;
}

function trIndex(name) {
  return INDEX_ES[name] || name;
}

function trRegime(regime) {
  const map = { bullish: "alcista", bearish: "bajista", neutral: "estable" };
  return map[regime?.toLowerCase()] || (regime || "").toUpperCase();
}

function trRec(rec) {
  if (!rec) return "—";
  const r = rec.toLowerCase();
  if (r.includes("strong buy")) return "COMPRA FUERTE";
  if (r.includes("buy")) return "COMPRAR";
  if (r.includes("strong sell")) return "VENTA FUERTE";
  if (r.includes("sell")) return "VENDER";
  if (r.includes("hold")) return "MANTENER";
  return rec.toUpperCase();
}

function trAgent(name) {
  const key = (name || "").replace("_agent", "");
  return AGENT_ES[key] || key.replace(/_/g, " ");
}

function trTrend(trend) {
  const map = { up: "alcista", down: "bajista", flat: "estable", rising: "subiendo", falling: "cayendo", stable: "estable" };
  return map[trend?.toLowerCase()] || trend || "—";
}

function trSensitivity(s) {
  const map = { high: "alta", medium: "media", low: "baja" };
  return map[s?.toLowerCase()] || s;
}

function trCapLabel(label) {
  const map = { large: "Gran capitalización", mid: "Mediana capitalización", small: "Pequeña capitalización" };
  return map[label?.toLowerCase()] || label;
}

function authHeaders(opts = {}) {
  const token = localStorage.getItem("nexbuy_token");
  const h = {};
  if (opts.json !== false) h["Content-Type"] = "application/json";
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

function isDeskPrincipal(p = window.__monarchPrincipal) {
  return !!(p && p.role === "desk");
}

function applyRoleMode(principal) {
  const desk = isDeskPrincipal(principal);
  document.body.classList.toggle("role-desk", desk);
  document.body.classList.toggle("role-client", !desk);
  const moneyLabel = document.getElementById("ceo-money-label");
  if (moneyLabel) moneyLabel.textContent = desk ? "Tu dinero" : "Cuenta Monarch";
  const accessPanel = document.getElementById("access-panel");
  if (accessPanel) accessPanel.classList.toggle("hidden", !desk);
  const clientBanner = document.getElementById("client-monitor-banner");
  if (clientBanner) clientBanner.classList.toggle("hidden", desk);
}

function setBootMsg(msg) {
  const el = document.getElementById("boot-splash-msg");
  if (el && msg) el.textContent = msg;
}

function hideBootSplash() {
  if (typeof window.__monarchKillSplash === "function") {
    window.__monarchKillSplash();
    return;
  }
  document.documentElement.classList.add("session-ok");
  const splash = document.getElementById("boot-splash");
  if (!splash) return;
  // Inline kill — works even if cached CSS lacks .boot-splash-done
  splash.style.cssText = "display:none!important;visibility:hidden!important;opacity:0!important;pointer-events:none!important;position:fixed!important;inset:0;z-index:-1;";
  splash.classList.add("boot-splash-done");
  splash.setAttribute("hidden", "true");
  splash.remove();
}

function applySessionUi(principal) {
  window.__monarchPrincipal = principal || null;
  const chip = document.getElementById("auth-chip");
  const logoutBtn = document.getElementById("btn-logout");
  const sessionBar = document.getElementById("session-bar");
  if (chip && principal) {
    const label = principal.role === "desk"
      ? "Mesa"
      : (principal.email || principal.org_name || "Cliente");
    chip.textContent = label;
    chip.classList.remove("hidden");
  } else if (chip) {
    chip.classList.add("hidden");
  }
  // Logout is a real <a href="/logout"> — always visible on the gated terminal
  if (logoutBtn) {
    logoutBtn.classList.remove("hidden");
    logoutBtn.textContent = "Cerrar sesión";
  }
  if (sessionBar) sessionBar.classList.add("session-bar-on");
  applyRoleMode(principal);
  hideBootSplash();
}

async function clearSessionAndGoLogin() {
  try {
    sessionStorage.setItem("monarch_logged_out", "1");
  } catch { /* private mode */ }
  localStorage.removeItem("nexbuy_token");
  localStorage.removeItem("monarch_auth");
  // Hard server logout — clears httponly cookies even if fetch/JS is broken
  location.replace("/logout");
}

async function logoutSession(ev) {
  // Prefer native <a href="/logout"> navigation; only intercept to clear localStorage first
  if (ev) {
    try { ev.preventDefault(); } catch { /* ignore */ }
  }
  try { toast("Cerrando sesión…"); } catch { /* ignore */ }
  await clearSessionAndGoLogin();
}

async function fetchWithTimeout(url, opts = {}, ms = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function ensureAuth() {
  setBootMsg("Validando sesión…");
  try {
    const token = localStorage.getItem("nexbuy_token");
    let me = null;
    if (token) {
      me = await fetchWithTimeout(`${API}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "same-origin",
      }, 12000);
    }
    // Cookie-only session (httponly) — recover without localStorage bounce loop
    if (!me || !me.ok) {
      if (token && me && me.status === 401) {
        localStorage.removeItem("nexbuy_token");
        localStorage.removeItem("monarch_auth");
      }
      me = await fetchWithTimeout(`${API}/auth/me`, { credentials: "same-origin" }, 12000);
    }
    if (!me.ok) {
      setBootMsg("Sesión expirada — redirigiendo…");
      await clearSessionAndGoLogin();
      return false;
    }
    const principal = await me.json();
    if (token && !localStorage.getItem("nexbuy_token")) {
      localStorage.setItem("nexbuy_token", token);
    }
    setBootMsg("Listo");
    applySessionUi(principal);
    return true;
  } catch (err) {
    // Cold start / timeout: still show the terminal if we have a local token
    if (!localStorage.getItem("nexbuy_token")) {
      location.replace("/login");
      return false;
    }
    setBootMsg("Servidor lento — abriendo igual…");
    let fallbackRole = "viewer";
    try {
      const meta = JSON.parse(localStorage.getItem("monarch_auth") || "{}");
      if (meta.type === "desk") fallbackRole = "desk";
    } catch { /* ignore */ }
    applySessionUi({
      role: fallbackRole,
      email: "sesión local",
      auth_type: "local_fallback",
    });
    return true;
  }
}

function formatApiDetail(detail) {
  if (!detail) return "Error";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return String(detail);
}

async function api(path, opts = {}) {
  const hasBody = opts.body !== undefined && opts.body !== null;
  const r = await fetch(path, {
    ...opts,
    credentials: "same-origin",
    headers: { ...authHeaders({ json: hasBody }), ...opts.headers },
  });
  if (r.status === 401) {
    await clearSessionAndGoLogin();
    throw new Error("Sesión expirada");
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(formatApiDetail(err.detail) || r.statusText);
  }
  return r.json();
}

async function apiWithRetry(path, opts = {}, retryOpts = {}) {
  const runner = window.MonarchUI?.apiRetry
    || (async (fn) => fn());
  return runner(() => api(path, opts), { label: path, ...retryOpts });
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    t.classList.remove("show");
    t.classList.add("hidden");
  }, ms);
}

let loadingCount = 0;

function showLoading(msg = "Procesando…") {
  loadingCount += 1;
  const el = $("#loading-overlay");
  if ($("#loading-msg")) $("#loading-msg").textContent = msg;
  el?.classList.remove("hidden");
}

function hideLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) $("#loading-overlay")?.classList.add("hidden");
}

async function withLoading(msg, fn) {
  showLoading(msg);
  try {
    return await fn();
  } finally {
    hideLoading();
  }
}

function syncBudgetFields(fromId, toId) {
  const from = $(fromId);
  const to = $(toId);
  if (from?.value && to) to.value = from.value;
}

function capitalFitHint(capital) {
  const c = parseFloat(capital) || 0;
  if (c <= 0) return "";
  if (c <= 100) {
    return `Capital micro ($${c}): solo compras con consenso unánime del comité (BUY corto+largo), cotización viva, cash ≥20% y tope ~35%/posición.`;
  }
  if (c <= 500) {
    return `Capital pequeño ($${c}): preferencia por acciones ≤ ~$25 que quepan en cada línea de asignación.`;
  }
  if (c <= 2000) {
    return `Capital medio ($${c}): se priorizan acciones asequibles respecto al tamaño de cada posición.`;
  }
  return `Capital estándar ($${c}): proporciones % normales; CFD solo si una acción no cabe.`;
}

function updateCapitalFitHints() {
  const capital = parseFloat($("#alloc-capital")?.value)
    || parseFloat($("#prop-budget")?.value)
    || parseFloat($("#disc-budget")?.value)
    || parseFloat($("#pf-capital")?.value)
    || 0;
  const hint = capitalFitHint(capital);
  const el = $("#capital-fit-hint");
  if (el) el.textContent = hint;
  const pf = $("#pf-capital-hint");
  if (pf && $("#pf-capital")?.value) pf.textContent = capitalFitHint($("#pf-capital").value);
}

function syncAllCapitalFields(sourceId) {
  const val = $(sourceId)?.value;
  if (!val) return;
  ["#alloc-capital", "#prop-budget", "#disc-budget", "#pf-capital"].forEach((id) => {
    if (id !== sourceId && $(id)) $(id).value = val;
  });
  updateCapitalFitHints();
}

function setupBudgetSync() {
  $("#disc-budget")?.addEventListener("change", () => {
    syncBudgetFields("#disc-budget", "#prop-budget");
    syncBudgetFields("#disc-budget", "#alloc-capital");
    updateCapitalFitHints();
  });
  $("#prop-budget")?.addEventListener("change", () => {
    syncBudgetFields("#prop-budget", "#disc-budget");
    syncBudgetFields("#prop-budget", "#alloc-capital");
    updateCapitalFitHints();
  });
  $("#alloc-capital")?.addEventListener("change", () => {
    syncBudgetFields("#alloc-capital", "#prop-budget");
    syncBudgetFields("#alloc-capital", "#disc-budget");
    updateCapitalFitHints();
  });
  $("#pf-capital")?.addEventListener("input", () => updateCapitalFitHints());
  $("#pf-capital")?.addEventListener("change", () => {
    syncAllCapitalFields("#pf-capital");
  });
  updateCapitalFitHints();
}

function setupMobileNav() {
  const markActive = (btn) => {
    $$(".mob-nav-btn").forEach((b) => b.classList.remove("active"));
    btn?.classList.add("active");
  };

  $$(".mob-nav-btn[data-scroll]").forEach((btn) => {
    btn.onclick = () => {
      const id = btn.dataset.scroll;
      if (id === "ceo-bar") clearMobileTechFocus();
      else clearMobileTechFocus(false);
      const el = id === "watchlist-matrix"
        ? document.querySelector("#watchlist-matrix-panel")
        : document.getElementById(id);
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
      markActive(btn);
    };
  });

  $("#mob-tech")?.addEventListener("click", () => {
    focusTechView({ force: true });
    markActive($("#mob-tech"));
  });

  $("#mob-analyze")?.addEventListener("click", () => {
    $("#global-ticker")?.focus();
    runAnalyze();
    markActive($("#mob-analyze"));
  });

  $$(".mob-nav-btn[data-tab]").forEach((btn) => {
    btn.onclick = () => {
      clearMobileTechFocus(false);
      const tab = btn.dataset.tab;
      const tabBtn = document.querySelector(`.tab[data-tab="${tab}"]`);
      tabBtn?.click();
      tabBtn?.closest(".panel")?.scrollIntoView({ behavior: "smooth" });
      markActive(btn);
    };
  });
}

function isMobileViewport() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function clearMobileTechFocus(exitFullscreen = true) {
  document.body.classList.remove("mobile-tech-focus");
  if (exitFullscreen) setTechChartFullscreen(false);
}

function focusTechView({ force = false } = {}) {
  if (!isMobileViewport() && !force) return;
  switchToTab("overview");
  document.body.classList.add("mobile-tech-focus");
  requestAnimationFrame(() => {
    const el = $("#tech-view") || document.querySelector(".col-center .panel.tabs");
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    resizeLwCharts();
  });
}

function resizeLwCharts() {
  Object.entries(lwCharts).forEach(([key, chart]) => {
    if (!chart) return;
    const el = key === "candle" ? $("#candle-chart")
      : key === "rsi" ? $("#rsi-chart")
        : $("#macd-chart");
    if (!el) return;
    const width = el.clientWidth || el.parentElement?.clientWidth || 0;
    const height = el.clientHeight || el.parentElement?.clientHeight || 0;
    if (width > 0 && height > 0) {
      chart.applyOptions({ width, height });
      chart.timeScale().fitContent();
    }
  });
}

function setTechChartFullscreen(on) {
  const box = $("#candle-chart-box");
  const btn = $("#btn-tech-expand");
  if (!box) return;
  box.classList.toggle("fullscreen", !!on);
  document.body.classList.toggle("tech-chart-fs", !!on);
  if (btn) btn.textContent = on ? "✕" : "⛶";
  requestAnimationFrame(() => resizeLwCharts());
}

function syncTechSummaryCollapse() {
  const summary = $("#tech-summary");
  const more = $("#btn-tech-summary-more");
  if (!summary || !more) return;
  summary.classList.remove("is-collapsed");
  more.classList.add("hidden");
  more.textContent = "Ver más";
  // Only clamp on mobile when text is long
  if (!isMobileViewport()) return;
  const long = (summary.textContent || "").length > 220;
  if (!long) return;
  summary.classList.add("is-collapsed");
  more.classList.remove("hidden");
}

function setupTechMobileControls() {
  $("#btn-tech-expand")?.addEventListener("click", () => {
    const box = $("#candle-chart-box");
    setTechChartFullscreen(!box?.classList.contains("fullscreen"));
  });
  $("#btn-tech-summary-more")?.addEventListener("click", () => {
    const summary = $("#tech-summary");
    const more = $("#btn-tech-summary-more");
    if (!summary || !more) return;
    const collapsed = summary.classList.toggle("is-collapsed");
    more.textContent = collapsed ? "Ver más" : "Ver menos";
  });
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!isMobileViewport()) clearMobileTechFocus();
      resizeLwCharts();
    }, 120);
  });
  window.addEventListener("orientationchange", () => {
    setTimeout(resizeLwCharts, 250);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#candle-chart-box")?.classList.contains("fullscreen")) {
      setTechChartFullscreen(false);
    }
  });
}

function ticker() { return ($("#global-ticker").value || "VRT").trim().toUpperCase(); }

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function fmtScore(v) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}`;
}

function recClass(rec) {
  if (!rec) return "";
  const r = rec.toLowerCase();
  if (r.includes("buy")) return "rec-buy";
  if (r.includes("sell")) return "rec-sell";
  return "rec-hold";
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function makeChart(id, config) {
  destroyChart(id);
  const el = document.getElementById(id);
  if (!el) return null;
  charts[id] = new Chart(el, config);
  return charts[id];
}

function renderIndices(indices) {
  $("#indices-row").innerHTML = indices.map((i) => {
    const cls = (i.change_pct || 0) >= 0 ? "up" : "down";
    const sym = i.symbol ? `<div class="sym">${escapeHtml(i.symbol)}</div>` : "";
    return `<div class="idx"><div class="name">${trIndex(i.name)}</div>${sym}<div class="price">${i.price ?? "—"}</div><div class="chg ${cls}">${fmtPct(i.change_pct)}</div></div>`;
  }).join("");
}

function renderHeatmap(sectors) {
  $("#sector-heatmap").innerHTML = sectors.map((s) =>
    `<div class="heat-cell ${s.regime}"><div>${trSector(s.sector)}</div><div>${s.change_pct != null ? fmtPct(s.change_pct) : "—"}</div></div>`
  ).join("");
}

function renderCeoBar(d) {
  const p = d.portfolio;
  lastDashboardPortfolio = p || null;
  $("#ceo-portfolio").textContent = p ? `$${p.total_value?.toFixed(0)}` : "—";
  const ret = p?.return_pct;
  const retEl = $("#ceo-return");
  retEl.textContent = ret != null ? fmtPct(ret) : "—";
  retEl.className = ret >= 0 ? "up" : "down";
  $("#ceo-alerts").textContent = (d.active_alerts || []).length;
  $("#ceo-watchlist-count").textContent = (d.watchlist || []).length;
  $("#ceo-updated").textContent = d.timestamp ? new Date(d.timestamp).toLocaleTimeString(LOCALE) : new Date().toLocaleTimeString(LOCALE);
  if (p && !alpacaBookCapital()) {
    const cap = Number(p.cash || p.total_value || p.initial_capital || 0);
    if (cap > 0 && cap !== 1000) syncCapitalInputsFromBroker(cap);
  }
}

function renderProviderHealth(health) {
  if (!health || !health.providers) {
    $("#provider-health").textContent = "Proveedores: —";
    return;
  }
  const p = health.providers;
  const parts = [
    `Polygon: ${p.polygon?.authenticated ? "OK" : p.polygon?.configured ? "Error" : "—"}`,
    `AV: ${p.alpha_vantage?.configured ? "OK" : "—"}`,
    `FRED: ${p.fred?.configured ? "OK" : "—"}`,
    `YF: ${p.yfinance?.enabled ? "OK" : "—"}`,
  ];
  $("#provider-health").textContent = `Proveedores: ${parts.join(" · ")} | Actualización automática cada ${REFRESH_MS / 1000}s`;
}

async function loadDailyBriefing() {
  const el = $("#daily-briefing");
  $("#btn-export-briefing").href = `${API}/reports/daily/latest/export`;
  try {
    const r = await api(`${API}/reports/daily/latest`);
    $("#briefing-date").textContent = r.date ? new Date(r.date).toLocaleDateString(LOCALE) : "";
    const mr = r.market_report || {};
    el.innerHTML = `
      <p><b>Resumen mercado:</b> ${mr.market_summary || "—"}</p>
      <p><b>Sectores fuertes:</b> ${(mr.strong_sectors || []).join(", ") || "—"}</p>
      <p><b>Sectores débiles:</b> ${(mr.weak_sectors || []).join(", ") || "—"}</p>
      <p><b>Top oportunidades:</b> ${(r.top_opportunities || []).join(", ") || "—"}</p>
      <p><b>Peores:</b> ${(r.worst_performers || []).join(", ") || "—"}</p>
      <p><b>Cambios watchlist:</b> ${(r.watchlist_changes || []).slice(0, 4).join("; ") || "Sin cambios"}</p>
      <p><b>Alertas:</b> ${(r.alerts || []).slice(0, 5).join("; ") || "Ninguna"}</p>`;
  } catch {
    el.innerHTML = `<p class="muted">Briefing diario no disponible aún. Ejecuta el scheduler o <code>python main.py report daily</code>.</p>`;
  }
}

function renderTradeRecommendations(r) {
  $("#trade-recs-date").textContent = r.generated_at
    ? new Date(r.generated_at).toLocaleString(LOCALE)
    : "";
  const modeEs = {
    crisis: "cuidado extremo",
    risk_off: "modo defensivo",
    risk_on: "modo ofensivo",
    neutral: "modo equilibrado",
  }[r.macro_mode] || r.macro_mode;
  const macroBits = [
    modeEs ? `Clima: ${modeEs}` : null,
    r.size_multiplier != null && r.size_multiplier !== 1
      ? `tamaño de compra ×${r.size_multiplier}`
      : null,
  ].filter(Boolean).join(" · ");
  $("#trade-recs-summary").textContent =
    [macroBits, r.summary].filter(Boolean).join(" — ");
  $("#trade-recs-disclaimer").textContent = r.disclaimer || "";

  const picks = r.picks || [];
  const grid = $("#trade-recs-grid");
  if (!picks.length) {
    const crisis = r.macro_mode === "crisis"
      ? " Risk Desk en crisis: sin nuevas compras."
      : "";
    grid.innerHTML = `<p class="muted">Sin setups de momentum hoy.${crisis} Pulsa <b>Gestionar capital</b> para que el escritorio busque penny stocks asequibles a tu portafolio.</p>`;
    return;
  }

  grid.innerHTML = picks.map((p) => `
    <div class="trade-rec-card">
      <div class="tr-head">
        <span class="tr-ticker">${p.ticker}</span>
        <span class="tr-action ${p.action === "vigilar" ? "watch" : ""}">${p.action === "vigilar" ? "Solo mirar" : "Idea de compra"}</span>
      </div>
      <div style="font-size:0.85rem;color:var(--muted);font-weight:300">${(p.company_name || "").slice(0, 40)}</div>
      <div class="tr-levels">
        <span>Ahora: <b>$${p.current_price ?? "—"}</b></span>
        <span>Meta: <b class="up">$${p.target_price ?? "—"}</b></span>
        <span>Salida si baja: <b class="down">$${p.stop_loss ?? "—"}</b></span>
        <span>Potencial: <b>${p.expected_return_pct != null ? "+" + p.expected_return_pct + "%" : "—"}</b></span>
      </div>
      <div class="tr-catalysts">${(p.catalysts || []).slice(0, 2).join(" · ") || p.rationale?.slice(0, 120) || ""}</div>
      <div class="tr-btns">
        <button class="btn tr-analyze-btn" data-t="${p.ticker}">Ver análisis</button>
        <button class="btn tr-add-btn" data-t="${p.ticker}">Seguir</button>
        <button class="btn primary tr-alpaca-btn" data-t="${p.ticker}"
          data-stop="${p.stop_loss ?? ""}" data-target="${p.target_price ?? ""}"
          data-price="${p.current_price ?? ""}">Comprar</button>
      </div>
    </div>`).join("");

  $$(".tr-analyze-btn").forEach((btn) => {
    btn.onclick = () => {
      $("#global-ticker").value = btn.dataset.t;
      runAnalyze();
    };
  });
  $$(".tr-add-btn").forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`${API}/watchlist`, { method: "POST", body: JSON.stringify({ ticker: btn.dataset.t }) });
        toast(`${btn.dataset.t} agregado a watchlist`);
        await loadDashboard();
      } catch (e) { toast("Watchlist: " + e.message); }
    };
  });
  $$(".tr-alpaca-btn").forEach((btn) => {
    btn.onclick = () => executeAlpacaPick(btn.dataset.t, {
      stop_loss: btn.dataset.stop ? parseFloat(btn.dataset.stop) : null,
      take_profit: btn.dataset.target ? parseFloat(btn.dataset.target) : null,
      price: btn.dataset.price ? parseFloat(btn.dataset.price) : null,
    });
  });
}

let lastMicroPlan = null;
let lastAlpacaStatus = null;
let lastDashboardPortfolio = null;
let capitalInputsSyncedFromBroker = false;

function alpacaBookCapital() {
  const acc = lastAlpacaStatus?.account;
  if (!acc || !lastAlpacaStatus?.connected) return null;
  for (const key of ["equity", "portfolio_value", "cash", "buying_power"]) {
    const n = Number(acc[key]);
    if (Number.isFinite(n) && n > 0) return Math.round(n * 100) / 100;
  }
  return null;
}

function syncCapitalInputsFromBroker(capital) {
  if (!capital || capital <= 0) return;
  const rounded = Math.round(capital * 100) / 100;
  ["#alloc-capital", "#prop-budget", "#disc-budget", "#pf-capital"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    // Only overwrite the HTML default (1000) or empty — keep intentional user edits
    const cur = parseFloat(el.value);
    if (!el.value || cur === 1000 || capitalInputsSyncedFromBroker) {
      el.value = String(rounded);
    }
  });
  capitalInputsSyncedFromBroker = true;
  if (typeof updateCapitalFitHints === "function") updateCapitalFitHints();
}

function renderAlpacaStatus(st) {
  lastAlpacaStatus = st;
  const el = $("#alpaca-status");
  if (!el) return;
  el.classList.remove("ok", "warn", "err");
  if (!st?.configured) {
    el.classList.add("warn");
    el.textContent = "Cuenta: falta conectar el broker (revisa las claves Alpaca).";
    return;
  }
  if (!st.connected) {
    el.classList.add("err");
    el.textContent = `Cuenta: no conectada — ${st.message || "error de conexión"}`;
    return;
  }
  const cash = st.account?.cash != null ? `Efectivo $${Number(st.account.cash).toFixed(2)}` : "";
  const eq = st.account?.equity != null ? `Total $${Number(st.account.equity).toFixed(2)}` : "";
  const mkt = st.market_open === true ? "Mercado abierto" : (st.market_open === false ? "Mercado cerrado" : "");
  const bits = [cash, eq, mkt].filter(Boolean).join(" · ");
  if (st.paper) {
    el.classList.add("ok");
    el.textContent = `Cuenta de práctica conectada${bits ? ` — ${bits}` : ""}`;
  } else {
    el.classList.add("err");
    el.textContent = `Cuenta real (dinero de verdad)${bits ? ` — ${bits}` : ""}`;
  }
  const book = alpacaBookCapital();
  if (book) syncCapitalInputsFromBroker(book);
}

async function loadAlpacaStatus() {
  try {
    const st = await api(`${API}/broker/status`);
    renderAlpacaStatus(st);
    if (st?.connected) await loadAlpacaBook();
  } catch {
    const el = $("#alpaca-status");
    if (el) {
      el.classList.add("warn");
      el.textContent = "Alpaca: estado no disponible";
    }
  }
}

async function loadRiskDesk() {
  const el = $("#risk-desk-status");
  if (!el) return;
  try {
    const r = await api(`${API}/risk/status`);
    const m = r.macro || {};
    const mode = m.mode || "neutral";
    el.classList.remove("ok", "warn", "err");
    if (mode === "crisis") el.classList.add("err");
    else if (mode === "risk_off") el.classList.add("warn");
    else if (mode === "risk_on") el.classList.add("ok");
    const modeEs = {
      crisis: "cuidado extremo",
      risk_off: "modo defensivo",
      risk_on: "modo ofensivo",
      neutral: "modo equilibrado",
    }[mode] || mode;
    const cash = r.portfolio?.cash_pct != null ? `${Number(r.portfolio.cash_pct).toFixed(0)}% en efectivo` : "";
    const auto = r.auto_execute_enabled ? "compras automáticas ON" : "compras automáticas OFF";
    const vix = m.vix != null ? `nerviosismo del mercado ${m.vix}` : "";
    el.textContent = [
      `Riesgo: ${modeEs}`,
      cash,
      auto,
      vix,
    ].filter(Boolean).join(" · ");
    el.title = (m.thesis || "") + "\n" + (r.notes || []).join("\n");
  } catch (e) {
    el.classList.add("warn");
    el.textContent = "Riesgo: no disponible ahora";
  }
}

async function loadOpsDesk() {
  const el = $("#ops-desk-status");
  if (!el) return;
  try {
    const [st, metrics] = await Promise.all([
      api(`${API}/ops/status`),
      api(`${API}/ops/risk-metrics`).catch(() => null),
    ]);
    el.classList.remove("ok", "warn", "err");
    const ks = st.kill_switch?.active;
    if (ks) el.classList.add("err");
    else el.classList.add("ok");
    const autonomy = st.firm_autonomy ? "la firma opera sola" : "modo manual";
    const kill = ks ? "parada de emergencia ACTIVADA" : "parada de emergencia apagada";
    const auto = st.auto_execute?.allowed ? "puede comprar" : "compras bloqueadas";
    const ap = st.autopilot_interval_minutes
      ? `revisa cada ${st.autopilot_interval_minutes} min`
      : "autopilot apagado";
    el.textContent = `Firma: ${autonomy} · ${kill} · ${auto} · ${ap}`;
    el.title = st.auto_execute?.policy?.promotion_note || st.auto_execute?.reason || "";
  } catch {
    el.classList.add("warn");
    el.textContent = "Firma: estado no disponible";
  }
}

async function runKillSwitch() {
  if (!confirm("Parada de emergencia: cancela órdenes y cierra TODAS las posiciones. ¿Continuar?")) return;
  if (!confirm("Confirmación final: esto vende todo en la cuenta real.")) return;
  await withLoading("Activando parada de emergencia…", async () => {
    try {
      const r = await api(`${API}/ops/kill-switch/on`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, flatten: true, reason: "panic flat UI" }),
      });
      toast(r.active ? `KILL ON · ${r.flat_result || ""}` : "Kill switch", 10000);
      await loadOpsDesk();
      await loadAlpacaBook();
    } catch (e) { toast("Kill switch: " + e.message, 8000); }
  });
}

async function runReconcile() {
  await withLoading("Reconciliando Alpaca ↔ DB…", async () => {
    try {
      const r = await api(`${API}/ops/reconcile?sync=true`, { method: "POST", body: "{}" });
      toast(r.message || `Diffs: ${(r.diffs || []).length}`, 8000);
      await loadOpsDesk();
      await loadDashboard();
    } catch (e) { toast("Reconcile: " + e.message); }
  });
}

async function runLifecycleScan() {
  await withLoading("Escaneando lifecycle…", async () => {
    try {
      const r = await api(`${API}/ops/lifecycle/scan`, { method: "POST", body: "{}" });
      const exits = (r.exits || []).join(", ") || "ninguna";
      toast(`Lifecycle: ${r.positions} pos · exits: ${exits}`, 8000);
      await loadOpsDesk();
      await loadAlpacaBook();
    } catch (e) { toast("Lifecycle: " + e.message); }
  });
}

async function runAutopilot() {
  const exec = confirm(
    "¿Autopilot completo?\nOK = también intenta auto-execute (respeta AUTO_EXECUTE_*).\nCancel = solo research/reconcile/lifecycle sin enviar órdenes."
  );
  await withLoading("Autopilot de la firma…", async () => {
    try {
      const r = await api(`${API}/ops/autopilot/run`, {
        method: "POST",
        body: JSON.stringify({
          execute_trades: exec ? null : false,
          session_label: "ceo_autopilot",
        }),
      });
      if (r.aborted) {
        toast("Autopilot abortado: " + r.aborted, 8000);
        return;
      }
      const picks = r.recommendations?.tickers?.join(", ") || "—";
      const exits = (r.lifecycle?.exits || []).join(", ") || "—";
      const ae = r.auto_execute || {};
      toast(
        `Autopilot OK · picks ${picks} · exits ${exits} · exec ${ae.skipped ? "skip:" + ae.reason : "sent " + (ae.submitted || 0)}`,
        12000
      );
      await loadOpsDesk();
      await loadRiskDesk();
      await loadDailyTradeRecommendations();
      await loadAlpacaBook();
    } catch (e) { toast("Autopilot: " + e.message, 8000); }
  });
}

let auditPageOffset = 0;
const AUDIT_PAGE_SIZE = 15;

async function loadAuditLog(offset = auditPageOffset) {
  const el = $("#ops-audit-log");
  if (!el) return;
  el.classList.remove("hidden");
  window.MonarchUI?.renderState(el, "loading", { title: "Cargando auditoría…" });
  try {
    const page = await apiWithRetry(`${API}/ops/audit?limit=${AUDIT_PAGE_SIZE}&offset=${offset}`);
    const rows = page.items || page || [];
    auditPageOffset = page.offset ?? offset;
    if (!rows.length) {
      window.MonarchUI?.renderState(el, "empty", {
        title: "Sin eventos de audit aún",
        detail: "Las acciones de la mesa aparecerán aquí.",
      });
      return;
    }
    const total = page.total ?? rows.length;
    el.innerHTML = rows.map((e) =>
      `<div>${new Date(e.created_at).toLocaleString(LOCALE)} · <b>${escapeHtml(e.action)}</b>` +
      `${e.symbol ? " " + escapeHtml(e.symbol) : ""} · ${e.success ? "OK" : "FAIL"} · ${escapeHtml((e.message || "").slice(0, 80))}</div>`
    ).join("") +
    `<div class="pager-bar"><span>${auditPageOffset + 1}–${auditPageOffset + rows.length} de ${total}</span>` +
    `<span><button type="button" class="btn" id="audit-prev" ${auditPageOffset <= 0 ? "disabled" : ""}>Anterior</button> ` +
    `<button type="button" class="btn" id="audit-next" ${page.has_more ? "" : "disabled"}>Siguiente</button></span></div>`;
    $("#audit-prev")?.addEventListener("click", () => loadAuditLog(Math.max(0, auditPageOffset - AUDIT_PAGE_SIZE)));
    $("#audit-next")?.addEventListener("click", () => loadAuditLog(auditPageOffset + AUDIT_PAGE_SIZE));
  } catch (e) {
    window.MonarchUI?.renderState(el, "error", {
      title: "No se pudo cargar el audit",
      detail: e.message,
      retryId: "audit",
    });
  }
}

async function loadAlpacaBook() {
  const el = $("#alpaca-book");
  if (!el || !lastAlpacaStatus?.connected) return;
  try {
    const [positions, openOrders, recent] = await Promise.all([
      api(`${API}/broker/positions`),
      api(`${API}/broker/orders?status=open&limit=20`),
      api(`${API}/broker/orders?status=all&limit=10`),
    ]);
    const posLines = (positions || []).length
      ? (positions || []).map((p) =>
          `<div><b>${p.symbol}</b> ${p.qty} @ $${Number(p.avg_entry_price || 0).toFixed(2)} · P/L $${Number(p.unrealized_pl || 0).toFixed(2)}</div>`
        ).join("")
      : "<div>Sin posiciones abiertas</div>";
    const orderLines = (openOrders || []).length
      ? (openOrders || []).map((o) =>
          `<div>OPEN <b>${o.symbol}</b> ${o.side} ${o.qty} · ${o.status}${o.id ? ` · #${String(o.id).slice(0, 8)}` : ""}</div>`
        ).join("")
      : "<div>Sin órdenes abiertas (pending)</div>";
    const recentLines = (recent || []).slice(0, 5).map((o) =>
      `<div>HIST <b>${o.symbol}</b> ${o.side} ${o.qty} · <b>${o.status}</b>${o.id ? ` · #${String(o.id).slice(0, 8)}` : ""}</div>`
    ).join("") || "<div>Sin historial reciente</div>";
    const mkt = lastAlpacaStatus.market_open === false
      ? "<div><b>Mercado US cerrado</b> — las market orders quedan pending hasta ~9:30 ET. Las posiciones aparecen al ejecutarse.</div>"
      : "";
    el.innerHTML = `${mkt}<div><b>Posiciones</b></div>${posLines}<div style="margin-top:4px"><b>Órdenes abiertas</b></div>${orderLines}<div style="margin-top:4px"><b>Últimas órdenes</b></div>${recentLines}`;
  } catch (e) {
    el.textContent = `No se pudo leer libro Alpaca: ${e.message}`;
  }
}

function confirmAlpacaLive() {
  if (!lastAlpacaStatus || lastAlpacaStatus.paper === false) {
    const closed = lastAlpacaStatus?.market_open === false
      ? "\n\nNOTA: el mercado US está CERRADO. La orden puede quedar pending hasta mañana 9:30 ET (no verás posición hasta que se ejecute)."
      : "";
    return window.confirm(
      "ATENCIÓN: vas a enviar órdenes LIVE a Alpaca con dinero REAL." + closed + "\n\n¿Confirmas la ejecución?"
    );
  }
  return true;
}

async function runAlpacaDoctor() {
  await withLoading("Diagnóstico Alpaca…", async () => {
    try {
      const d = await api(`${API}/broker/doctor`);
      const lines = (d.checks || []).join(" · ");
      const warn = (d.warnings || [])[0];
      toast(d.ok ? `Doctor OK · ${lines}` : `Doctor · ${warn || lines || "fallo"}`);
      await loadAlpacaStatus();
    } catch (e) { toast("Doctor: " + e.message); }
  });
}

async function cancelAllAlpacaOrders() {
  if (!window.confirm("¿Cancelar TODAS las órdenes abiertas en Alpaca?")) return;
  if (!confirmAlpacaLive()) return;
  const q = new URLSearchParams({
    confirm_cancel_all: "true",
    confirm_live: lastAlpacaStatus?.paper === false ? "true" : "false",
  });
  await withLoading("Cancelando órdenes Alpaca…", async () => {
    try {
      const r = await api(`${API}/broker/orders?${q}`, { method: "DELETE" });
      toast(`Canceladas: ${Array.isArray(r) ? r.length : 1}`);
      await loadAlpacaStatus();
    } catch (e) { toast("Cancelar: " + e.message); }
  });
}

async function executeAlpacaPick(ticker, opts = {}) {
  if (!confirmAlpacaLive()) return;
  const cash = Number(lastAlpacaStatus?.account?.cash ?? 0);
  const bp = Number(lastAlpacaStatus?.account?.buying_power ?? cash);
  if (cash <= 0 && bp <= 0) {
    toast("Alpaca tiene $0 de cash/buying power. Si acabas de fondear, espera a que el depósito esté disponible.", 9000);
    return;
  }
  const price = opts.price || 0;
  const capital = Math.min(currentPortfolioCapital() || 22, bp > 0 ? bp : cash);
  let shares = 1;
  if (price > 0) {
    shares = Math.max(1, Math.floor((capital * 0.35) / price));
    const cost = shares * price;
    if (cost > bp && bp > 0) {
      shares = Math.max(1, Math.floor(bp / price));
    }
    if (shares * price > bp + 0.01) {
      toast(`No alcanza buying power ($${bp.toFixed(2)}) para 1× ${ticker} @ $${price}`, 9000);
      return;
    }
  }
  const body = {
    ticker,
    shares,
    dry_run: false,
    // Usuario ya confirmó en el diálogo — siempre true en LIVE
    confirm_live: true,
    sync_portfolio_id: lastPortfolioId || undefined,
  };
  await withLoading(`Enviando ${shares}× ${ticker} a Alpaca…`, async () => {
    try {
      const r = await api(`${API}/broker/execute/pick`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      showAlpacaExecuteResult(r);
      await loadAlpacaStatus();
      await loadAlpacaBook();
    } catch (e) { toast("Alpaca: " + e.message, 8000); }
  });
}

function showAlpacaExecuteResult(r) {
  const submitted = r.submitted || [];
  const failed = r.failed || [];
  const warnings = r.warnings || [];
  if (warnings.length && !submitted.length && !failed.length) {
    toast(warnings[0], 9000);
    return;
  }
  if (failed.length) {
    const fail = failed.map((o) => `${o.symbol}: ${o.error || o.status}`).join(" · ");
    toast(`Alpaca rechazó · ${fail}`, 10000);
    return;
  }
  if (submitted.length) {
    const ok = submitted.map((o) => {
      const id = o.id ? ` #${String(o.id).slice(0, 8)}` : "";
      return `${o.symbol} ${o.status || "ok"}${id}`;
    }).join(", ");
    const extra = warnings.length ? ` · ${warnings[0]}` : "";
    toast(
      `Enviada a Alpaca · ${ok}${extra}. Revisa Orders/Activity (no solo el portafolio).`,
      10000
    );
    return;
  }
  toast("Alpaca: sin órdenes enviadas", 6000);
}

async function executeAlpacaMicroPlan(dryRun = false) {
  if (!lastMicroPlan?.lines?.length) {
    toast("Genera primero un plan con Gestionar capital");
    return;
  }
  if (!dryRun) {
    const cash = lastAlpacaStatus?.account?.cash;
    if (cash != null && Number(cash) <= 0) {
      toast("Alpaca tiene $0 de cash. Fondea en app.alpaca.markets antes de ejecutar.", 8000);
      return;
    }
  }
  if (!dryRun && !confirmAlpacaLive()) return;
  const body = {
    lines: lastMicroPlan.lines.map((l) => ({
      ticker: l.ticker,
      shares: l.shares,
      stop_loss: l.stop_loss ?? null,
      take_profit: l.take_profit ?? null,
    })),
    dry_run: dryRun,
    confirm_live: true,
    sync_portfolio_id: lastPortfolioId || undefined,
  };
  await withLoading(dryRun ? "Simulando órdenes Alpaca…" : "Ejecutando plan en Alpaca…", async () => {
    try {
      const r = await api(`${API}/broker/execute/micro-plan`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (dryRun) {
        toast(`Simulación (no enviada): ${(r.submitted || []).length} órdenes listas`, 6000);
      } else {
        showAlpacaExecuteResult(r);
      }
      if (!dryRun) {
        await loadAlpacaStatus();
        await loadAlpacaBook();
      }
    } catch (e) { toast("Alpaca plan: " + e.message, 8000); }
  });
}

function renderMicroPlan(plan) {
  const el = $("#micro-plan-panel");
  if (!el) return;
  lastMicroPlan = plan;
  if (!plan?.lines?.length) {
    el.classList.remove("hidden");
    el.innerHTML = `
      <div class="micro-plan-head">Plan de gestión · sin compras (falta consenso del comité)</div>
      <div class="micro-plan-cash">Efectivo reserva: $${plan?.cash_reserve_usd ?? "—"} · Desplegable: $${plan?.deployable_usd ?? "—"}</div>
      <p class="muted" style="font-size:11px;margin:8px 0">
        Solo se compra si <b>todos</b> los agentes votan BUY y las estrategias de corto y largo plazo coinciden en compra.
        ${(plan?.summary || "Mantén efectivo hasta que haya consenso.")}
      </p>
      ${(plan?.warnings || []).length ? `<p class="muted" style="font-size:10px">${plan.warnings.join(" · ")}</p>` : ""}
    `;
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="micro-plan-head">Plan de gestión · consenso comité BUY · capital $${plan.capital} · máx $${plan.max_share_price}/acc</div>
    <div class="micro-plan-cash">Efectivo reserva: $${plan.cash_reserve_usd} · Desplegable: $${plan.deployable_usd}</div>
    <table class="matrix-table compact">
      <thead><tr><th>Ticker</th><th>Precio</th><th>Acciones</th><th>$</th><th>%</th><th>Stop / Obj</th></tr></thead>
      <tbody>
        ${plan.lines.map((l) => `
          <tr>
            <td><b>${l.ticker}</b></td>
            <td>$${l.price}</td>
            <td>${l.shares}</td>
            <td>$${l.allocation_usd}</td>
            <td>${l.allocation_pct}%</td>
            <td>$${l.stop_loss ?? "—"} / $${l.take_profit ?? "—"}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    ${(plan.warnings || []).length ? `<p class="muted" style="font-size:10px">${plan.warnings.join(" · ")}</p>` : ""}
    <div class="micro-plan-actions">
      <button type="button" class="btn" id="btn-alpaca-dry">Simular Alpaca</button>
      <button type="button" class="btn primary" id="btn-alpaca-exec">Ejecutar en Alpaca</button>
    </div>
  `;
  $("#btn-alpaca-dry").onclick = () => executeAlpacaMicroPlan(true);
  $("#btn-alpaca-exec").onclick = () => executeAlpacaMicroPlan(false);
}

function currentPortfolioCapital() {
  // Clients never pull Alpaca — they only see the firm dashboard book
  if (isDeskPrincipal()) {
    const alpaca = alpacaBookCapital();
    if (alpaca) return alpaca;
  }

  // Firm portfolio from dashboard (mesa book)
  if (lastDashboardPortfolio) {
    for (const key of ["cash", "total_value", "initial_capital"]) {
      const n = Number(lastDashboardPortfolio[key]);
      if (Number.isFinite(n) && n > 0) return Math.round(n * 100) / 100;
    }
  }

  // 3) Inputs — ignore the HTML default of 1000 unless user synced/edited
  const inputs = ["#alloc-capital", "#prop-budget", "#disc-budget", "#pf-capital"]
    .map((id) => parseFloat($(id)?.value))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (inputs.length) {
    const v = inputs[0];
    // Treat untouched default 1000 as "unknown" when no broker/portfolio
    if (v === 1000 && !capitalInputsSyncedFromBroker && !lastDashboardPortfolio) {
      return null;
    }
    return v;
  }

  const ceo = $("#ceo-portfolio")?.textContent?.replace(/[^0-9.]/g, "");
  const n = parseFloat(ceo);
  return n > 0 ? n : null;
}

async function generateDailyTrades() {
  const capital = currentPortfolioCapital();
  await withLoading("Generando recomendaciones…", async () => {
    try {
      const body = { session: "pre_market", max_picks: capital && capital <= 100 ? 3 : 8 };
      if (capital) body.capital = capital;
      const r = await api(`${API}/recommendations/daily/generate`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      $("#micro-plan-panel")?.classList.add("hidden");
      renderTradeRecommendations(r);
      toast(`${(r.picks || []).length} recomendaciones listas`);
    } catch (e) { toast("Recomendaciones: " + e.message); }
  });
}

async function loadDailyTradeRecommendations() {
  try {
    const r = await api(`${API}/recommendations/daily/latest`);
    renderTradeRecommendations(r);
  } catch {
    $("#trade-recs-grid").innerHTML = `<p class="muted">Recomendaciones no disponibles. Pulsa "Generar ahora" o "Gestionar capital".</p>`;
  }
}

async function managePortfolioCapital() {
  // Prefer live Alpaca; if missing, ask API to resolve (Alpaca/server-side portfolio)
  let capital = currentPortfolioCapital();
  if (capital === 1000 && !capitalInputsSyncedFromBroker && !alpacaBookCapital()) {
    capital = null; // don't send the HTML default
  }
  const label = capital ? `$${capital}` : "Alpaca/portafolio";
  await withLoading(`Gestionando capital ${label}…`, async () => {
    try {
      const body = { persist_as_daily: true };
      if (capital && capital > 0) body.capital = capital;
      const plan = await api(`${API}/recommendations/manage-capital`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (plan.capital) syncCapitalInputsFromBroker(plan.capital);
      renderMicroPlan(plan);
      $("#trade-recs-summary").textContent = plan.summary || "";
      if (plan.picks?.length) {
        renderTradeRecommendations({
          picks: plan.picks,
          summary: plan.summary,
          generated_at: new Date().toISOString(),
          disclaimer: "Plan autónomo — solo tickers con consenso unánime del comité (BUY corto+largo). No es asesoría financiera.",
        });
        toast(`${plan.picks.length} posiciones con consenso del comité`, 5000);
      } else {
        toast("Sin consenso del comité: se mantiene efectivo (sin compras forzadas)", 7000);
      }
      toast(plan.lines?.length
        ? `Plan $${plan.capital}: ${plan.lines.map((l) => l.ticker).join(", ")}`
        : `Sin líneas (capital $${plan.capital}) — intenta de nuevo`);
    } catch (e) {
      toast("Gestión: " + e.message);
      if (String(e.message || "").includes("capital")) openPortfolioModal();
    }
  });
}

async function loadWatchlistMatrix() {
  try {
    const rows = await api(`${API}/dashboard/watchlist-matrix`);
    const body = $("#matrix-body");
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="8">Watchlist vacía</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => `
      <tr class="matrix-row" data-t="${r.ticker}">
        <td><b>${r.ticker}</b></td>
        <td>${r.price != null ? "$" + r.price : "—"}</td>
        <td class="${(r.change_pct || 0) >= 0 ? "up" : "down"}">${fmtPct(r.change_pct)}</td>
        <td class="${recClass(r.recommendation)}">${trRec(r.recommendation)}</td>
        <td>${r.confidence != null ? (r.confidence * 100).toFixed(0) + "%" : "—"}</td>
        <td>${fmtScore(r.news_score)}</td>
        <td>${fmtScore(r.technical_score)}</td>
        <td>${fmtScore(r.sentiment_score)}</td>
      </tr>`).join("");
    $$(".matrix-row").forEach((el) => el.onclick = () => {
      $("#global-ticker").value = el.dataset.t;
      runAnalyze();
    });
  } catch (e) {
    $("#matrix-body").innerHTML = `<tr><td colspan="8">Error: ${e.message}</td></tr>`;
  }
}

function renderPortfolioPies(p) {
  if (!p) { destroyChart("sector-chart"); destroyChart("cap-chart"); return; }
  const sectorLabels = Object.keys(p.sector_weights || {});
  const sectorData = Object.values(p.sector_weights || {});
  if (sectorLabels.length) {
    makeChart("sector-chart", {
      type: "doughnut",
      data: {
        labels: sectorLabels,
        datasets: [{ data: sectorData, backgroundColor: ["#c4a574","#3d9b6e","#d4a574","#d45d5d","#2d4a40","#9aaba3"] }],
      },
      options: { plugins: { legend: { position: "bottom", labels: { color: "#7d8fa3", font: { size: 10 } } } }, maintainAspectRatio: false },
    });
  }
  const capLabels = Object.keys(p.cap_exposure || {}).filter((k) => p.cap_exposure[k] > 0).map(trCapLabel);
  const capKeys = Object.keys(p.cap_exposure || {}).filter((k) => p.cap_exposure[k] > 0);
  const capData = capKeys.map((k) => p.cap_exposure[k]);
  if (capKeys.length) {
    makeChart("cap-chart", {
      type: "pie",
      data: {
        labels: capLabels,
        datasets: [{ data: capData, backgroundColor: ["#c4a574","#3d9b6e","#d4a574"] }],
      },
      options: { plugins: { legend: { position: "bottom", labels: { color: "#7d8fa3", font: { size: 10 } } } }, maintainAspectRatio: false },
    });
  }
}

function renderOpportunities(opps, risks) {
  const fmt = (items) => items.length
    ? items.map((o) => `<div class="opp-item" data-t="${o.ticker}"><b>${o.ticker}</b> ${trRec(o.recommendation)} ${(o.confidence * 100).toFixed(0)}%<br/><span>${o.reason?.slice(0, 80) || ""}</span></div>`).join("")
    : "—";
  $("#opportunities").innerHTML = `<h4>Oportunidades</h4>${fmt(opps)}`;
  $("#risks-panel").innerHTML = `<h4>Riesgos</h4>${fmt(risks)}`;
  $$(".opp-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
}

function renderMarketNews(items) {
  lastNewsItems = items || [];
  const el = $("#market-news");
  if (!lastNewsItems.length) {
    el.innerHTML = '<p class="muted">Sin noticias disponibles.</p>';
    return;
  }
  el.innerHTML = lastNewsItems.map((n, i) => {
    const excerpt = (n.summary || n.title || "").slice(0, 140);
    const thumb = n.thumbnail_url
      ? `<img class="news-card-thumb" src="${escapeHtml(n.thumbnail_url)}" alt="" loading="lazy" />`
      : `<div class="news-card-thumb placeholder">📰</div>`;
    return `
      <article class="news-card" data-news-idx="${i}" tabindex="0" role="button">
        ${thumb}
        <div class="news-card-body">
          <div class="news-card-meta">
            <span class="news-source">${escapeHtml(n.source)}</span>
            ${n.published_at ? `<span class="news-date">${escapeHtml(fmtNewsDate(n.published_at))}</span>` : ""}
          </div>
          <h4 class="news-card-title">${escapeHtml(n.title)}</h4>
          <p class="news-card-excerpt">${escapeHtml(excerpt)}${(n.summary || "").length > 140 ? "…" : ""}</p>
        </div>
      </article>`;
  }).join("");
  $$(".news-card").forEach((card) => {
    const open = () => openNewsModal(Number(card.dataset.newsIdx));
    card.onclick = open;
    card.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    };
  });
}

function openNewsModal(idx) {
  const n = lastNewsItems[idx];
  if (!n) return;
  const modal = $("#news-modal");
  const thumbWrap = $("#news-modal-thumb-wrap");
  const thumb = $("#news-modal-thumb");
  if (n.thumbnail_url) {
    thumb.src = n.thumbnail_url;
    thumb.alt = n.title;
    thumbWrap.classList.remove("hidden");
  } else {
    thumbWrap.classList.add("hidden");
  }
  $("#news-modal-meta").innerHTML = `
    <span class="news-source">${escapeHtml(n.source)}</span>
    ${n.published_at ? `<span class="news-date">${escapeHtml(fmtNewsDate(n.published_at))}</span>` : ""}`;
  $("#news-modal-title").textContent = n.title;
  $("#news-modal-summary").textContent = n.summary || n.title;
  const link = $("#news-modal-link");
  if (n.url) {
    link.href = n.url;
    link.classList.remove("hidden");
  } else {
    link.href = "#";
    link.classList.add("hidden");
  }
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closeNewsModal() {
  const modal = $("#news-modal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

function renderDashboard(d) {
  const regime = $("#market-regime");
  const scoreSign = d.market_regime_score >= 0 ? "+" : "";
  regime.textContent = `Mercado ${trRegime(d.market_regime)} (${scoreSign}${d.market_regime_score})`;
  regime.className = `regime ${d.market_regime}`;
  renderCeoBar(d);
  renderIndices(d.indices || []);
  renderHeatmap(d.sector_heatmap || []);
  $("#econ-calendar").innerHTML = (d.economic_calendar || []).map((e) => `<div><b>${e.date}</b> ${e.title}</div>`).join("") || "—";
  renderMarketNews(d.news_highlights || []);
  $("#m-msent").textContent = fmtScore(d.market_sentiment_score);
  $("#watchlist").innerHTML = (d.watchlist || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$(".wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
  {
    const alerts = d.active_alerts || [];
    const alertsEl = $("#alerts-panel");
    if (alertsEl) {
      if (!alerts.length) {
        window.MonarchUI?.renderState(alertsEl, "empty", {
          title: "Sin alertas",
          detail: "Cuando haya riesgos o señales, las verás aquí.",
        });
      } else {
        alertsEl.innerHTML = alerts.map((a) => `<div>${escapeHtml(typeof a === "string" ? a : (a.title || a))}</div>`).join("");
      }
    }
  }
  $("#recent-panel").innerHTML = (d.recently_analyzed || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$("#recent-panel .wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });

  const p = d.portfolio;
  const modeBadge = p?.mode === "demo"
    ? '<span class="demo-badge demo">Demo</span>'
    : p?.mode === "real"
      ? '<span class="demo-badge real">Real</span>'
      : "";
  $("#portfolio-panel").innerHTML = p ? `
    <div><b>${p.name || "Portafolio"}</b>${modeBadge}</div>
    <div>Capital inicial: $${p.initial_capital?.toFixed(2)}</div>
    <div>Valor: $${p.total_value?.toFixed(2)}</div>
    <div>Efectivo: $${(p.cash ?? 0).toFixed(2)}</div>
    <div>Rendimiento: ${fmtPct(p.return_pct)}</div>
    <div>Sharpe: ${p.sharpe?.toFixed(2) ?? "—"}</div>
    <div>Drawdown: ${p.max_drawdown?.toFixed(2) ?? "—"}%</div>
    <div>P&amp;L no realizado: $${p.unrealized_pnl?.toFixed(2)}</div>
    <div>Países: ${Object.entries(p.country_weights || {}).map(([k,v]) => `${k} ${v}%`).join(", ") || "—"}</div>
    ${isDeskPrincipal()
      ? `<button type="button" id="btn-sync-alpaca-pf" class="btn" style="margin-top:6px;width:100%;font-size:11px">Sincronizar desde Alpaca</button>
         <p class="muted" style="font-size:10px;margin-top:6px">Mesa: sincroniza el book Alpaca. Usa Postgres (<code>DATABASE_URL</code>) para no perder datos en redeploy.</p>`
      : `<p class="muted" style="font-size:10px;margin-top:6px">Portafolio de tu empresa — revisa capital, posiciones y rendimiento.</p>`
    }
  ` : (isDeskPrincipal()
    ? `<div class="ui-state"><p>Sin portafolio</p><button type="button" id="btn-create-default-pf" class="btn primary" style="margin-top:6px;width:100%">Crear / sincronizar book</button></div>`
    : `<div class="ui-state"><p>Sin portafolio de empresa</p><button type="button" id="btn-create-default-pf" class="btn primary" style="margin-top:6px;width:100%">Crear portafolio</button></div>`);
  renderPortfolioPies(p);
  lastPortfolioId = p?.portfolio_id || null;
  if (lastPortfolioId) {
    try { localStorage.setItem("nexbuy_portfolio_id", lastPortfolioId); } catch {}
  }
  const bootNote = d.provider_health?.portfolio_bootstrap;
  if (bootNote && isDeskPrincipal()) toast(bootNote, 9000);
  $("#btn-sync-alpaca-pf") && ($("#btn-sync-alpaca-pf").onclick = syncPortfolioFromAlpaca);
  $("#btn-create-default-pf") && ($("#btn-create-default-pf").onclick = async () => {
    try {
      showLoading("Creando portafolio…");
      if (isDeskPrincipal()) {
        try {
          await api(`${API}/portfolios/sync-alpaca`, { method: "POST" });
        } catch {
          await api(`${API}/portfolios/default`, { method: "POST", body: "{}" });
        }
      } else {
        await api(`${API}/portfolios/default`, {
          method: "POST",
          body: JSON.stringify({
            name: "Portafolio empresa",
            strategy: "growth_investing",
            initial_capital: 1000,
            mode: "demo",
          }),
        });
      }
      toast("Portafolio listo");
      await loadDashboard();
    } catch (e) {
      toast("Portafolio: " + e.message);
    } finally {
      hideLoading();
    }
  });
  if (p?.portfolio_id) {
    loadPortfolioHistory(p.portfolio_id);
    if (p.mode === "demo") {
      $("#demo-projections-wrap").classList.remove("hidden");
      loadDemoProjections(p.portfolio_id);
    } else {
      $("#demo-projections-wrap").classList.add("hidden");
      destroyChart("demo-projection-chart");
    }
  } else {
    destroyChart("portfolio-history-chart");
    $("#demo-projections-wrap").classList.add("hidden");
  }
  renderOpportunities(d.top_opportunities || [], d.top_risks || []);
  renderProviderHealth(d.provider_health);
}

async function loadPushStatus() {
  try {
    const s = await api(`${API}/alerts/push-status`);
    const badge = $("#push-status-badge");
    if (!badge) return;
    if (s.enabled) {
      const parts = [];
      if (s.telegram) parts.push("TG");
      if (s.whatsapp) parts.push("WA");
      if (s.webhook) parts.push("WH");
      badge.textContent = `push ${parts.join("+")}`;
      badge.className = "push-badge on";
      badge.title = s.whatsapp_provider
        ? `Push activo · WhatsApp=${s.whatsapp_provider}`
        : "Notificaciones push activas";
    } else {
      badge.textContent = "push off";
      badge.className = "push-badge off";
      badge.title = "Configura Telegram y/o WhatsApp (CallMeBot/Meta/Twilio) en el servidor";
    }
  } catch {
    /* ignore */
  }
}

async function testPushNotification() {
  toast("Enviando alerta de prueba…");
  try {
    const r = await api(`${API}/alerts/test-push`, { method: "POST" });
    toast(r.ok ? "Push de prueba enviado" : "Push no entregado — revisa configuración");
  } catch (e) { toast("Push: " + e.message); }
  try {
    const b = await api(`${API}/alerts/briefing/send?session_kind=manual`, { method: "POST" });
    if (b.ok) toast("Status de portafolio enviado (WA/TG)", 5000);
  } catch {
    /* briefing optional */
  }
}

function showDashboardError(err) {
  const box = $("#panel-error");
  if (!box) {
    toast("Panel: " + (err?.message || err));
    return;
  }
  box.classList.remove("hidden");
  const title = $("#panel-error-title");
  const detail = $("#panel-error-detail");
  if (title) title.textContent = "No se pudo cargar el terminal";
  if (detail) detail.textContent = err?.message || String(err);
}

function hideDashboardError() {
  $("#panel-error")?.classList.add("hidden");
}

async function loadDashboard() {
  hideDashboardError();
  try {
    const d = await apiWithRetry(`${API}/dashboard`, {}, { retries: 2, label: "dashboard" });
    hideBootSplash();
    renderDashboard(d);
    const jobs = [
      loadDailyBriefing(),
      loadDailyTradeRecommendations(),
      loadWatchlistMatrix(),
      loadPushStatus(),
    ];
    if (isDeskPrincipal()) {
      jobs.push(
        loadAlpacaStatus(),
        loadRiskDesk(),
        loadOpsDesk(),
        loadAccessRequests(),
        loadPasswordResets(),
        loadDeskCapitalRequests(),
      );
    } else {
      loadClientCapital();
    }
    await Promise.all(jobs);
  } catch (e) {
    hideBootSplash();
    showDashboardError(e);
    toast("Panel: " + e.message);
  }
}

function _accessStatusLabel(status) {
  if (status === "approved") return "autorizado";
  if (status === "rejected") return "rechazado";
  return "pendiente";
}

async function _accessAction(orgId, action, btn) {
  if (!orgId || !action) return;
  if (btn) {
    btn.disabled = true;
      btn.textContent = action === "reject" ? "Eliminando…" : action === "approve" ? "Autorizando…" : "Guardando…";
  }
  try {
    const path = action === "deposit-received"
      ? `${API}/auth/companies/${orgId}/deposit-received`
      : `${API}/auth/companies/${orgId}/${action}`;
    await api(path, { method: "POST", body: JSON.stringify({}) });
    toast(
      action === "reject" ? "Solicitud eliminada"
        : action === "approve" ? "Cliente autorizado"
          : "Depósito marcado como recibido"
    );
    await loadAccessRequests();
  } catch (e) {
    toast(e.message || "No se pudo completar la acción");
    if (btn) {
      btn.disabled = false;
      btn.textContent = action === "reject" ? "Rechazar"
        : action === "approve" ? "Autorizar"
          : "Marcar depósito recibido";
    }
  }
}

async function loadAccessRequests() {
  const box = document.getElementById("access-list");
  if (!box || !isDeskPrincipal()) return;
  try {
    const page = await api(`${API}/auth/companies?limit=50`);
    const list = Array.isArray(page?.items) ? page.items : [];
    if (!list.length) {
      box.innerHTML = `<p class="muted">Sin solicitudes todavía.</p>`;
      return;
    }
    // Pending first, then approved (rejected are deleted server-side)
    const rank = { pending: 0, approved: 1 };
    list.sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9));

    box.innerHTML = list.map((c) => {
      const status = c.status || (c.active ? "approved" : "pending");
      if (status === "rejected") return ""; // should already be purged
      const deposit = c.deposit_status || "none";
      const depLabel = deposit === "requested"
        ? ` · depósito $${Number(c.deposit_requested_usd || 0).toLocaleString()} solicitado`
        : deposit === "received"
          ? " · depósito recibido"
          : "";
      const actions = [];
      if (status === "pending") {
        actions.push(`<button type="button" class="btn primary" data-access-action="approve" data-org-id="${c.id}">Autorizar</button>`);
        actions.push(`<button type="button" class="btn danger" data-access-action="reject" data-org-id="${c.id}">Rechazar</button>`);
      } else if (status === "approved" && c.email) {
        actions.push(`<button type="button" class="btn" data-set-password="${c.email}">Nueva contraseña</button>`);
      }
      if (deposit === "requested") {
        actions.push(`<button type="button" class="btn" data-access-action="deposit-received" data-org-id="${c.id}">Marcar depósito recibido</button>`);
      }
      return `<div class="access-card" data-org="${c.id}">
        <div class="meta">
          <strong>${c.name || "Cliente"}</strong>
          <span class="pill ${status}">${_accessStatusLabel(status)}</span>
          <div class="muted" style="font-size:12px;margin-top:0.2rem">
            ${c.full_name || "—"} · ${c.email || "—"} ${depLabel}
          </div>
        </div>
        <div class="actions">${actions.join("")}</div>
      </div>`;
    }).join("");

    // Event delegation — survives re-renders and works on mobile taps
    if (!box.dataset.boundAccess) {
      box.dataset.boundAccess = "1";
      box.addEventListener("click", (ev) => {
        const setPw = ev.target?.closest?.("[data-set-password]");
        if (setPw && box.contains(setPw)) {
          ev.preventDefault();
          deskSetClientPassword(setPw.getAttribute("data-set-password"));
          return;
        }
        const btn = ev.target?.closest?.("[data-access-action]");
        if (!btn || !box.contains(btn)) return;
        ev.preventDefault();
        ev.stopPropagation();
        _accessAction(btn.getAttribute("data-org-id"), btn.getAttribute("data-access-action"), btn);
      });
    }
  } catch (e) {
    box.innerHTML = `<p class="muted">No se pudieron cargar accesos: ${e.message}</p>`;
  }
}

async function loadPasswordResets() {
  const box = document.getElementById("reset-list");
  if (!box || !isDeskPrincipal()) return;
  try {
    const data = await api(`${API}/auth/password/resets`);
    const items = data.items || [];
    if (!items.length) {
      box.innerHTML = `<p class="muted">Sin recuperaciones activas.</p>`;
      return;
    }
    box.innerHTML = items.map((r) => `
      <div class="access-card">
        <div class="meta">
          <strong>${r.email || "—"}</strong>
          <span class="pill pending">código activo</span>
          <div class="muted" style="font-size:12px;margin-top:0.25rem">
            Código: <b style="color:#e8dcc8;letter-spacing:0.12em">${r.code || "—"}</b>
            · vence ${r.expires_at ? new Date(r.expires_at).toLocaleString() : "—"}
          </div>
        </div>
      </div>
    `).join("");
  } catch (e) {
    box.innerHTML = `<p class="muted">No se pudieron cargar recuperaciones: ${e.message}</p>`;
  }
}

async function deskSetClientPassword(email) {
  if (!email) return;
  const pw = window.prompt(`Nueva contraseña para ${email} (mín. 8 caracteres):`);
  if (!pw) return;
  if (pw.length < 8) {
    toast("La contraseña debe tener al menos 8 caracteres");
    return;
  }
  try {
    await api(`${API}/auth/password/desk-set`, {
      method: "POST",
      body: JSON.stringify({ email, new_password: pw }),
    });
    toast("Contraseña actualizada. Entrégasela al cliente.");
    loadPasswordResets();
  } catch (e) {
    toast(e.message);
  }
}

function _copyText(text, label) {
  const v = (text || "").trim();
  if (!v) return;
  const done = () => toast(`${label || "Dato"} copiado`);
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(v).then(done).catch(() => {
      window.prompt("Copia este valor:", v);
    });
  } else {
    window.prompt("Copia este valor:", v);
  }
}

function renderFundingBox(funding) {
  const box = document.getElementById("funding-box");
  if (!box || !funding) return;
  const bank = funding.bank || {};
  const steps = (funding.steps || []).map((s) => `<li>${s}</li>`).join("");
  const wire = funding.wire_details
    ? `<pre style="white-space:pre-wrap;margin:0.5rem 0 0;font-size:11px">${funding.wire_details}</pre>`
    : "";
  const rows = [
    ["Beneficiario", bank.beneficiary],
    ["Banco", bank.bank_name],
    ["Routing ABA", bank.routing_number],
    ["Número de cuenta", bank.account_number],
    ["Tipo", bank.account_type],
    ["SWIFT (si aplica)", bank.swift],
    ["Monto", funding.amount_usd != null ? `$${Number(funding.amount_usd).toLocaleString()} USD` : ""],
    ["Referencia / memo", funding.memo_reference],
  ].filter(([, v]) => v);

  const bankRows = rows.map(([label, value]) => `
    <div class="fund-row">
      <div>
        <div class="fund-label">${label}</div>
        <div class="fund-value memo">${value}</div>
      </div>
      <button type="button" class="btn" data-copy="${String(value).replace(/"/g, "&quot;")}" data-copy-label="${label}">Copiar</button>
    </div>
  `).join("");

  const missing = !funding.configured
    ? `<p class="muted" style="margin:0.5rem 0 0">La mesa aún debe publicar los datos bancarios de depósito. Si no los ves, escríbeles — no uses Alpaca login.</p>`
    : "";

  // Never show Alpaca login links (blocked server-side too)
  const extraLink = funding.funding_url
    ? `<p style="margin:0.45rem 0 0">Página adicional (opcional): <a href="${funding.funding_url}" target="_blank" rel="noopener">${funding.funding_url}</a></p>`
    : "";

  box.innerHTML = `
    <strong>Dónde depositar — ${funding.account_name || "Monarch Capital"}</strong>
    <p style="margin:0.35rem 0 0;color:#fde68a">${funding.headline || "Solo transferencia bancaria. Sin registro ni login en Alpaca."}</p>
    <ol>${steps}</ol>
    <div class="fund-grid">${bankRows || "<p class='muted'>Sin datos bancarios configurados todavía.</p>"}</div>
    ${funding.instructions ? `<p style="margin:0.55rem 0 0;white-space:pre-wrap">${funding.instructions}</p>` : ""}
    ${wire}
    ${extraLink}
    ${missing}
    <div style="margin-top:0.75rem">
      <button type="button" class="btn primary" id="btn-deposit-confirm">Ya deposité — avisar a la mesa</button>
    </div>
  `;
  box.classList.remove("hidden");
  box.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.onclick = () => _copyText(btn.getAttribute("data-copy"), btn.getAttribute("data-copy-label"));
  });
  const confirmBtn = document.getElementById("btn-deposit-confirm");
  if (confirmBtn && window.__pendingDepositId) {
    confirmBtn.onclick = () => confirmClientDeposit(window.__pendingDepositId);
  }
}

function _capitalStatusLabel(kind, status) {
  const map = {
    deposit: {
      requested: "depósito: pendiente de envío",
      client_confirmed: "depósito: cliente confirma envío",
      received: "depósito: recibido en Alpaca",
      rejected: "depósito: rechazado",
    },
    withdrawal: {
      requested: "retiro: pendiente de aprobación",
      approved: "retiro: aprobado (en proceso)",
      paid: "retiro: pagado",
      rejected: "retiro: rechazado",
    },
  };
  return (map[kind] && map[kind][status]) || status;
}

async function loadClientCapital() {
  const list = document.getElementById("client-capital-list");
  const depMsg = document.getElementById("deposit-status-msg");
  const wMsg = document.getElementById("withdraw-status-msg");
  try {
    const data = await api(`${API}/auth/capital/mine`);
    const items = data.items || [];
    if (list) {
      if (!items.length) {
        list.innerHTML = `<p class="muted">Sin movimientos todavía.</p>`;
      } else {
        list.innerHTML = items.map((r) => `
          <div class="access-card">
            <div class="meta">
              <strong>${r.kind === "withdrawal" ? "Retiro" : "Depósito"} $${Number(r.amount_usd || 0).toLocaleString()}</strong>
              <span class="pill ${r.status === "requested" || r.status === "client_confirmed" ? "pending" : r.status === "rejected" ? "rejected" : "approved"}">
                ${_capitalStatusLabel(r.kind, r.status)}
              </span>
              <div class="muted" style="font-size:12px;margin-top:0.2rem">${r.note || "—"} · ${r.created_at ? new Date(r.created_at).toLocaleString() : ""}</div>
            </div>
            <div class="actions">
              ${r.kind === "deposit" && r.status === "requested"
                ? `<button type="button" class="btn primary" data-confirm-deposit="${r.id}">Ya deposité</button>`
                : ""}
            </div>
          </div>
        `).join("");
        list.querySelectorAll("[data-confirm-deposit]").forEach((btn) => {
          btn.onclick = () => confirmClientDeposit(btn.getAttribute("data-confirm-deposit"));
        });
      }
    }
    const openDep = items.find((r) => r.kind === "deposit" && (r.status === "requested" || r.status === "client_confirmed"));
    if (openDep) {
      window.__pendingDepositId = openDep.id;
      if (depMsg) {
        depMsg.textContent = openDep.status === "client_confirmed"
          ? `Aviso enviado. Monto $${Number(openDep.amount_usd).toLocaleString()} — la mesa confirmará en Alpaca.`
          : `Depósito pendiente $${Number(openDep.amount_usd).toLocaleString()}. Usa el enlace de fondeo y luego «Ya deposité».`;
      }
      if (openDep.status === "requested") {
        try {
          const fund = await api(`${API}/auth/capital/funding`);
          renderFundingBox(fund.funding);
          const confirmBtn = document.getElementById("btn-deposit-confirm");
          if (confirmBtn) confirmBtn.onclick = () => confirmClientDeposit(openDep.id);
        } catch { /* ignore */ }
      }
    } else if (depMsg) {
      depMsg.textContent = "Indica el monto y pulsa Depositar para recibir el enlace a la cuenta Alpaca de Monarch.";
    }
    const openW = items.find((r) => r.kind === "withdrawal" && (r.status === "requested" || r.status === "approved"));
    if (wMsg) {
      wMsg.textContent = openW
        ? `Retiro $${Number(openW.amount_usd).toLocaleString()} — ${_capitalStatusLabel("withdrawal", openW.status)}.`
        : "Los retiros los revisa y aprueba la mesa Monarch.";
    }
  } catch (e) {
    if (list) list.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

async function submitDepositRequest() {
  const amount = parseFloat(document.getElementById("deposit-amount")?.value || "");
  const note = document.getElementById("deposit-note")?.value || "";
  if (!(amount > 0)) {
    toast("Indica un monto válido");
    return;
  }
  try {
    const r = await api(`${API}/auth/capital/deposit`, {
      method: "POST",
      body: JSON.stringify({ amount_usd: amount, note }),
    });
    window.__pendingDepositId = r.request?.id;
    if (r.funding) renderFundingBox(r.funding);
    const depMsg = document.getElementById("deposit-status-msg");
    if (depMsg) depMsg.textContent = r.message || "Usa el enlace para depositar en Alpaca.";
    toast("Enlace de depósito listo — fondea la cuenta Alpaca de Monarch");
    loadClientCapital();
  } catch (e) {
    toast(e.message);
  }
}

async function confirmClientDeposit(requestId) {
  if (!requestId) return;
  try {
    const r = await api(`${API}/auth/capital/deposit/${requestId}/confirm`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    toast(r.message || "Aviso enviado a la mesa");
    loadClientCapital();
  } catch (e) {
    toast(e.message);
  }
}

async function submitWithdrawRequest() {
  const amount = parseFloat(document.getElementById("withdraw-amount")?.value || "");
  const note = document.getElementById("withdraw-note")?.value || "";
  if (!(amount > 0)) {
    toast("Indica un monto de retiro válido");
    return;
  }
  try {
    const r = await api(`${API}/auth/capital/withdraw`, {
      method: "POST",
      body: JSON.stringify({ amount_usd: amount, note }),
    });
    toast(r.message || "Retiro solicitado — pendiente de aprobación");
    document.getElementById("withdraw-amount").value = "";
    loadClientCapital();
  } catch (e) {
    toast(e.message);
  }
}

async function loadDeskCapitalRequests() {
  const box = document.getElementById("capital-desk-list");
  if (!box || !isDeskPrincipal()) return;
  try {
    const data = await api(`${API}/auth/capital/requests`);
    const items = data.items || [];
    if (!items.length) {
      box.innerHTML = `<p class="muted">Sin movimientos de capital.</p>`;
      return;
    }
    box.innerHTML = items.map((r) => {
      const actions = [];
      if (r.kind === "deposit" && (r.status === "requested" || r.status === "client_confirmed")) {
        actions.push(`<button type="button" class="btn primary" data-cap-action="received" data-cap-id="${r.id}">Marcar recibido en Alpaca</button>`);
        actions.push(`<button type="button" class="btn" data-cap-action="reject" data-cap-id="${r.id}">Rechazar</button>`);
      }
      if (r.kind === "withdrawal" && r.status === "requested") {
        actions.push(`<button type="button" class="btn primary" data-cap-action="approve" data-cap-id="${r.id}">Aprobar retiro</button>`);
        actions.push(`<button type="button" class="btn danger" data-cap-action="reject" data-cap-id="${r.id}">Rechazar</button>`);
      }
      if (r.kind === "withdrawal" && r.status === "approved") {
        actions.push(`<button type="button" class="btn primary" data-cap-action="paid" data-cap-id="${r.id}">Marcar pagado</button>`);
      }
      return `<div class="access-card">
        <div class="meta">
          <strong>${r.kind === "withdrawal" ? "Retiro" : "Depósito"} $${Number(r.amount_usd || 0).toLocaleString()}</strong>
          <span class="pill ${r.status === "rejected" ? "rejected" : r.status === "requested" || r.status === "client_confirmed" ? "pending" : "approved"}">
            ${_capitalStatusLabel(r.kind, r.status)}
          </span>
          <div class="muted" style="font-size:12px;margin-top:0.2rem">
            ${r.email || "—"} · ${r.note || "—"} · ${r.created_at ? new Date(r.created_at).toLocaleString() : ""}
          </div>
        </div>
        <div class="actions">${actions.join("")}</div>
      </div>`;
    }).join("");
    if (!box.dataset.boundCapital) {
      box.dataset.boundCapital = "1";
      box.addEventListener("click", async (ev) => {
        const btn = ev.target?.closest?.("[data-cap-action]");
        if (!btn || !box.contains(btn)) return;
        const id = btn.getAttribute("data-cap-id");
        const action = btn.getAttribute("data-cap-action");
        btn.disabled = true;
        try {
          await api(`${API}/auth/capital/${id}/${action}`, {
            method: "POST",
            body: JSON.stringify({}),
          });
          toast(action === "approve" ? "Retiro aprobado"
            : action === "paid" ? "Retiro marcado como pagado"
              : action === "received" ? "Depósito confirmado en Alpaca"
                : "Solicitud rechazada");
          loadDeskCapitalRequests();
        } catch (e) {
          toast(e.message);
          btn.disabled = false;
        }
      });
    }
  } catch (e) {
    box.innerHTML = `<p class="muted">${e.message}</p>`;
  }
}

async function loadPriceChart(t) {
  await loadTechnicalChart(t);
}

function destroyLwChart(key) {
  if (lwCharts[key]) {
    lwCharts[key].remove();
    lwCharts[key] = null;
  }
}

function destroyAllLwCharts() {
  Object.keys(lwCharts).forEach(destroyLwChart);
}

const BIAS_ES = { bullish: "Alcista", bearish: "Bajista", neutral: "Neutral" };

function isIntradayTf(tf) {
  return tf && !["1D", "1W"].includes(tf);
}

function lwTime(dateStr, tf) {
  if (!dateStr) return null;
  if (!isIntradayTf(tf)) return String(dateStr).slice(0, 10);
  const s = String(dateStr).trim();
  const m = s.match(/^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  if (!m) return s.slice(0, 10);
  if (!m[2]) return m[1];
  const d = new Date(`${m[1]}T${m[2]}:${m[3]}:00`);
  return Math.floor(d.getTime() / 1000);
}

function syncChartTimeframe(tf) {
  activeGapTf = tf;
  const sel = $("#tech-chart-tf");
  if (sel && sel.value !== tf) sel.value = tf;
}

function renderTechnicalKpis(snap) {
  if (!snap) return;
  const rsiEl = $("#tk-rsi");
  rsiEl.textContent = snap.rsi != null ? snap.rsi.toFixed(1) : "—";
  rsiEl.className = snap.rsi > 70 ? "bearish" : snap.rsi < 30 ? "bullish" : "";
  $("#tk-macd").textContent = snap.macd != null && snap.macd_signal != null
    ? (snap.macd > snap.macd_signal ? "Alcista" : "Bajista")
    : "—";
  const biasEl = $("#tk-bias");
  biasEl.textContent = BIAS_ES[snap.bias] || snap.bias || "—";
  biasEl.className = snap.bias === "bullish" ? "bullish" : snap.bias === "bearish" ? "bearish" : "";
  $("#tk-support").textContent = snap.support != null ? `$${snap.support}` : "—";
  $("#tk-resistance").textContent = snap.resistance != null ? `$${snap.resistance}` : "—";
  $("#tk-levels").textContent = snap.stop_loss && snap.take_profit_1
    ? `$${snap.stop_loss} / $${snap.take_profit_1}`
    : "—";
}

function renderTechPlaybook(data, techAgentReport) {
  const el = $("#tech-playbook");
  if (!el) return;
  const pb = data?.playbook || techAgentReport?.raw_data?.playbook;
  const confluence = data?.confluence || techAgentReport?.raw_data?.confluence;
  const structure = data?.structure || techAgentReport?.raw_data?.structure;
  const historical = data?.historical_setups || techAgentReport?.raw_data?.historical_setups;
  const mkt = pb?.market_opinion || data?.market_opinion || techAgentReport?.raw_data?.market_opinion;
  if (!pb || !pb.strategy_es) {
    el.className = "tech-playbook hidden";
    el.innerHTML = "";
    return;
  }
  const opinion = pb.opinion || "neutral";
  el.className = `tech-playbook ${opinion}`;
  const checklist = (pb.checklist || []).slice(0, 8).map((c) => `<li>${c}</li>`).join("");
  const invalid = (pb.invalidation || []).slice(0, 3).map((c) => `<li>${c}</li>`).join("");
  const hist = pb.historical_note
    || (historical?.best
      ? `Edge histórico: ${historical.best.label_es} · ${historical.best.hit_rate}% acierto (${historical.best.samples} señales)`
      : "");
  const confLine = confluence
    ? `${confluence.label_es || "—"} · acuerdo ${confluence.agreement_pct ?? "—"}%`
    : "—";
  const structLine = structure?.label_es || data?.snapshot?.structure_label || "—";
  let mktHtml = "";
  if (mkt?.available) {
    const ch = mkt.channels || {};
    const chBits = ["news", "social", "retail", "analyst"]
      .map((k) => {
        const sc = ch[k]?.score;
        if (sc == null) return null;
        const names = { news: "Noticias", social: "Social", retail: "Minorista", analyst: "Analistas" };
        return `${names[k]} ${Number(sc) >= 0 ? "+" : ""}${Number(sc).toFixed(0)}`;
      })
      .filter(Boolean)
      .join(" · ");
    const align = pb.tech_market_alignment;
    mktHtml = `
      <div class="pb-market">
        <div class="pb-market-head">Opinión de mercado: <b>${mkt.label_es || "—"}</b>
          <span class="muted">(${mkt.aggregated_score != null ? (mkt.aggregated_score >= 0 ? "+" : "") + Number(mkt.aggregated_score).toFixed(1) : "—"})</span>
        </div>
        ${chBits ? `<div class="pb-market-ch">${chBits}</div>` : ""}
        ${mkt.summary ? `<p class="pb-market-sum">${mkt.summary}</p>` : ""}
        ${align ? `<p class="pb-market-align">Técnico vs mercado: <b>${align.status_es || align.status}</b> — ${align.note || ""}</p>` : ""}
        ${(mkt.top_factors || []).slice(0, 2).map((f) => `<div class="pb-market-factor">• ${f}</div>`).join("")}
      </div>`;
  }
  el.innerHTML = `
    <h4>Playbook técnico</h4>
    <div class="pb-meta">
      <span>Estrategia: <b>${pb.strategy_es}</b></span>
      <span>Opinión: <b>${pb.opinion_es || "—"}</b></span>
      <span>Estructura: <b>${structLine}</b></span>
      <span>Confluencia: <b>${confLine}</b></span>
    </div>
    <p class="pb-thesis">${pb.thesis || ""}</p>
    ${mktHtml}
    ${checklist ? `<ul>${checklist}</ul>` : ""}
    ${invalid ? `<p class="muted" style="margin:0.25rem 0 0;font-size:11px">Invalidación</p><ul>${invalid}</ul>` : ""}
    ${hist ? `<p class="pb-hist">${hist}</p>` : ""}
    <p class="muted" style="margin:0.35rem 0 0;font-size:10px">${pb.framework || ""}</p>
  `;
}

function renderGapHighlights(candleSeries, gaps, chartTf) {
  if (!gaps?.length) return;
  const tf = chartTf || activeGapTf;
  const markers = [];
  gaps.forEach((g) => {
    const time = lwTime(g.date, tf);
    if (!time) return;
    const isOpen = !g.filled;
    const color = isOpen
      ? (g.gap_type === "gap_up" ? "#f59e0b" : "#a855f7")
      : "rgba(100,116,139,0.6)";
    if (isOpen) {
      candleSeries.createPriceLine({
        price: g.gap_top,
        color: "rgba(251,191,36,0.85)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `Gap ${g.gap_size_pct}%`,
      });
      candleSeries.createPriceLine({
        price: g.gap_bottom,
        color: "rgba(251,191,36,0.85)",
        lineWidth: 1,
        lineStyle: 2,
        title: `Fill → $${g.fill_target}`,
      });
    }
    markers.push({
      time,
      position: g.gap_type === "gap_up" ? "belowBar" : "aboveBar",
      color,
      shape: g.gap_type === "gap_up" ? "arrowUp" : "arrowDown",
      text: isOpen ? `Gap ${g.gap_size_pct}%` : "✓",
    });
  });
  if (markers.length) candleSeries.setMarkers(markers);
}

let lastGapData = null;
let activeGapTf = "1D";

function renderGapsPanel(data) {
  lastGapData = data;
  const gapsByTf = data?.gaps_by_timeframe || {};
  const tfs = Object.keys(gapsByTf);
  const tabsEl = $("#gap-tf-tabs");
  const listEl = $("#gap-list");

  if (!tfs.length) {
    tabsEl.innerHTML = "";
    listEl.innerHTML = `<p class="muted" style="font-size:11px;margin:0">Sin gaps detectados en los horarios analizados.</p>`;
    return;
  }

  if (!tfs.includes(activeGapTf)) activeGapTf = tfs[0];

  tabsEl.innerHTML = tfs.map((tf) => {
    const open = (gapsByTf[tf] || []).filter((g) => !g.filled).length;
    return `<button type="button" class="gap-tf-tab ${tf === activeGapTf ? "active" : ""}" data-tf="${tf}">${tf}${open ? ` (${open})` : ""}</button>`;
  }).join("");

  $$(".gap-tf-tab").forEach((btn) => {
    btn.onclick = () => {
      syncChartTimeframe(btn.dataset.tf);
      renderGapsPanel(lastGapData);
      const t = ticker();
      if (t) loadTechnicalChart(t);
    };
  });

  const gaps = gapsByTf[activeGapTf] || [];
  if (!gaps.length) {
    listEl.innerHTML = `<p class="muted" style="font-size:11px;margin:0">Sin gaps en ${activeGapTf}.</p>`;
    return;
  }

  listEl.innerHTML = gaps.map((g) => `
    <div class="gap-item ${g.filled ? "filled" : "unfilled"}">
      <span class="gap-dir ${g.gap_type === "gap_up" ? "up" : "down"}">${g.gap_type === "gap_up" ? "↑ ALC" : "↓ BAJ"}</span>
      <span class="gap-zone">${g.date?.slice(0, 10) || g.date} · $${g.gap_bottom} – $${g.gap_top} · fill → <b>$${g.fill_target}</b> (${g.gap_size_pct}%)</span>
      <span class="gap-status ${g.filled ? "closed" : "open"}">${g.filled ? "Cubierto" : "Abierto"}</span>
    </div>`).join("");
}

function renderTechDataStatus(data) {
  const el = $("#tech-data-status");
  if (!el) return;
  const status = data?.market_status || "unavailable";
  const asOf = data?.as_of;
  const staleDays = data?.stale_days;
  el.hidden = false;
  el.className = "tech-data-status " + status;
  if (status === "live" && asOf) {
    el.textContent = `Mercado al día — última vela ${asOf}.`;
    return;
  }
  if (status === "delisted") {
    el.textContent = asOf
      ? `Sin cotización actual (deslistado/suspendido). Último dato: ${asOf}${staleDays != null ? ` · ${staleDays} días` : ""}. No hay serie completa a día de hoy.`
      : "Sin cotización actual (deslistado/suspendido). No hay datos de mercado a día de hoy.";
    return;
  }
  if (status === "stale") {
    el.textContent = asOf
      ? `Datos desactualizados — última vela ${asOf}${staleDays != null ? ` · hace ${staleDays} días` : ""}.`
      : "Datos de mercado desactualizados.";
    return;
  }
  el.textContent = asOf
    ? `Sin datos suficientes a día de hoy. Última sesión conocida: ${asOf}.`
    : "Sin datos de mercado a día de hoy para este ticker.";
}

async function loadTechnicalChart(t, techAgentReport) {
  const period = $("#tech-period")?.value || "6mo";
  const chartTf = $("#tech-chart-tf")?.value || activeGapTf || "1D";
  syncChartTimeframe(chartTf);
  const intraday = isIntradayTf(chartTf);
  try {
    const data = await api(`${API}/market/${t}/technical?period=${period}&timeframe=${encodeURIComponent(chartTf)}`);
    const pts = data.points || [];
    renderTechDataStatus(data);
    if (!pts.length) {
      destroyAllLwCharts();
      $("#tech-summary").textContent = data.summary || "Sin datos técnicos.";
      syncTechSummaryCollapse();
      renderTechPlaybook(data, techAgentReport);
      if (data.gaps_by_timeframe) renderGapsPanel(data);
      return;
    }

    renderTechnicalKpis(data.snapshot);
    renderTechPlaybook(data, techAgentReport);

    let summary = data.summary || "";
    if (techAgentReport?.summary) {
      summary = techAgentReport.summary + (summary ? `\n\n${summary}` : "");
    }
    $("#tech-summary").textContent = summary;
    syncTechSummaryCollapse();

    const chartOpts = {
      layout: { background: { color: "transparent" }, textColor: "#7d8fa3" },
      grid: { vertLines: { color: "#1e2a38" }, horzLines: { color: "#1e2a38" } },
      rightPriceScale: { borderColor: "#1e2a38" },
      timeScale: { borderColor: "#1e2a38", timeVisible: intraday, secondsVisible: false },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      handleScroll: { vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true },
    };

    const mapTime = (p) => lwTime(p.date, chartTf);

    destroyLwChart("candle");
    const candleEl = $("#candle-chart");
    const candleH = candleEl.clientHeight || candleEl.parentElement?.clientHeight || (isMobileViewport() ? 300 : 260);
    lwCharts.candle = LightweightCharts.createChart(candleEl, {
      ...chartOpts,
      width: candleEl.clientWidth || undefined,
      height: candleH,
    });
    const candleSeries = lwCharts.candle.addCandlestickSeries({
      upColor: "#22c55e", downColor: "#ef4444",
      borderUpColor: "#22c55e", borderDownColor: "#ef4444",
      wickUpColor: "#22c55e", wickDownColor: "#ef4444",
    });
    candleSeries.setData(pts.map((p) => ({
      time: mapTime(p), open: p.open, high: p.high, low: p.low, close: p.close,
    })));

    const sma20Data = pts.filter((p) => p.sma20 != null).map((p) => ({ time: mapTime(p), value: p.sma20 }));
    const sma50Data = pts.filter((p) => p.sma50 != null).map((p) => ({ time: mapTime(p), value: p.sma50 }));
    if (sma20Data.length) {
      const s20 = lwCharts.candle.addLineSeries({ color: "#f59e0b", lineWidth: 1, title: "SMA20" });
      s20.setData(sma20Data);
    }
    if (sma50Data.length) {
      const s50 = lwCharts.candle.addLineSeries({ color: "#8b5cf6", lineWidth: 1, title: "SMA50" });
      s50.setData(sma50Data);
    }

    if (data.snapshot?.support) {
      candleSeries.createPriceLine({ price: data.snapshot.support, color: "#22c55e", lineWidth: 1, lineStyle: 2, title: "Soporte" });
    }
    if (data.snapshot?.resistance) {
      candleSeries.createPriceLine({ price: data.snapshot.resistance, color: "#ef4444", lineWidth: 1, lineStyle: 2, title: "Resistencia" });
    }

    renderGapHighlights(candleSeries, data.gaps || [], chartTf);
    renderGapsPanel(data);

    const volData = pts.filter((p) => p.volume != null).map((p) => ({
      time: mapTime(p), value: p.volume,
      color: p.close >= p.open ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)",
    }));
    if (volData.length) {
      const volSeries = lwCharts.candle.addHistogramSeries({
        priceFormat: { type: "volume" }, priceScaleId: "vol",
      });
      lwCharts.candle.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volSeries.setData(volData);
    }
    lwCharts.candle.timeScale().fitContent();

    destroyLwChart("rsi");
    const rsiEl = $("#rsi-chart");
    const rsiH = rsiEl.clientHeight || rsiEl.parentElement?.clientHeight || 140;
    lwCharts.rsi = LightweightCharts.createChart(rsiEl, {
      ...chartOpts,
      width: rsiEl.clientWidth || undefined,
      height: rsiH,
    });
    const rsiSeries = lwCharts.rsi.addLineSeries({ color: "#c4a574", lineWidth: 2, title: "RSI" });
    rsiSeries.setData(pts.filter((p) => p.rsi != null).map((p) => ({ time: mapTime(p), value: p.rsi })));
    rsiSeries.createPriceLine({ price: 70, color: "rgba(239,68,68,0.6)", lineWidth: 1, lineStyle: 2 });
    rsiSeries.createPriceLine({ price: 30, color: "rgba(34,197,94,0.6)", lineWidth: 1, lineStyle: 2 });
    lwCharts.rsi.timeScale().fitContent();

    destroyLwChart("macd");
    const macdEl = $("#macd-chart");
    const macdH = macdEl.clientHeight || macdEl.parentElement?.clientHeight || 140;
    lwCharts.macd = LightweightCharts.createChart(macdEl, {
      ...chartOpts,
      width: macdEl.clientWidth || undefined,
      height: macdH,
    });
    const macdLine = lwCharts.macd.addLineSeries({ color: "#06b6d4", lineWidth: 1, title: "MACD" });
    macdLine.setData(pts.filter((p) => p.macd != null).map((p) => ({ time: mapTime(p), value: p.macd })));
    const sigLine = lwCharts.macd.addLineSeries({ color: "#f59e0b", lineWidth: 1, title: "Señal" });
    sigLine.setData(pts.filter((p) => p.macd_signal != null).map((p) => ({ time: mapTime(p), value: p.macd_signal })));
    const histSeries = lwCharts.macd.addHistogramSeries({ title: "Hist" });
    histSeries.setData(pts.filter((p) => p.macd_hist != null).map((p) => ({
      time: mapTime(p), value: p.macd_hist,
      color: p.macd_hist >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)",
    })));
    lwCharts.macd.timeScale().fitContent();
    requestAnimationFrame(resizeLwCharts);
  } catch (e) {
    destroyAllLwCharts();
    const statusEl = $("#tech-data-status");
    if (statusEl) {
      statusEl.hidden = false;
      statusEl.className = "tech-data-status delisted";
      statusEl.textContent = "No se pudo cargar el gráfico técnico.";
    }
    $("#tech-summary").textContent = "Error cargando gráfico técnico: " + e.message;
  }
}

async function loadSentimentTrend(t) {
  try {
    const hist = await api(`${API}/sentiment/${t}/history?limit=60`);
    if (!hist.length) { destroyChart("sentiment-trend-chart"); return; }
    makeChart("sentiment-trend-chart", {
      type: "line",
      data: {
        labels: hist.map((h) => new Date(h.timestamp).toLocaleDateString(LOCALE)),
        datasets: [{
          label: "Sentimiento",
          data: hist.map((h) => h.aggregated_score),
          borderColor: "#8b5cf6",
          backgroundColor: "rgba(139,92,246,0.15)",
          fill: true,
          tension: 0.3,
          pointRadius: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#7d8fa3", maxTicksLimit: 6 }, grid: { color: "#1e2a38" } },
          y: { min: -100, max: 100, ticks: { color: "#7d8fa3" }, grid: { color: "#1e2a38" } },
        },
      },
    });
  } catch { destroyChart("sentiment-trend-chart"); }
}

async function loadPortfolioHistory(portfolioId) {
  try {
    const hist = await api(`${API}/portfolios/${portfolioId}/history`);
    if (!hist.length) { destroyChart("portfolio-history-chart"); return; }
    makeChart("portfolio-history-chart", {
      type: "line",
      data: {
        labels: hist.map((h) => new Date(h.timestamp).toLocaleDateString(LOCALE)),
        datasets: [{
          label: "Valor del portafolio",
          data: hist.map((h) => h.total_value),
          borderColor: "#22c55e",
          backgroundColor: "rgba(34,197,94,0.12)",
          fill: true,
          tension: 0.2,
          pointRadius: hist.length > 30 ? 0 : 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#7d8fa3", maxTicksLimit: 5 }, grid: { color: "#1e2a38" } },
          y: { ticks: { color: "#7d8fa3" }, grid: { color: "#1e2a38" } },
        },
      },
    });
  } catch { destroyChart("portfolio-history-chart"); }
}

function renderScenarios(thesis) {
  const cases = [
    ["Alcista", thesis.bull_case, "bull"],
    ["Base", thesis.base_case, "base"],
    ["Bajista", thesis.bear_case, "bear"],
  ];
  $("#scenarios-row").innerHTML = cases.map(([label, c, cls]) => c ? `
    <div class="scenario-card ${cls}">
      <div class="sc-label">${label} (${((c.probability || 0) * 100).toFixed(0)}%)</div>
      <div class="sc-target">${c.price_target ? "$" + c.price_target.toFixed(2) : "—"}</div>
      <div class="sc-thesis">${(c.thesis || "").slice(0, 120)}</div>
    </div>` : "").join("");
}

function renderTechCorrelations(thesis) {
  const tech = (thesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
  const notes = tech?.raw_data?.cross_agent_correlations || [];
  const ul = $("#tech-correlations");
  ul.innerHTML = notes.length
    ? notes.map((n) => `<li>${n}</li>`).join("")
    : `<li class="muted">Sin correlaciones cruzadas — pulsa Analizar para generar contexto técnico.</li>`;
}

function renderCorrelations(corr) {
  $("#correlations-out").innerHTML = `
    <p class="prose">${corr.summary}</p>
    <h4 class="subhead">Correlaciones con Benchmark</h4>
    <table class="matrix-table compact">
      <thead><tr><th>ETF</th><th>ρ</th><th>Relación</th></tr></thead>
      <tbody>${(corr.benchmark_correlations || []).map((p) =>
        `<tr><td>${p.ticker}</td><td>${p.correlation?.toFixed(2) ?? "—"}</td><td>${p.interpretation}</td></tr>`
      ).join("")}</tbody>
    </table>
    <h4 class="subhead">Sensibilidades Macro</h4>
    <ul class="corr-list">${(corr.macro_sensitivities || []).map((m) =>
      `<li><b>${m.factor}</b> [${trSensitivity(m.sensitivity)}] — ${m.scenario}: ${m.impact_if_shock}</li>`
    ).join("")}</ul>
    <h4 class="subhead">Dependencias de la Empresa</h4>
    <ul class="corr-list">${(corr.company_dependencies || []).map((d) =>
      `<li><b>${d.ticker}</b> ${d.relationship}${d.correlation != null ? ` (ρ=${d.correlation.toFixed(2)})` : ""}: ${d.why_it_matters}</li>`
    ).join("")}</ul>`;
}

async function runAnalyze() {
  const t = ticker();
  await withLoading(`Analizando ${t}…`, async () => {
    try {
      const body = { ticker: t };
      if (lastPortfolioId) body.portfolio_id = lastPortfolioId;
      const [thesis, sent, graph, corr] = await Promise.all([
      api(`${API}/analyze`, { method: "POST", body: JSON.stringify(body) }),
      api(`${API}/sentiment/${t}/engine`),
      api(`${API}/graph/${t}`),
      api(`${API}/correlations/${t}`),
    ]);
    lastThesis = thesis;
    $("#m-rec").textContent = trRec(thesis.recommendation);
    $("#m-rec").className = recClass(thesis.recommendation);
    $("#m-conf").textContent = thesis.confidence ? `${(thesis.confidence * 100).toFixed(0)}%` : "—";
    $("#m-target").textContent = thesis.price_target ? `$${thesis.price_target.toFixed(2)}` : "—";
    const techReport = (thesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
    renderScenarios(thesis);
    renderTechCorrelations(thesis);
    await loadTechnicalChart(t, techReport);
    focusTechView();
    $$(".mob-nav-btn").forEach((b) => b.classList.remove("active"));
    $("#mob-tech")?.classList.add("active");

    const news = (thesis.agent_reports || []).find((r) => r.agent_name === "news_agent");
    const mem = (thesis.agent_reports || []).find((r) => r.agent_name === "investment_memory");
    $("#agents-grid").innerHTML = (thesis.agent_reports || []).map((r) => {
      const c = r.score >= 0 ? "pos" : "neg";
      return `<div class="agent-chip"><span>${trAgent(r.agent_name)}</span><span class="${c}">${fmtScore(r.score)}</span></div>`;
    }).join("");

    let txt = `${thesis.ticker} ${trRec(thesis.recommendation)} @ ${(thesis.confidence * 100).toFixed(0)}%\n\n${thesis.executive_summary}\n\n${thesis.investment_thesis}\n`;
    if (mem?.summary) txt += `\nMEMORIA: ${mem.summary}`;
    if (news?.raw_data) {
      txt += `\n2 años: ${news.raw_data.two_year_summary || ""}\n3 meses: ${news.raw_data.three_month_summary || ""}\nIMPACTO: ${news.raw_data.investment_impact || ""}`;
    }
    const dep = (thesis.agent_reports || []).find((r) => r.agent_name === "market_dependency_agent");
    if (dep) txt += `\n\nCORRELACIONES:\n${dep.summary}`;
    const tech = (thesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
    if (tech?.raw_data?.cross_agent_correlations?.length) {
      txt += `\n\nCONTEXTO TÉCNICO:\n` + tech.raw_data.cross_agent_correlations.join("\n");
    }
    if (tech?.raw_data?.playbook) {
      const pb = tech.raw_data.playbook;
      txt += `\n\nPLAYBOOK TÉCNICO: ${pb.strategy_es || ""} (${pb.opinion_es || ""})\n${pb.thesis || ""}`;
      if (pb.checklist?.length) txt += "\n- " + pb.checklist.slice(0, 5).join("\n- ");
      if (pb.historical_note) txt += `\n${pb.historical_note}`;
    }
    if (["sell", "strong_sell"].includes(String(thesis.recommendation || "").toLowerCase())) {
      txt += "\n\n⚠ Comité en SELL — si hay posición abierta, Lifecycle puede cerrarla.";
    }
    $("#analysis-out").textContent = txt;
    renderSentiment(sent);
    await loadSentimentTrend(t);
    renderGraph(graph);
    renderCorrelations(corr);
    await loadWatchlistMatrix();
    toast(`${t} listo`);
    } catch (e) { toast("Análisis: " + e.message); }
  });
}

function renderSentiment(s) {
  const channels = [
    ["Institucional", s.institutional], ["Minorista", s.retail], ["Social", s.social],
    ["Noticias", s.news], ["Analistas", s.analyst],
  ];
  $("#sentiment-out").innerHTML = `
    <p class="prose">${s.summary}</p>
    <div class="sent-grid">${channels.map(([label, ch]) => `
      <div class="sent-card"><h4>${label}</h4>
        <div class="score" style="color:${ch.score >= 0 ? "var(--green)" : ch.score < 0 ? "var(--red)" : "inherit"}">${fmtScore(ch.score)}</div>
        <div>Conf. ${(ch.confidence * 100).toFixed(0)}% · ${trTrend(ch.trend)} · n=${ch.sample_size}</div>
        <div style="font-size:10px;color:var(--muted)">${(ch.top_factors || []).slice(0, 2).join("; ")}</div>
      </div>`).join("")}
    </div><p style="margin-top:8px;font-size:11px;color:var(--muted)">Fuentes: ${s.sources_used?.join(", ")} | Fallidas: ${s.sources_failed?.join(", ") || "ninguna"}</p>`;
}

function renderGraph(g) {
  $("#graph-summary").textContent = g.summary + "\n\nBeneficiarios: " + (g.beneficiaries || []).join(", ") + "\nEn riesgo: " + (g.at_risk || []).join(", ");
  const nodes = new vis.DataSet((g.nodes || []).map((n) => ({
    id: n.id, label: n.label?.slice(0, 20) || n.id,
    color: n.type === "company" ? "#c4a574" : n.type === "geopolitical" ? "#d45d5d" : n.type === "commodity" ? "#d4a574" : "#9aaba3",
  })));
  const edges = new vis.DataSet((g.edges || []).map((e) => ({
    from: e.source, to: e.target, title: e.relation,
    color: { color: e.impact === "positive" ? "#22c55e" : e.impact === "negative" ? "#ef4444" : "#64748b" },
  })));
  if ($("#graph-network")._net) $("#graph-network")._net.destroy();
  $("#graph-network")._net = new vis.Network($("#graph-network"), { nodes, edges }, {
    physics: { stabilization: true }, interaction: { hover: true },
  });
}

async function simulateShock() {
  const node = $("#shock-node").value;
  toast(`Simulando shock: ${node}…`);
  try {
    const r = await api(`${API}/graph/shock/${node}`);
    $("#shock-out").textContent =
      `${r.summary}\n\nBENEFICIARIOS:\n${(r.beneficiaries || []).join("\n")}\n\nEN RIESGO:\n${(r.at_risk || []).join("\n")}\n\nRUTAS:\n${(r.transmission_paths || []).join("\n")}`;
    toast("Shock simulado");
  } catch (e) { toast("Shock: " + e.message); }
}

function renderProposalVisual(p) {
  const allocs = p.allocations || [];
  if (!allocs.length) {
    destroyChart("proposal-chart");
    $("#proposal-table").innerHTML = "";
    return;
  }
  makeChart("proposal-chart", {
    type: "doughnut",
    data: {
      labels: allocs.map((a) => a.ticker),
      datasets: [{
        data: allocs.map((a) => a.allocation_usd),
        backgroundColor: ["#c4a574","#3d9b6e","#d4a574","#d45d5d","#2d4a40","#6b8f7a","#9aaba3"],
      }],
    },
    options: { plugins: { legend: { position: "right", labels: { color: "#7d8fa3", font: { size: 10 } } } }, maintainAspectRatio: false },
  });
  $("#proposal-table").innerHTML = `
    <table class="matrix-table compact">
      <thead><tr><th>#</th><th>Ticker</th><th>Inst.</th><th>$</th><th>%</th><th>Margen</th></tr></thead>
      <tbody>${allocs.map((a) => `
        <tr>
          <td>${a.purchase_order}</td>
          <td><b>${a.ticker}</b></td>
          <td>${a.instrument}</td>
          <td>$${a.allocation_usd}</td>
          <td>${a.allocation_pct}%</td>
          <td>${a.margin_required ? "$" + a.margin_required : "—"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

async function buildAllocationAdvise() {
  const capital = parseFloat($("#alloc-capital").value);
  if (!capital || capital <= 0) {
    toast("Espera a que cargue Alpaca o indica el capital real");
    return;
  }
  const style = $("#alloc-style").value;
  await withLoading("Analizando mercado y watchlist…", async () => {
    try {
      const plan = await api(`${API}/allocation/advise`, {
        method: "POST",
        body: JSON.stringify({ capital, strategy_style: style }),
      });
      lastAllocationPlan = plan;
      renderAllocationPlan(plan);
      toast("Asignación generada");
    } catch (e) { toast("Asignación: " + e.message); }
  });
}

function renderAllocationPlan(plan) {
  $("#alloc-market-view").textContent = plan.market_view || "";
  $("#alloc-summary").textContent = plan.summary || "";
  const maxPct = Math.max(...(plan.buckets || []).map((b) => b.allocation_pct), 1);
  $("#alloc-buckets").innerHTML = (plan.buckets || []).map((b) => `
    <div class="alloc-bucket">
      <div class="alloc-bucket-head">
        <b>${b.label}</b>
        <span class="alloc-bucket-pct">${b.allocation_pct}% · $${b.allocation_usd.toLocaleString()}</span>
      </div>
      <div class="alloc-bar"><div class="alloc-bar-fill" style="width:${(b.allocation_pct / maxPct * 100).toFixed(0)}%"></div></div>
      ${b.tickers?.length ? `<div class="alloc-bucket-tickers">${b.tickers.join(" · ")}</div>` : ""}
      <div class="alloc-bucket-desc">${b.description || ""}</div>
    </div>`).join("");
  const items = plan.items || [];
  if (items.length) {
    $("#alloc-table-wrap").style.display = "";
    $("#btn-alloc-to-proposal").style.display = "";
    $("#alloc-body").innerHTML = items.map((i) => `
      <tr>
        <td><b>${i.ticker}</b></td>
        <td>${BUCKET_ES[i.bucket] || i.bucket}</td>
        <td>${i.allocation_pct}%</td>
        <td>$${i.allocation_usd}</td>
        <td>${trRec(i.recommendation)}</td>
        <td style="max-width:140px;font-size:10px;color:var(--muted)">${escapeHtml(i.rationale?.slice(0, 90) || "")}${i.is_emerging ? " 🌱" : ""}</td>
      </tr>`).join("");
  }
  if (plan.warnings?.length) {
    $("#alloc-market-view").textContent += " ⚠ " + plan.warnings.join("; ");
  }
}

function useAllocationInProposal() {
  if (!lastAllocationPlan) return;
  $("#prop-budget").value = lastAllocationPlan.capital;
  const styleMap = { emerging_focused: "aggressive", balanced: "balanced", defensive: "conservative" };
  $("#prop-risk").value = styleMap[lastAllocationPlan.strategy_style] || "balanced";
  toast("Capital y perfil copiados — pulsa Crear Propuesta");
  buildProposal();
}

async function buildProposal() {
  const tickers = $("#prop-tickers").value.trim();
  const body = {
    budget: parseFloat($("#prop-budget").value) || 50,
    tickers: tickers ? tickers.split(",").map((s) => s.trim().toUpperCase()) : null,
    use_watchlist: !tickers,
    risk_profile: $("#prop-risk").value,
    instrument_mode: "auto",
    prefer_affordable: true,
  };
  await withLoading("Creando propuesta…", async () => {
    try {
      const p = await api(`${API}/proposal`, { method: "POST", body: JSON.stringify(body) });
      lastProposal = p;
      renderProposalVisual(p);
      let out = p.summary + "\n\n" + (p.executive_report?.narrative || "") + "\n\n";
      if (p.executive_report) {
        out += "POR QUÉ SE SELECCIONARON:\n" + p.executive_report.why_selected.join("\n") + "\n\n";
        out += "POR QUÉ NO:\n" + (p.executive_report.why_excluded || []).join("\n") + "\n\n";
        out += "RIESGOS:\n" + p.executive_report.key_risks.join("\n") + "\n\n";
        out += "A MONITOREAR:\n" + p.executive_report.events_to_monitor.join("\n") + "\n\n";
        if (p.executive_report.correlation_notes?.length) {
          out += "CORRELACIONES:\n" + p.executive_report.correlation_notes.join("\n") + "\n\n";
        }
      }
      out += (p.allocations || []).map((a) =>
        `#${a.purchase_order} ${a.ticker} [${a.instrument}] $${a.allocation_usd} — ${a.rationale}`
      ).join("\n");
      $("#proposal-out").textContent = out;
      toast("Propuesta lista");
    } catch (e) { toast("Propuesta: " + e.message); }
  });
}

async function applyProposal() {
  if (!lastProposal) { toast("Genera una propuesta primero"); return; }
  let pid = lastPortfolioId;
  if (!pid) {
    try {
      toast("Crea un portafolio primero (Real o Demo)");
      openPortfolioModal();
      return;
    } catch (e) { toast("Portafolio: " + e.message); return; }
  }
  toast("Aplicando propuesta…");
  try {
    await api(`${API}/proposal/apply`, {
      method: "POST",
      body: JSON.stringify({ portfolio_id: pid, proposal: lastProposal }),
    });
    $("#proposal-apply-msg").textContent = `Propuesta aplicada al portafolio ${pid}`;
    toast("Propuesta aplicada");
    await loadDashboard();
  } catch (e) { toast("Aplicar: " + e.message); }
}

async function loadDemoProjections(portfolioId) {
  try {
    const r = await api(`${API}/portfolios/${portfolioId}/projections?horizon_months=12`);
    $("#demo-projection-summary").textContent = r.summary || "";
    const labels = r.points.map((pt) => pt.label);
    makeChart("demo-projection-chart", {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Optimista (p90)",
            data: r.points.map((pt) => pt.optimistic),
            borderColor: "#22c55e",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "Base (p50)",
            data: r.points.map((pt) => pt.base),
            borderColor: "#c4a574",
            backgroundColor: "rgba(196,165,116,0.1)",
            fill: true,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "Pesimista (p10)",
            data: r.points.map((pt) => pt.pessimistic),
            borderColor: "#ef4444",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#7d8fa3", font: { size: 10 } } } },
        scales: {
          x: { ticks: { color: "#7d8fa3", maxTicksLimit: 7 }, grid: { color: "#1e2a38" } },
          y: { ticks: { color: "#7d8fa3" }, grid: { color: "#1e2a38" } },
        },
      },
    });
    $("#demo-scenarios").innerHTML = (r.scenarios || []).map((s) => `
      <div class="demo-scenario">
        <b>${s.name}</b>
        <div class="val">$${s.projected_value?.toLocaleString()}</div>
        <div>${s.return_pct >= 0 ? "+" : ""}${s.return_pct}%</div>
      </div>`).join("");
  } catch (e) {
    $("#demo-projection-summary").textContent = "Proyecciones no disponibles: " + e.message;
    destroyChart("demo-projection-chart");
  }
}

async function simulateDemoProposal() {
  if (!lastPortfolioId) { toast("Crea un portafolio demo primero"); return; }
  const budget = parseFloat($("#prop-budget")?.value) || 50;
  toast("Simulando propuesta en demo…");
  try {
    const r = await api(`${API}/portfolios/${lastPortfolioId}/simulate`, {
      method: "POST",
      body: JSON.stringify({
        proposal_budget: budget,
        expected_return_pct: 12,
        horizon_months: 6,
      }),
    });
    $("#demo-projection-summary").textContent = r.summary || "Simulación completada";
    toast("Simulación demo lista");
    await loadDemoProjections(lastPortfolioId);
  } catch (e) { toast("Simulación: " + e.message); }
}

function openPortfolioModal() {
  $("#portfolio-modal").classList.remove("hidden");
  $("#portfolio-modal").setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closePortfolioModal() {
  $("#portfolio-modal").classList.add("hidden");
  $("#portfolio-modal").setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

async function syncPortfolioFromAlpaca() {
  await withLoading("Sincronizando portafolio desde Alpaca…", async () => {
    try {
      const p = await api(`${API}/portfolios/sync-alpaca`, { method: "POST", body: "{}" });
      lastPortfolioId = p.id;
      try { localStorage.setItem("nexbuy_portfolio_id", p.id); } catch {}
      toast(`Portafolio sync · cash $${Number(p.cash || 0).toFixed(2)} · ${(p.positions || []).length} posiciones`, 8000);
      await loadDashboard();
    } catch (e) { toast("Sync Alpaca: " + e.message, 8000); }
  });
}

async function createPortfolio() {
  openPortfolioModal();
}

async function submitPortfolioForm() {
  const name = ($("#pf-name").value || "Portafolio CEO").trim();
  const capital = parseFloat($("#pf-capital").value);
  const mode = document.querySelector('input[name="pf-mode"]:checked')?.value || "real";
  if (!capital || capital <= 0) { toast("Ingresa un capital válido"); return; }
  toast("Creando portafolio…");
  try {
    const p = await api(`${API}/portfolios`, {
      method: "POST",
      body: JSON.stringify({
        name,
        mode,
        initial_capital: capital,
        cash: capital,
        strategy: "growth_investing",
      }),
    });
    lastPortfolioId = p.id;
    closePortfolioModal();
    syncAllCapitalFields("#pf-capital");
    toast(`${p.name} (${mode === "demo" ? "Demo" : "Real"}) creado — $${p.initial_capital}`);
    await loadDashboard();
    // Auto-sugerir asignación acorde al capital recién creado
    if ($("#alloc-capital")) {
      $("#alloc-capital").value = p.initial_capital;
      updateCapitalFitHints();
    }
  } catch (e) { toast("Portafolio: " + e.message); }
}

async function scanWatchlist() {
  toast("Escaneando watchlist…");
  try {
    const r = await api(`${API}/watchlist/scan`, { method: "POST" });
    toast(`Escaneo: ${r.scanned} tickers, ${r.alerts} alertas`);
    await loadDashboard();
  } catch (e) { toast("Escaneo: " + e.message); }
}

function parseDiscoveryThemes() {
  const raw = ($("#disc-themes").value || "").trim();
  if (!raw) return null;
  return raw.split(/[,;]+/).map((t) => t.trim()).filter(Boolean);
}

const REC_ES = {
  strong_buy: "Compra fuerte",
  buy: "Compra",
  hold: "Mantener",
  sell: "Venta",
  strong_sell: "Venta fuerte",
};

function renderDiscoveryReport(report) {
  lastDiscoveryReport = report;
  $("#disc-summary").textContent = report.summary || "Sin resultados";
  const candidates = report.candidates || [];
  if (!candidates.length) {
    $("#disc-table-wrap").style.display = "none";
    return;
  }
  $("#disc-table-wrap").style.display = "block";
  $("#disc-body").innerHTML = candidates.map((c) => `
    <tr>
      <td><b>${c.ticker}</b></td>
      <td>${(c.company_name || "—").slice(0, 28)}</td>
      <td>${c.score}</td>
      <td>${c.mention_count}</td>
      <td>${(c.sources || []).join(", ")}</td>
      <td style="font-size:10px;color:var(--muted)">${(c.rationale || "").slice(0, 80)}</td>
      <td><button class="btn disc-add-btn" data-t="${c.ticker}" style="font-size:10px;padding:2px 6px">+ WL</button></td>
    </tr>`).join("");
  $$(".disc-add-btn").forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`${API}/watchlist`, { method: "POST", body: JSON.stringify({ ticker: btn.dataset.t }) });
        toast(`${btn.dataset.t} agregado a watchlist`);
        await loadDashboard();
      } catch (e) { toast("Watchlist: " + e.message); }
    };
  });
}

function renderDiscoveryAnalyses(result) {
  renderDiscoveryReport(result.discovery);
  const analyses = result.analyses || [];
  if (!analyses.length) {
    $("#disc-analyses").innerHTML = "";
    if (result.recommendation_summary) {
      $("#disc-summary").textContent = result.recommendation_summary;
    }
    return;
  }
  $("#disc-summary").textContent = result.recommendation_summary || result.discovery?.summary || "";
  $("#disc-analyses").innerHTML = analyses.map((t) => `
    <div class="disc-analysis-card">
      <h4>${t.ticker}<span class="rec-tag">${REC_ES[t.recommendation] || t.recommendation} · ${(t.confidence * 100).toFixed(0)}%</span></h4>
      <p class="prose" style="font-size:11px;margin:0">${(t.executive_summary || "").slice(0, 400)}</p>
      <button class="btn disc-analyze-btn" data-t="${t.ticker}" style="margin-top:6px;font-size:10px">Ver análisis completo</button>
    </div>`).join("");
  $$(".disc-analyze-btn").forEach((btn) => {
    btn.onclick = () => {
      $("#global-ticker").value = btn.dataset.t;
      runAnalyze();
    };
  });
}

async function runDiscoveryResearch() {
  $("#disc-analyses").innerHTML = "";
  await withLoading("Investigando redes sociales y noticias…", async () => {
    try {
      const r = await api(`${API}/discover/research`, {
        method: "POST",
        body: JSON.stringify({ themes: parseDiscoveryThemes(), max_candidates: 15 }),
      });
      renderDiscoveryReport(r);
      toast(`${(r.candidates || []).length} candidatos encontrados`);
    } catch (e) { toast("Descubrimiento: " + e.message); }
  });
}

async function runDiscoveryAnalyze() {
  const analyzeTop = parseInt($("#disc-analyze-top").value, 10) || 3;
  await withLoading(`Investigando y analizando top ${analyzeTop}…`, async () => {
    try {
      const r = await api(`${API}/discover/analyze`, {
        method: "POST",
        body: JSON.stringify({
          themes: parseDiscoveryThemes(),
          max_candidates: 15,
          analyze_top: analyzeTop,
          portfolio_id: lastPortfolioId,
        }),
      });
      renderDiscoveryAnalyses(r);
      toast("Descubrimiento y análisis completados");
    } catch (e) { toast("Descubrimiento: " + e.message); }
  });
}

function switchToTab(tabName) {
  const tabBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
  if (tabBtn) tabBtn.click();
}

function speakAnalyzeResult(ticker) {
  return new Promise((resolve) => {
    if (!lastThesis || lastThesis.ticker?.toUpperCase() !== ticker.toUpperCase()) {
      resolve();
      return;
    }
    const rec = trRec(lastThesis.recommendation);
    const conf = Math.round((lastThesis.confidence || 0) * 100);
    const summary = (lastThesis.executive_summary || "").slice(0, 280);
    const tech = (lastThesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
    const pb = tech?.raw_data?.playbook;
    const mkt = tech?.raw_data?.market_opinion || pb?.market_opinion;
    let text = `${ticker}: ${rec}, confianza ${conf} por ciento. ${summary}`;
    if (pb?.strategy_es) {
      text += ` Playbook: ${pb.strategy_es}. ${pb.opinion_es || ""}`;
    }
    if (mkt?.available) {
      text += ` Opinión de mercado ${mkt.label_es || ""} (${Number(mkt.aggregated_score || 0) >= 0 ? "+" : ""}${Number(mkt.aggregated_score || 0).toFixed(0)}).`;
    }
    if (["sell", "strong_sell"].includes(String(lastThesis.recommendation || "").toLowerCase())) {
      text += " El comité está en vender; si quieres cerrar en Alpaca di vende seguido del ticker.";
    } else if (["buy", "strong_buy"].includes(String(lastThesis.recommendation || "").toLowerCase())) {
      text += " Si quieres operar di compra 1 seguido del ticker, y luego confirma.";
    }
    if (typeof window.speakAssistant === "function") {
      window.speakAssistant(text, resolve);
      return;
    }
    if (!window.speechSynthesis) { resolve(); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "es-MX";
    const voices = window.speechSynthesis.getVoices();
    const es = voices.find((v) => v.lang.startsWith("es-MX"))
      || voices.find((v) => v.lang.startsWith("es"));
    if (es) u.voice = es;
    window.speechSynthesis.resume();
    u.onend = resolve;
    u.onerror = resolve;
    window.speechSynthesis.speak(u);
  });
}

function renderProposalFromResult(p, extraSummary) {
  lastProposal = p;
  renderProposalVisual(p);
  let out = (extraSummary ? extraSummary + "\n\n" : "") + (p.summary || "") + "\n\n" + (p.executive_report?.narrative || "") + "\n\n";
  if (p.executive_report) {
    out += "POR QUÉ SE SELECCIONARON:\n" + (p.executive_report.why_selected || []).join("\n") + "\n\n";
    out += "POR QUÉ NO:\n" + (p.executive_report.why_excluded || []).join("\n") + "\n\n";
    out += "RIESGOS:\n" + (p.executive_report.key_risks || []).join("\n") + "\n\n";
    out += "A MONITOREAR:\n" + (p.executive_report.events_to_monitor || []).join("\n") + "\n\n";
    if (p.executive_report.correlation_notes?.length) {
      out += "CORRELACIONES:\n" + p.executive_report.correlation_notes.join("\n") + "\n\n";
    }
  }
  out += (p.allocations || []).map((a) =>
    `#${a.purchase_order} ${a.ticker} [${a.instrument}] $${a.allocation_usd} — ${a.rationale}`
  ).join("\n");
  $("#proposal-out").textContent = out;
}

async function runDiscoveryProposal() {
  const budget = parseFloat($("#disc-budget")?.value) || currentPortfolioCapital() || alpacaBookCapital();
  if (!budget || budget <= 0) {
    toast("Espera a que cargue Alpaca o indica el capital real");
    return;
  }
  const proposalTop = Math.min(parseInt($("#disc-analyze-top").value, 10) || 3, 6);
  $("#disc-analyses").innerHTML = "";
  await withLoading("Descubriendo y generando propuesta…", async () => {
    try {
      const r = await api(`${API}/discover/proposal`, {
        method: "POST",
        body: JSON.stringify({
          budget,
          themes: parseDiscoveryThemes(),
          max_candidates: 15,
          proposal_top: proposalTop,
          portfolio_id: lastPortfolioId,
          risk_profile: "balanced",
          instrument_mode: "auto",
          add_to_watchlist: true,
          use_llm_narrative: true,
        }),
      });
      renderDiscoveryReport(r.discovery);
      if (r.watchlist_added?.length) {
        toast(`Watchlist: ${r.watchlist_added.join(", ")}`);
        await loadDashboard();
      }
      $("#prop-budget").value = budget;
      renderProposalFromResult(r.proposal, r.summary);
      switchToTab("proposal");
      toast(`Propuesta lista con ${(r.tickers_selected || []).join(", ")}`);
    } catch (e) { toast("Descubrir → Propuesta: " + e.message); }
  });
}

$$(".tab").forEach((btn) => btn.onclick = () => {
  $$(".tab").forEach((b) => b.classList.remove("active"));
  $$(".tab-pane").forEach((p) => p.classList.remove("active"));
  btn.classList.add("active");
  $(`#tab-${btn.dataset.tab}`).classList.add("active");
});

$("#btn-analyze").onclick = runAnalyze;
$("#btn-refresh").onclick = loadDashboard;
$("#btn-allocation-advise").onclick = buildAllocationAdvise;
$("#btn-alloc-to-proposal").onclick = useAllocationInProposal;
$("#btn-proposal").onclick = buildProposal;
$("#btn-apply-proposal").onclick = applyProposal;
$("#btn-create-portfolio").onclick = createPortfolio;
$("#btn-pf-submit").onclick = submitPortfolioForm;
$("#portfolio-modal-close").onclick = closePortfolioModal;
$("#portfolio-modal-backdrop").onclick = closePortfolioModal;
$("#btn-simulate-proposal").onclick = simulateDemoProposal;
$("#btn-scan").onclick = scanWatchlist;
$("#btn-generate-trades").onclick = generateDailyTrades;
$("#btn-manage-capital").onclick = managePortfolioCapital;
$("#btn-alpaca-doctor") && ($("#btn-alpaca-doctor").onclick = runAlpacaDoctor);
$("#btn-alpaca-refresh-book") && ($("#btn-alpaca-refresh-book").onclick = loadAlpacaBook);
$("#btn-alpaca-cancel-all") && ($("#btn-alpaca-cancel-all").onclick = cancelAllAlpacaOrders);
$("#btn-risk-refresh") && ($("#btn-risk-refresh").onclick = () => { loadRiskDesk(); loadOpsDesk(); });
$("#btn-kill-switch") && ($("#btn-kill-switch").onclick = runKillSwitch);
$("#btn-ops-reconcile") && ($("#btn-ops-reconcile").onclick = runReconcile);
$("#btn-ops-lifecycle") && ($("#btn-ops-lifecycle").onclick = runLifecycleScan);
$("#btn-ops-autopilot") && ($("#btn-ops-autopilot").onclick = runAutopilot);
$("#btn-ops-audit") && ($("#btn-ops-audit").onclick = loadAuditLog);
$("#tech-period").onchange = () => { const t = ticker(); if (t) loadTechnicalChart(t); };
$("#tech-chart-tf").onchange = () => {
  syncChartTimeframe($("#tech-chart-tf").value);
  if (lastGapData) renderGapsPanel(lastGapData);
  const t = ticker();
  if (t) loadTechnicalChart(t);
};
$("#btn-disc-research").onclick = runDiscoveryResearch;
$("#btn-disc-analyze").onclick = runDiscoveryAnalyze;
$("#btn-disc-proposal").onclick = runDiscoveryProposal;
$("#btn-test-push") && ($("#btn-test-push").onclick = testPushNotification);
const btnLogout = $("#btn-logout");
if (btnLogout) {
  btnLogout.addEventListener("click", logoutSession, { capture: true });
  btnLogout.onclick = logoutSession;
}
$("#btn-shock").onclick = simulateShock;

$("#news-modal-close").onclick = closeNewsModal;
$("#news-modal-backdrop").onclick = closeNewsModal;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#news-modal").classList.contains("hidden")) closeNewsModal();
    if (!$("#portfolio-modal").classList.contains("hidden")) closePortfolioModal();
  }
});

(async () => {
  // Never leave a blank green screen if auth/network hangs
  setTimeout(() => {
    if (!document.documentElement.classList.contains("session-ok")) {
      setBootMsg("Abriendo terminal…");
      hideBootSplash();
    }
  }, 10000);

  if (window.MonarchUI) {
    window.MonarchUI.installErrorBoundary();
    window.MonarchUI.bindRetries(document, {
      audit: () => loadAuditLog(auditPageOffset),
      dashboard: () => loadDashboard(),
    });
  }
  $("#btn-dashboard-retry")?.addEventListener("click", () => loadDashboard());
  const ok = await ensureAuth();
  if (ok === false) return; // redirected to /login
  setupMobileNav();
  setupTechMobileControls();
  setupBudgetSync();
  syncBudgetFields("#disc-budget", "#prop-budget");
  const t = localStorage.getItem("nexbuy_token");
  const exportBtn = $("#btn-export-briefing");
  if (exportBtn && t) exportBtn.href = `${API}/reports/daily/latest/export?token=${encodeURIComponent(t)}`;
  if (typeof initVoiceModule === "function") {
    initVoiceModule({
      api,
      API,
      toast,
      loadDashboard,
      loadAlpacaBook,
      runAnalyze,
      runDiscoveryResearch,
      switchToTab,
      focusTechView,
      getPortfolioId: () => lastPortfolioId,
      speakAnalyzeResult,
    });
  }
  setBootMsg("Cargando panel…");
  // Only the mesa may seed/sync the firm book
  if (isDeskPrincipal()) {
    api(`${API}/portfolios/default`, { method: "POST" }).catch(() => {});
  }
  document.getElementById("btn-access-refresh")?.addEventListener("click", () => {
    loadAccessRequests();
    loadPasswordResets();
    loadDeskCapitalRequests();
  });
  document.getElementById("btn-deposit-request")?.addEventListener("click", () => submitDepositRequest());
  document.getElementById("btn-withdraw-request")?.addEventListener("click", () => submitWithdrawRequest());
  loadDashboard();
  setInterval(loadDashboard, REFRESH_MS);
})();
