const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

function ticker() { return ($("#global-ticker").value || "VRT").trim().toUpperCase(); }

function renderIndices(indices) {
  $("#indices-row").innerHTML = indices.map((i) => {
    const cls = (i.change_pct || 0) >= 0 ? "up" : "down";
    const sign = (i.change_pct || 0) >= 0 ? "+" : "";
    return `<div class="idx"><div class="name">${i.name}</div><div class="price">${i.price ?? "—"}</div><div class="chg ${cls}">${sign}${i.change_pct ?? 0}%</div></div>`;
  }).join("");
}

function renderHeatmap(sectors) {
  $("#sector-heatmap").innerHTML = sectors.map((s) =>
    `<div class="heat-cell ${s.regime}"><div>${s.sector}</div><div>${s.change_pct != null ? (s.change_pct >= 0 ? "+" : "") + s.change_pct + "%" : "—"}</div></div>`
  ).join("");
}

function renderDashboard(d) {
  const regime = $("#market-regime");
  regime.textContent = `MARKET ${d.market_regime.toUpperCase()} (${d.market_regime_score >= 0 ? "+" : ""}${d.market_regime_score})`;
  regime.className = `regime ${d.market_regime}`;
  renderIndices(d.indices || []);
  renderHeatmap(d.sector_heatmap || []);
  $("#econ-calendar").innerHTML = (d.economic_calendar || []).map((e) => `<div><b>${e.date}</b> ${e.title}</div>`).join("") || "—";
  $("#market-news").innerHTML = (d.news_highlights || []).map((n) => `<div>${n.title}</div>`).join("") || "—";
  $("#m-msent").textContent = `${d.market_sentiment_score >= 0 ? "+" : ""}${d.market_sentiment_score?.toFixed(1)}`;
  $("#watchlist").innerHTML = (d.watchlist || []).map((t) => `<div class="wl-item" data-t="${t}">${t}</div>`).join("") || "—";
  $$(".wl-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
  $("#alerts-panel").innerHTML = (d.active_alerts || []).map((a) => `<div>${a}</div>`).join("") || "No alerts";
  $("#recent-panel").innerHTML = (d.recently_analyzed || []).map((t) => `<div>${t}</div>`).join("") || "—";
  const p = d.portfolio;
  $("#portfolio-panel").innerHTML = p ? `
    <div><b>${p.name || "Portfolio"}</b></div>
    <div>Value: $${p.total_value?.toFixed(2)}</div>
    <div>Return: ${p.return_pct?.toFixed(2)}%</div>
    <div>Sharpe: ${p.sharpe?.toFixed(2) ?? "—"}</div>
    <div>Drawdown: ${p.max_drawdown?.toFixed(2) ?? "—"}%</div>
    <div>Unrealized P&L: $${p.unrealized_pnl?.toFixed(2)}</div>
    <div>Sectors: ${Object.entries(p.sector_weights || {}).map(([k,v]) => `${k} ${v}%`).join(", ") || "—"}</div>
    <div>Countries: ${Object.entries(p.country_weights || {}).map(([k,v]) => `${k} ${v}%`).join(", ") || "—"}</div>
    <div>Cap: ${Object.entries(p.cap_exposure || {}).filter(([,v]) => v > 0).map(([k,v]) => `${k} ${v}%`).join(", ") || "—"}</div>
  ` : "No portfolio — create via API";
  renderOpportunities(d.top_opportunities || [], d.top_risks || []);
}

function renderOpportunities(opps, risks) {
  const fmt = (items) => items.length
    ? items.map((o) => `<div class="opp-item" data-t="${o.ticker}"><b>${o.ticker}</b> ${o.recommendation} ${(o.confidence * 100).toFixed(0)}%<br/><span>${o.reason?.slice(0, 80) || ""}</span></div>`).join("")
  : "—";
  $("#opportunities").innerHTML = `<h4>Opportunities</h4>${fmt(opps)}`;
  $("#risks-panel").innerHTML = `<h4>Risks</h4>${fmt(risks)}`;
  $$(".opp-item").forEach((el) => el.onclick = () => { $("#global-ticker").value = el.dataset.t; runAnalyze(); });
}

async function loadDashboard() {
  try {
    const d = await api(`${API}/dashboard`);
    renderDashboard(d);
  } catch (e) { toast("Dashboard: " + e.message); }
}

async function runAnalyze() {
  const t = ticker();
  toast(`Analyzing ${t}…`);
  try {
    const [thesis, sent, graph] = await Promise.all([
      api(`${API}/analyze`, { method: "POST", body: JSON.stringify({ ticker: t }) }),
      api(`${API}/sentiment/${t}/engine`),
      api(`${API}/graph/${t}`),
    ]);
    $("#m-rec").textContent = (thesis.recommendation || "").toUpperCase();
    $("#m-conf").textContent = thesis.confidence ? `${(thesis.confidence * 100).toFixed(0)}%` : "—";
    $("#exec-summary").textContent = thesis.executive_summary || "";
    const news = (thesis.agent_reports || []).find((r) => r.agent_name === "news_agent");
    $("#m-news").textContent = news ? `${news.score >= 0 ? "+" : ""}${news.score?.toFixed(1)}` : "—";
    $("#agents-grid").innerHTML = (thesis.agent_reports || []).map((r) => {
      const c = r.score >= 0 ? "pos" : "neg";
      return `<div class="agent-chip"><span>${r.agent_name.replace("_agent", "")}</span><span class="${c}">${r.score >= 0 ? "+" : ""}${r.score?.toFixed(1)}</span></div>`;
    }).join("");
    let txt = `${thesis.ticker} ${thesis.recommendation?.toUpperCase()} @ ${(thesis.confidence * 100).toFixed(0)}%\n\n${thesis.executive_summary}\n\n${thesis.investment_thesis}\n`;
    if (news?.raw_data) {
      txt += `\n2Y: ${news.raw_data.two_year_summary || ""}\n3M: ${news.raw_data.three_month_summary || ""}\nIMPACT: ${news.raw_data.investment_impact || ""}`;
    }
    const dep = (thesis.agent_reports || []).find((r) => r.agent_name === "market_dependency_agent");
    if (dep) txt += `\n\nCORRELATIONS:\n${dep.summary}`;
    $("#analysis-out").textContent = txt;
    renderSentiment(sent);
    renderGraph(graph);
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
        <div class="score" style="color:${ch.score >= 0 ? "var(--green)" : ch.score < 0 ? "var(--red)" : "inherit"}">${ch.score >= 0 ? "+" : ""}${ch.score?.toFixed(1)}</div>
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
    let out = p.summary + "\n\n" + (p.executive_report?.narrative || "") + "\n\n";
    if (p.executive_report) {
      out += "WHY SELECTED:\n" + p.executive_report.why_selected.join("\n") + "\n\n";
      out += "RISKS:\n" + p.executive_report.key_risks.join("\n") + "\n\n";
      out += "MONITOR:\n" + p.executive_report.events_to_monitor.join("\n") + "\n\n";
    }
    out += (p.allocations || []).map((a) =>
      `#${a.purchase_order} ${a.ticker} [${a.instrument}] $${a.allocation_usd} — ${a.rationale}`
    ).join("\n");
    $("#proposal-out").textContent = out;
    toast("Proposal ready");
  } catch (e) { toast("Proposal: " + e.message); }
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

loadDashboard();
