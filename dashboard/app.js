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
  const map = { bullish: "ALCISTA", bearish: "BAJISTA", neutral: "NEUTRAL" };
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

function authHeaders() {
  const token = localStorage.getItem("nexbuy_token");
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function ensureAuth() {
  try {
    const s = await fetch(`${API}/auth/status`).then((r) => r.json());
    if (!s.auth_required) return;
    const token = localStorage.getItem("nexbuy_token");
    if (!token) {
      location.href = "/login";
      return;
    }
    const check = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!check.ok) {
      localStorage.removeItem("nexbuy_token");
      location.href = "/login";
    }
  } catch {
    /* offline / dev */
  }
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { ...authHeaders(), ...opts.headers },
  });
  if (r.status === 401) {
    localStorage.removeItem("nexbuy_token");
    location.href = "/login";
    throw new Error("Sesión expirada");
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
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
    return `Capital micro ($${c}): se buscarán penny stocks ≤ ~$5 para comprar acciones enteras manteniendo los %.`;
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
  $$(".mob-nav-btn[data-scroll]").forEach((btn) => {
    btn.onclick = () => {
      const id = btn.dataset.scroll;
      const el = id === "watchlist-matrix" ? document.querySelector(".matrix-table")?.closest(".panel") : document.getElementById(id);
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
      $$(".mob-nav-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    };
  });
  $("#mob-analyze")?.addEventListener("click", () => {
    $("#global-ticker")?.focus();
    runAnalyze();
  });
  $$(".mob-nav-btn[data-tab]").forEach((btn) => {
    btn.onclick = () => {
      const tab = btn.dataset.tab;
      const tabBtn = document.querySelector(`.tab[data-tab="${tab}"]`);
      tabBtn?.click();
      tabBtn?.closest(".panel")?.scrollIntoView({ behavior: "smooth" });
    };
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
    return `<div class="idx"><div class="name">${trIndex(i.name)}</div><div class="price">${i.price ?? "—"}</div><div class="chg ${cls}">${fmtPct(i.change_pct)}</div></div>`;
  }).join("");
}

function renderHeatmap(sectors) {
  $("#sector-heatmap").innerHTML = sectors.map((s) =>
    `<div class="heat-cell ${s.regime}"><div>${trSector(s.sector)}</div><div>${s.change_pct != null ? fmtPct(s.change_pct) : "—"}</div></div>`
  ).join("");
}

function renderCeoBar(d) {
  const p = d.portfolio;
  $("#ceo-portfolio").textContent = p ? `$${p.total_value?.toFixed(0)}` : "—";
  const ret = p?.return_pct;
  const retEl = $("#ceo-return");
  retEl.textContent = ret != null ? fmtPct(ret) : "—";
  retEl.className = ret >= 0 ? "up" : "down";
  $("#ceo-alerts").textContent = (d.active_alerts || []).length;
  $("#ceo-watchlist-count").textContent = (d.watchlist || []).length;
  $("#ceo-updated").textContent = d.timestamp ? new Date(d.timestamp).toLocaleTimeString(LOCALE) : new Date().toLocaleTimeString(LOCALE);
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
  $("#trade-recs-summary").textContent = r.summary || "";
  $("#trade-recs-disclaimer").textContent = r.disclaimer || "";

  const picks = r.picks || [];
  const grid = $("#trade-recs-grid");
  if (!picks.length) {
    grid.innerHTML = `<p class="muted">Sin setups de momentum hoy. Pulsa <b>Gestionar capital</b> para que el escritorio busque penny stocks asequibles a tu portafolio.</p>`;
    return;
  }

  grid.innerHTML = picks.map((p) => `
    <div class="trade-rec-card">
      <div class="tr-head">
        <span class="tr-ticker">${p.ticker}</span>
        <span class="tr-action ${p.action === "vigilar" ? "watch" : ""}">${p.action} · ${p.horizon}</span>
      </div>
      <div style="font-size:10px;color:var(--muted)">${(p.company_name || "").slice(0, 32)}</div>
      <div class="tr-levels">
        <span>Precio: <b>$${p.current_price ?? "—"}</b></span>
        <span>Objetivo: <b class="up">$${p.target_price ?? "—"}</b></span>
        <span>Stop: <b class="down">$${p.stop_loss ?? "—"}</b></span>
        <span>Retorno: <b>${p.expected_return_pct != null ? "+" + p.expected_return_pct + "%" : "—"}</b></span>
        <span>Δ1d: ${p.change_1d_pct != null ? p.change_1d_pct + "%" : "—"}</span>
        <span>Score: ${p.score} · ${(p.confidence * 100).toFixed(0)}%</span>
      </div>
      <div class="tr-catalysts">${(p.catalysts || []).slice(0, 2).join(" · ") || p.rationale?.slice(0, 100) || ""}</div>
      <div class="tr-btns">
        <button class="btn tr-analyze-btn" data-t="${p.ticker}">Analizar</button>
        <button class="btn tr-add-btn" data-t="${p.ticker}">+ WL</button>
        <button class="btn primary tr-alpaca-btn" data-t="${p.ticker}"
          data-stop="${p.stop_loss ?? ""}" data-target="${p.target_price ?? ""}"
          data-price="${p.current_price ?? ""}">Alpaca</button>
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

function renderAlpacaStatus(st) {
  lastAlpacaStatus = st;
  const el = $("#alpaca-status");
  if (!el) return;
  el.classList.remove("ok", "warn", "err");
  if (!st?.configured) {
    el.classList.add("warn");
    el.textContent = "Alpaca LIVE: sin keys — añade ALPACA_API_KEY + ALPACA_SECRET_KEY (brokerage) en el entorno";
    return;
  }
  if (!st.connected) {
    el.classList.add("err");
    el.textContent = `Alpaca: ${st.message || "error de conexión"}`;
    return;
  }
  const mode = st.paper ? "Paper" : "LIVE";
  const cash = st.account?.cash != null ? ` · cash $${Number(st.account.cash).toFixed(2)}` : "";
  const mkt = st.market_open === true ? " · mercado abierto" : (st.market_open === false ? " · mercado cerrado" : "");
  if (st.paper) {
    el.classList.add("ok");
    el.textContent = `Alpaca Paper conectado${cash}${mkt}`;
  } else {
    el.classList.add("err");
    el.textContent = `Alpaca LIVE · dinero real${cash}${mkt}`;
  }
}

async function loadAlpacaStatus() {
  try {
    const st = await api(`${API}/broker/status`);
    renderAlpacaStatus(st);
  } catch {
    const el = $("#alpaca-status");
    if (el) {
      el.classList.add("warn");
      el.textContent = "Alpaca: estado no disponible";
    }
  }
}

function confirmAlpacaLive() {
  if (!lastAlpacaStatus || lastAlpacaStatus.paper === false) {
    return window.confirm(
      "ATENCIÓN: vas a enviar órdenes LIVE a Alpaca con dinero REAL.\n\n¿Confirmas la ejecución?"
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
  const cash = lastAlpacaStatus?.account?.cash;
  if (cash != null && Number(cash) <= 0) {
    toast("Alpaca tiene $0 de cash. Fondea en app.alpaca.markets antes de comprar.", 8000);
    return;
  }
  const price = opts.price || 0;
  const capital = currentPortfolioCapital() || 22;
  let shares = 1;
  if (price > 0) shares = Math.max(1, Math.floor((capital * 0.35) / price));
  const body = {
    ticker,
    shares,
    dry_run: false,
    confirm_live: lastAlpacaStatus?.paper === false,
  };
  // Market order simple (sin bracket) evita rechazos extra en cuentas pequeñas
  // stop/target se pueden poner luego en Alpaca
  await withLoading(`Enviando ${shares}× ${ticker} a Alpaca…`, async () => {
    try {
      const r = await api(`${API}/broker/execute/pick`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      showAlpacaExecuteResult(r);
      await loadAlpacaStatus();
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
      // sin stop/target en el envío → market simple (más fiable con poco capital)
    })),
    dry_run: dryRun,
    confirm_live: lastAlpacaStatus?.paper === false,
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
      if (!dryRun) await loadAlpacaStatus();
    } catch (e) { toast("Alpaca plan: " + e.message, 8000); }
  });
}

function renderMicroPlan(plan) {
  const el = $("#micro-plan-panel");
  if (!el) return;
  lastMicroPlan = plan;
  if (!plan?.lines?.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="micro-plan-head">Plan de gestión · capital $${plan.capital} · máx $${plan.max_share_price}/acc</div>
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
  const fromInputs = parseFloat($("#alloc-capital")?.value)
    || parseFloat($("#prop-budget")?.value)
    || parseFloat($("#disc-budget")?.value)
    || parseFloat($("#pf-capital")?.value);
  if (fromInputs) return fromInputs;
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
  const capital = currentPortfolioCapital();
  if (!capital) {
    toast("Crea un portafolio o indica el capital primero");
    openPortfolioModal();
    return;
  }
  await withLoading(`Gestionando capital $${capital}…`, async () => {
    try {
      const plan = await api(`${API}/recommendations/manage-capital`, {
        method: "POST",
        body: JSON.stringify({ capital, persist_as_daily: true }),
      });
      renderMicroPlan(plan);
      $("#trade-recs-summary").textContent = plan.summary || "";
      if (plan.picks?.length) {
        renderTradeRecommendations({
          picks: plan.picks,
          summary: plan.summary,
          generated_at: new Date().toISOString(),
          disclaimer: "Plan de escritorio de capital — penny stocks asequibles. No es asesoría financiera.",
        });
      }
      toast(plan.lines?.length
        ? `Plan: ${plan.lines.map((l) => l.ticker).join(", ")}`
        : "Sin líneas — intenta de nuevo");
    } catch (e) { toast("Gestión: " + e.message); }
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
        datasets: [{ data: sectorData, backgroundColor: ["#3b82f6","#22c55e","#f59e0b","#ef4444","#8b5cf6","#64748b"] }],
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
        datasets: [{ data: capData, backgroundColor: ["#3b82f6","#22c55e","#f59e0b"] }],
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
  regime.textContent = `MERCADO ${trRegime(d.market_regime)} (${scoreSign}${d.market_regime_score})`;
  regime.className = `regime ${d.market_regime}`;
  renderCeoBar(d);
  renderIndices(d.indices || []);
  renderHeatmap(d.sector_heatmap || []);
  $("#econ-calendar").innerHTML = (d.economic_calendar || []).map((e) => `<div><b>${e.date}</b> ${e.title}</div>`).join("") || "—";
  renderMarketNews(d.news_highlights || []);
  $("#m-msent").textContent = fmtScore(d.market_sentiment_score);
  $("#watchlist").innerHTML = (d.watchlist || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$(".wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
  $("#alerts-panel").innerHTML = (d.active_alerts || []).map((a) => `<div>${a}</div>`).join("") || "Sin alertas";
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
  ` : "Sin portafolio — créalo con el botón de arriba";
  renderPortfolioPies(p);
  lastPortfolioId = p?.portfolio_id || null;
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
      if (s.webhook) parts.push("WH");
      badge.textContent = `push ${parts.join("+")}`;
      badge.className = "push-badge on";
      badge.title = "Notificaciones push activas";
    } else {
      badge.textContent = "push off";
      badge.className = "push-badge off";
      badge.title = "Configura TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID en el servidor";
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
}

async function loadDashboard() {
  try {
    const d = await api(`${API}/dashboard`);
    renderDashboard(d);
    await Promise.all([
      loadDailyBriefing(),
      loadDailyTradeRecommendations(),
      loadWatchlistMatrix(),
      loadPushStatus(),
      loadAlpacaStatus(),
    ]);
  } catch (e) { toast("Panel: " + e.message); }
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

async function loadTechnicalChart(t, techAgentReport) {
  const period = $("#tech-period")?.value || "6mo";
  const chartTf = $("#tech-chart-tf")?.value || activeGapTf || "1D";
  syncChartTimeframe(chartTf);
  const intraday = isIntradayTf(chartTf);
  try {
    const data = await api(`${API}/market/${t}/technical?period=${period}&timeframe=${encodeURIComponent(chartTf)}`);
    const pts = data.points || [];
    if (!pts.length) {
      destroyAllLwCharts();
      $("#tech-summary").textContent = data.summary || "Sin datos técnicos.";
      if (data.gaps_by_timeframe) renderGapsPanel(data);
      return;
    }

    renderTechnicalKpis(data.snapshot);

    let summary = data.summary || "";
    if (techAgentReport?.summary) {
      summary = techAgentReport.summary + (summary ? `\n\n${summary}` : "");
    }
    $("#tech-summary").textContent = summary;

    const chartOpts = {
      layout: { background: { color: "transparent" }, textColor: "#7d8fa3" },
      grid: { vertLines: { color: "#1e2a38" }, horzLines: { color: "#1e2a38" } },
      rightPriceScale: { borderColor: "#1e2a38" },
      timeScale: { borderColor: "#1e2a38", timeVisible: intraday, secondsVisible: false },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    };

    const mapTime = (p) => lwTime(p.date, chartTf);

    destroyLwChart("candle");
    const candleEl = $("#candle-chart");
    lwCharts.candle = LightweightCharts.createChart(candleEl, { ...chartOpts, height: candleEl.clientHeight || 260 });
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
    lwCharts.rsi = LightweightCharts.createChart(rsiEl, { ...chartOpts, height: rsiEl.clientHeight || 120 });
    const rsiSeries = lwCharts.rsi.addLineSeries({ color: "#3b82f6", lineWidth: 2, title: "RSI" });
    rsiSeries.setData(pts.filter((p) => p.rsi != null).map((p) => ({ time: mapTime(p), value: p.rsi })));
    rsiSeries.createPriceLine({ price: 70, color: "rgba(239,68,68,0.6)", lineWidth: 1, lineStyle: 2 });
    rsiSeries.createPriceLine({ price: 30, color: "rgba(34,197,94,0.6)", lineWidth: 1, lineStyle: 2 });
    lwCharts.rsi.timeScale().fitContent();

    destroyLwChart("macd");
    const macdEl = $("#macd-chart");
    lwCharts.macd = LightweightCharts.createChart(macdEl, { ...chartOpts, height: macdEl.clientHeight || 120 });
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
  } catch (e) {
    destroyAllLwCharts();
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
      const [thesis, sent, graph, corr] = await Promise.all([
      api(`${API}/analyze`, { method: "POST", body: JSON.stringify({ ticker: t }) }),
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

    const news = (thesis.agent_reports || []).find((r) => r.agent_name === "news_agent");
    $("#agents-grid").innerHTML = (thesis.agent_reports || []).map((r) => {
      const c = r.score >= 0 ? "pos" : "neg";
      return `<div class="agent-chip"><span>${trAgent(r.agent_name)}</span><span class="${c}">${fmtScore(r.score)}</span></div>`;
    }).join("");

    let txt = `${thesis.ticker} ${trRec(thesis.recommendation)} @ ${(thesis.confidence * 100).toFixed(0)}%\n\n${thesis.executive_summary}\n\n${thesis.investment_thesis}\n`;
    if (news?.raw_data) {
      txt += `\n2 años: ${news.raw_data.two_year_summary || ""}\n3 meses: ${news.raw_data.three_month_summary || ""}\nIMPACTO: ${news.raw_data.investment_impact || ""}`;
    }
    const dep = (thesis.agent_reports || []).find((r) => r.agent_name === "market_dependency_agent");
    if (dep) txt += `\n\nCORRELACIONES:\n${dep.summary}`;
    const tech = (thesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
    if (tech?.raw_data?.cross_agent_correlations?.length) {
      txt += `\n\nCONTEXTO TÉCNICO:\n` + tech.raw_data.cross_agent_correlations.join("\n");
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
    color: n.type === "company" ? "#3b82f6" : n.type === "geopolitical" ? "#ef4444" : n.type === "commodity" ? "#f59e0b" : "#64748b",
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
        backgroundColor: ["#3b82f6","#22c55e","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899"],
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
  const capital = parseFloat($("#alloc-capital").value) || 1000;
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
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59,130,246,0.1)",
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
    const summary = (lastThesis.executive_summary || "").slice(0, 350);
    const text = `${ticker}: ${rec}, confianza ${conf} por ciento. ${summary}`;
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
  const budget = parseFloat($("#disc-budget")?.value) || 1000;
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
$("#btn-alpaca-cancel-all") && ($("#btn-alpaca-cancel-all").onclick = cancelAllAlpacaOrders);
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
$("#btn-test-push").onclick = testPushNotification;
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
  await ensureAuth();
  setupMobileNav();
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
      runAnalyze,
      runDiscoveryResearch,
      switchToTab,
      getPortfolioId: () => lastPortfolioId,
      speakAnalyzeResult,
    });
  }
  loadDashboard();
  setInterval(loadDashboard, REFRESH_MS);
})();
