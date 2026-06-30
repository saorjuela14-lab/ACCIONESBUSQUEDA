const API = "/api/v1";
const REFRESH_MS = 120000;
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const charts = {};
let lastProposal = null;
let lastPortfolioId = null;

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
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
    return `<div class="idx"><div class="name">${i.name}</div><div class="price">${i.price ?? "—"}</div><div class="chg ${cls}">${fmtPct(i.change_pct)}</div></div>`;
  }).join("");
}

function renderHeatmap(sectors) {
  $("#sector-heatmap").innerHTML = sectors.map((s) =>
    `<div class="heat-cell ${s.regime}"><div>${s.sector}</div><div>${s.change_pct != null ? fmtPct(s.change_pct) : "—"}</div></div>`
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
  $("#ceo-updated").textContent = d.timestamp ? new Date(d.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
}

function renderProviderHealth(health) {
  if (!health || !health.providers) {
    $("#provider-health").textContent = "Providers: —";
    return;
  }
  const p = health.providers;
  const parts = [
    `Polygon: ${p.polygon?.authenticated ? "OK" : p.polygon?.configured ? "ERR" : "—"}`,
    `AV: ${p.alpha_vantage?.configured ? "OK" : "—"}`,
    `FRED: ${p.fred?.configured ? "OK" : "—"}`,
    `YF: ${p.yfinance?.enabled ? "OK" : "—"}`,
  ];
  $("#provider-health").textContent = `Providers: ${parts.join(" · ")} | Auto-refresh ${REFRESH_MS / 1000}s`;
}

async function loadDailyBriefing() {
  const el = $("#daily-briefing");
  $("#btn-export-briefing").href = `${API}/reports/daily/latest/export`;
  try {
    const r = await api(`${API}/reports/daily/latest`);
    $("#briefing-date").textContent = r.date ? new Date(r.date).toLocaleDateString() : "";
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
        <td class="${recClass(r.recommendation)}">${(r.recommendation || "—").toUpperCase()}</td>
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
  const capLabels = Object.keys(p.cap_exposure || {}).filter((k) => p.cap_exposure[k] > 0);
  const capData = capLabels.map((k) => p.cap_exposure[k]);
  if (capLabels.length) {
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
    ? items.map((o) => `<div class="opp-item" data-t="${o.ticker}"><b>${o.ticker}</b> ${o.recommendation} ${(o.confidence * 100).toFixed(0)}%<br/><span>${o.reason?.slice(0, 80) || ""}</span></div>`).join("")
    : "—";
  $("#opportunities").innerHTML = `<h4>Opportunities</h4>${fmt(opps)}`;
  $("#risks-panel").innerHTML = `<h4>Risks</h4>${fmt(risks)}`;
  $$(".opp-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
}

function renderDashboard(d) {
  const regime = $("#market-regime");
  regime.textContent = `MARKET ${d.market_regime.toUpperCase()} (${d.market_regime_score >= 0 ? "+" : ""}${d.market_regime_score})`;
  regime.className = `regime ${d.market_regime}`;
  renderCeoBar(d);
  renderIndices(d.indices || []);
  renderHeatmap(d.sector_heatmap || []);
  $("#econ-calendar").innerHTML = (d.economic_calendar || []).map((e) => `<div><b>${e.date}</b> ${e.title}</div>`).join("") || "—";
  $("#market-news").innerHTML = (d.news_highlights || []).map((n) => `<div>${n.title}</div>`).join("") || "—";
  $("#m-msent").textContent = fmtScore(d.market_sentiment_score);
  $("#watchlist").innerHTML = (d.watchlist || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$(".wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
  $("#alerts-panel").innerHTML = (d.active_alerts || []).map((a) => `<div>${a}</div>`).join("") || "No alerts";
  $("#recent-panel").innerHTML = (d.recently_analyzed || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$("#recent-panel .wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });

  const p = d.portfolio;
  $("#portfolio-panel").innerHTML = p ? `
    <div><b>${p.name || "Portfolio"}</b></div>
    <div>Value: $${p.total_value?.toFixed(2)}</div>
    <div>Return: ${fmtPct(p.return_pct)}</div>
    <div>Sharpe: ${p.sharpe?.toFixed(2) ?? "—"}</div>
    <div>Drawdown: ${p.max_drawdown?.toFixed(2) ?? "—"}%</div>
    <div>Unrealized P&L: $${p.unrealized_pnl?.toFixed(2)}</div>
    <div>Countries: ${Object.entries(p.country_weights || {}).map(([k,v]) => `${k} ${v}%`).join(", ") || "—"}</div>
  ` : "No portfolio — create via API";
  renderPortfolioPies(p);
  lastPortfolioId = p?.portfolio_id || null;
  if (p?.portfolio_id) loadPortfolioHistory(p.portfolio_id);
  else destroyChart("portfolio-history-chart");
  renderOpportunities(d.top_opportunities || [], d.top_risks || []);
  renderProviderHealth(d.provider_health);
}

async function loadDashboard() {
  try {
    const d = await api(`${API}/dashboard`);
    renderDashboard(d);
    await Promise.all([loadDailyBriefing(), loadWatchlistMatrix()]);
  } catch (e) { toast("Dashboard: " + e.message); }
}

async function loadPriceChart(t) {
  try {
    const data = await api(`${API}/market/${t}/chart?period=6mo`);
    const labels = data.points.map((p) => p.date);
    const closes = data.points.map((p) => p.close);
    const highs = data.points.map((p) => p.high ?? p.close);
    const lows = data.points.map((p) => p.low ?? p.close);
    makeChart("price-chart", {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "High",
            data: highs,
            borderColor: "rgba(34,197,94,0.35)",
            backgroundColor: "transparent",
            borderWidth: 1,
            pointRadius: 0,
            tension: 0.1,
          },
          {
            label: "Close",
            data: closes,
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59,130,246,0.12)",
            fill: true,
            tension: 0.2,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: "Low",
            data: lows,
            borderColor: "rgba(239,68,68,0.35)",
            backgroundColor: "transparent",
            borderWidth: 1,
            pointRadius: 0,
            tension: 0.1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#7d8fa3", maxTicksLimit: 8 }, grid: { color: "#1e2a38" } },
          y: { ticks: { color: "#7d8fa3" }, grid: { color: "#1e2a38" } },
        },
      },
    });
  } catch { destroyChart("price-chart"); }
}

async function loadSentimentTrend(t) {
  try {
    const hist = await api(`${API}/sentiment/${t}/history?limit=60`);
    if (!hist.length) { destroyChart("sentiment-trend-chart"); return; }
    makeChart("sentiment-trend-chart", {
      type: "line",
      data: {
        labels: hist.map((h) => new Date(h.timestamp).toLocaleDateString()),
        datasets: [{
          label: "Sentiment",
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
        labels: hist.map((h) => new Date(h.timestamp).toLocaleDateString()),
        datasets: [{
          label: "Portfolio Value",
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
    ["Bull", thesis.bull_case, "bull"],
    ["Base", thesis.base_case, "base"],
    ["Bear", thesis.bear_case, "bear"],
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
    : `<li class="muted">Sin correlaciones cruzadas — ejecuta Analyze para generar contexto técnico.</li>`;
}

function renderCorrelations(corr) {
  $("#correlations-out").innerHTML = `
    <p class="prose">${corr.summary}</p>
    <h4 class="subhead">Benchmark Correlations</h4>
    <table class="matrix-table compact">
      <thead><tr><th>ETF</th><th>ρ</th><th>Relación</th></tr></thead>
      <tbody>${(corr.benchmark_correlations || []).map((p) =>
        `<tr><td>${p.ticker}</td><td>${p.correlation?.toFixed(2) ?? "—"}</td><td>${p.interpretation}</td></tr>`
      ).join("")}</tbody>
    </table>
    <h4 class="subhead">Macro Sensitivities</h4>
    <ul class="corr-list">${(corr.macro_sensitivities || []).map((m) =>
      `<li><b>${m.factor}</b> [${m.sensitivity}] — ${m.scenario}: ${m.impact_if_shock}</li>`
    ).join("")}</ul>
    <h4 class="subhead">Company Dependencies</h4>
    <ul class="corr-list">${(corr.company_dependencies || []).map((d) =>
      `<li><b>${d.ticker}</b> ${d.relationship}${d.correlation != null ? ` (ρ=${d.correlation.toFixed(2)})` : ""}: ${d.why_it_matters}</li>`
    ).join("")}</ul>`;
}

async function runAnalyze() {
  const t = ticker();
  toast(`Analyzing ${t}…`);
  try {
    const [thesis, sent, graph, corr] = await Promise.all([
      api(`${API}/analyze`, { method: "POST", body: JSON.stringify({ ticker: t }) }),
      api(`${API}/sentiment/${t}/engine`),
      api(`${API}/graph/${t}`),
      api(`${API}/correlations/${t}`),
    ]);
    $("#m-rec").textContent = (thesis.recommendation || "").toUpperCase();
    $("#m-rec").className = recClass(thesis.recommendation);
    $("#m-conf").textContent = thesis.confidence ? `${(thesis.confidence * 100).toFixed(0)}%` : "—";
    $("#m-target").textContent = thesis.price_target ? `$${thesis.price_target.toFixed(2)}` : "—";
    $("#exec-summary").textContent = thesis.executive_summary || "";
    renderScenarios(thesis);
    renderTechCorrelations(thesis);
    await loadPriceChart(t);

    const news = (thesis.agent_reports || []).find((r) => r.agent_name === "news_agent");
    $("#agents-grid").innerHTML = (thesis.agent_reports || []).map((r) => {
      const c = r.score >= 0 ? "pos" : "neg";
      return `<div class="agent-chip"><span>${r.agent_name.replace("_agent", "")}</span><span class="${c}">${fmtScore(r.score)}</span></div>`;
    }).join("");

    let txt = `${thesis.ticker} ${thesis.recommendation?.toUpperCase()} @ ${(thesis.confidence * 100).toFixed(0)}%\n\n${thesis.executive_summary}\n\n${thesis.investment_thesis}\n`;
    if (news?.raw_data) {
      txt += `\n2Y: ${news.raw_data.two_year_summary || ""}\n3M: ${news.raw_data.three_month_summary || ""}\nIMPACT: ${news.raw_data.investment_impact || ""}`;
    }
    const dep = (thesis.agent_reports || []).find((r) => r.agent_name === "market_dependency_agent");
    if (dep) txt += `\n\nCORRELATIONS:\n${dep.summary}`;
    const tech = (thesis.agent_reports || []).find((r) => r.agent_name === "technical_agent");
    if (tech?.raw_data?.cross_agent_correlations?.length) {
      txt += `\n\nTECH CONTEXT:\n` + tech.raw_data.cross_agent_correlations.join("\n");
    }
    $("#analysis-out").textContent = txt;
    renderSentiment(sent);
    await loadSentimentTrend(t);
    renderGraph(graph);
    renderCorrelations(corr);
    await loadWatchlistMatrix();
    toast(`${t} done`);
  } catch (e) { toast("Analyze: " + e.message); }
}

function renderSentiment(s) {
  const channels = [
    ["Institutional", s.institutional], ["Retail", s.retail], ["Social", s.social],
    ["News", s.news], ["Analyst", s.analyst],
  ];
  $("#sentiment-out").innerHTML = `
    <p class="prose">${s.summary}</p>
    <div class="sent-grid">${channels.map(([label, ch]) => `
      <div class="sent-card"><h4>${label}</h4>
        <div class="score" style="color:${ch.score >= 0 ? "var(--green)" : ch.score < 0 ? "var(--red)" : "inherit"}">${fmtScore(ch.score)}</div>
        <div>Conf ${(ch.confidence * 100).toFixed(0)}% · ${ch.trend} · n=${ch.sample_size}</div>
        <div style="font-size:10px;color:var(--muted)">${(ch.top_factors || []).slice(0, 2).join("; ")}</div>
      </div>`).join("")}
    </div><p style="margin-top:8px;font-size:11px;color:var(--muted)">Sources: ${s.sources_used?.join(", ")} | Failed: ${s.sources_failed?.join(", ") || "none"}</p>`;
}

function renderGraph(g) {
  $("#graph-summary").textContent = g.summary + "\n\nBeneficiaries: " + (g.beneficiaries || []).join(", ") + "\nAt risk: " + (g.at_risk || []).join(", ");
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
      <thead><tr><th>#</th><th>Ticker</th><th>Inst</th><th>$</th><th>%</th><th>Margin</th></tr></thead>
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

async function buildProposal() {
  const tickers = $("#prop-tickers").value.trim();
  const body = {
    budget: parseFloat($("#prop-budget").value) || 50,
    tickers: tickers ? tickers.split(",").map((s) => s.trim().toUpperCase()) : null,
    use_watchlist: !tickers,
    risk_profile: $("#prop-risk").value,
    instrument_mode: "auto",
  };
  toast("Building proposal…");
  try {
    const p = await api(`${API}/proposal`, { method: "POST", body: JSON.stringify(body) });
    lastProposal = p;
    renderProposalVisual(p);
    let out = p.summary + "\n\n" + (p.executive_report?.narrative || "") + "\n\n";
    if (p.executive_report) {
      out += "WHY SELECTED:\n" + p.executive_report.why_selected.join("\n") + "\n\n";
      out += "WHY NOT:\n" + (p.executive_report.why_excluded || []).join("\n") + "\n\n";
      out += "RISKS:\n" + p.executive_report.key_risks.join("\n") + "\n\n";
      out += "MONITOR:\n" + p.executive_report.events_to_monitor.join("\n") + "\n\n";
      if (p.executive_report.correlation_notes?.length) {
        out += "CORRELATIONS:\n" + p.executive_report.correlation_notes.join("\n") + "\n\n";
      }
    }
    out += (p.allocations || []).map((a) =>
      `#${a.purchase_order} ${a.ticker} [${a.instrument}] $${a.allocation_usd} — ${a.rationale}`
    ).join("\n");
    $("#proposal-out").textContent = out;
    toast("Proposal ready");
  } catch (e) { toast("Proposal: " + e.message); }
}

async function applyProposal() {
  if (!lastProposal) { toast("Genera una propuesta primero"); return; }
  let pid = lastPortfolioId;
  if (!pid) {
    try {
      const p = await api(`${API}/portfolios/default`, { method: "POST" });
      pid = p.id;
      lastPortfolioId = pid;
    } catch (e) { toast("Portfolio: " + e.message); return; }
  }
  toast("Aplicando propuesta…");
  try {
    await api(`${API}/proposal/apply`, {
      method: "POST",
      body: JSON.stringify({ portfolio_id: pid, proposal: lastProposal }),
    });
    $("#proposal-apply-msg").textContent = `Propuesta aplicada al portfolio ${pid}`;
    toast("Propuesta aplicada");
    await loadDashboard();
  } catch (e) { toast("Apply: " + e.message); }
}

async function createPortfolio() {
  toast("Creando portfolio CEO…");
  try {
    const p = await api(`${API}/portfolios/default`, { method: "POST" });
    lastPortfolioId = p.id;
    toast(`Portfolio ${p.name} creado ($${p.initial_capital})`);
    await loadDashboard();
  } catch (e) { toast("Portfolio: " + e.message); }
}

async function scanWatchlist() {
  toast("Scanning watchlist…");
  try {
    const r = await api(`${API}/watchlist/scan`, { method: "POST" });
    toast(`Scan: ${r.scanned} tickers, ${r.alerts} alertas`);
    await loadDashboard();
  } catch (e) { toast("Scan: " + e.message); }
}

$$(".tab").forEach((btn) => btn.onclick = () => {
  $$(".tab").forEach((b) => b.classList.remove("active"));
  $$(".tab-pane").forEach((p) => p.classList.remove("active"));
  btn.classList.add("active");
  $(`#tab-${btn.dataset.tab}`).classList.add("active");
});

$("#btn-analyze").onclick = runAnalyze;
$("#btn-refresh").onclick = loadDashboard;
$("#btn-proposal").onclick = buildProposal;
$("#btn-apply-proposal").onclick = applyProposal;
$("#btn-create-portfolio").onclick = createPortfolio;
$("#btn-scan").onclick = scanWatchlist;
$("#btn-shock").onclick = simulateShock;

loadDashboard();
setInterval(loadDashboard, REFRESH_MS);
