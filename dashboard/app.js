const API = "/api/v1";

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return document.querySelectorAll(sel); }

function toast(msg, ms = 4000) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function ticker() {
  return ($("#global-ticker").value || "ABBV").trim().toUpperCase();
}

// Tabs
$all(".nav").forEach((btn) => {
  btn.addEventListener("click", () => {
    $all(".nav").forEach((b) => b.classList.remove("active"));
    $all(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  });
});

async function loadProviders() {
  try {
    const s = await api("/api/v1/providers/status");
    const poly = s.providers?.polygon?.authenticated;
    $("#provider-status").textContent = poly ? "Providers OK" : "Providers partial";
    $("#provider-status").classList.add(poly ? "ok" : "");
  } catch {
    $("#provider-status").textContent = "API offline";
    $("#provider-status").classList.add("err");
  }
}

function renderAgents(reports) {
  const el = $("#ov-agents");
  el.innerHTML = reports.map((r) => {
    const cls = r.score >= 0 ? "pos" : "neg";
    return `<div class="agent-chip"><span>${r.agent_name.replace("_agent", "")}</span><span class="score ${cls}">${r.score >= 0 ? "+" : ""}${r.score?.toFixed(1)}</span></div>`;
  }).join("");
}

async function runAnalyze(t) {
  toast(`Analizando ${t}… (puede tardar ~1 min)`);
  $("#ov-summary").textContent = "Analizando…";
  $("#analyze-output").innerHTML = '<span class="loading">Ejecutando comité de inversión…</span>';

  const thesis = await api(`${API}/analyze`, {
    method: "POST",
    body: JSON.stringify({ ticker: t }),
  });

  $("#ov-rec").textContent = (thesis.recommendation || "—").toUpperCase();
  $("#ov-conf").textContent = thesis.confidence ? `${(thesis.confidence * 100).toFixed(0)}%` : "—";
  $("#ov-summary").textContent = thesis.executive_summary || "—";

  const news = (thesis.agent_reports || []).find((r) => r.agent_name === "news_agent");
  const sent = (thesis.agent_reports || []).find((r) => r.agent_name === "sentiment_agent");
  if (news?.raw_data) {
    $("#ov-news").textContent = news.score != null ? `${news.score >= 0 ? "+" : ""}${news.score.toFixed(1)}` : "—";
    $("#ov-impact").textContent = news.raw_data.investment_impact || news.raw_data.actualidad_summary || "—";
  }
  if (sent) $("#ov-sent").textContent = `${sent.score >= 0 ? "+" : ""}${sent.score?.toFixed(1)}`;

  renderAgents(thesis.agent_reports || []);

  let html = `<strong>${thesis.ticker}</strong> — ${thesis.recommendation?.toUpperCase()} @ ${(thesis.confidence * 100).toFixed(0)}%\n\n`;
  html += thesis.executive_summary + "\n\n";
  html += "— Tesis —\n" + (thesis.investment_thesis || "") + "\n\n";

  for (const r of thesis.agent_reports || []) {
    if (r.agent_name === "news_agent" && r.raw_data) {
      html += "— NOTICIAS 2Y —\n" + (r.raw_data.two_year_summary || "") + "\n\n";
      html += "— NOTICIAS 3M —\n" + (r.raw_data.three_month_summary || "") + "\n\n";
      html += "— IMPACTO —\n" + (r.raw_data.investment_impact || "") + "\n\n";
    }
    if (r.agent_name === "market_dependency_agent" && r.raw_data) {
      html += "— CORRELACIONES —\n" + (r.summary || "") + "\n\n";
    }
  }
  $("#analyze-output").textContent = html;
  toast(`${t} listo`);
  return thesis;
}

$("#btn-quick-analyze").addEventListener("click", async () => {
  try {
    await runAnalyze(ticker());
  } catch (e) {
    toast("Error: " + e.message);
  }
});

$("#btn-correlations").addEventListener("click", async () => {
  const t = ticker();
  $("#corr-output").innerHTML = '<span class="loading">Calculando correlaciones…</span>';
  try {
    const c = await api(`${API}/correlations/${t}`);
    let html = `<strong>${c.ticker}</strong> — ${c.sector || ""} / ${c.industry || ""}\n\n`;
    html += c.summary + "\n\n";
    html += "— Benchmarks —\n";
    html += (c.benchmark_correlations || []).slice(0, 8).map((p) =>
      `  ${p.ticker}: ${p.correlation >= 0 ? "+" : ""}${p.correlation} — ${p.interpretation}`
    ).join("\n") + "\n\n";
    html += "— Macro / Geopolítica —\n";
    html += (c.macro_sensitivities || []).map((m) =>
      `  [${m.sensitivity}] ${m.factor} (${m.proxy_ticker})${m.correlation != null ? ` corr ${m.correlation}` : ""}\n    Escenario: ${m.scenario}\n    Impacto: ${m.impact_if_shock}`
    ).join("\n\n") + "\n\n";
    html += "— Empresas vinculadas —\n";
    html += (c.company_dependencies || []).map((d) =>
      `  ${d.ticker} (${d.relationship})${d.correlation != null ? ` corr ${d.correlation}` : ""}: ${d.why_it_matters}`
    ).join("\n") + "\n\n";
    html += "— EM —\n" + c.emerging_market_exposure;
    $("#corr-output").textContent = html;
  } catch (e) {
    $("#corr-output").textContent = "Error: " + e.message;
  }
});

$("#btn-proposal").addEventListener("click", async () => {
  $("#proposal-output").innerHTML = '<span class="loading">Generando propuesta (analiza cada ticker)…</span>';
  const tickersRaw = $("#prop-tickers").value.trim();
  const body = {
    budget: parseFloat($("#prop-budget").value) || 50,
    tickers: tickersRaw ? tickersRaw.split(",").map((s) => s.trim().toUpperCase()) : null,
    use_watchlist: $("#prop-watchlist").checked,
    instrument_mode: $("#prop-instrument").value,
    risk_profile: $("#prop-risk").value,
    cfd_margin_pct: $("#prop-margin").value ? parseFloat($("#prop-margin").value) : null,
  };
  try {
    const p = await api(`${API}/proposal`, { method: "POST", body: JSON.stringify(body) });
    let html = p.summary + "\n\n" + p.instrument_summary + "\n\n";
    if (p.warnings?.length) html += "⚠ " + p.warnings.join("\n⚠ ") + "\n\n";
    if (p.total_margin_required) html += `Margen CFD total: $${p.total_margin_required}\n\n`;
    html += "— Asignaciones —\n";
    html += (p.allocations || []).map((a) =>
      `${a.ticker} [${a.instrument.toUpperCase()}] $${a.allocation_usd} (${a.allocation_pct}%) — ${a.recommendation}\n  ${a.rationale}`
    ).join("\n\n");
    html += `\n\nEfectivo sin asignar: $${p.unallocated_cash}`;
    $("#proposal-output").textContent = html;
    toast("Propuesta generada");
  } catch (e) {
    $("#proposal-output").textContent = "Error: " + e.message;
  }
});

async function refreshWatchlist() {
  const items = await api(`${API}/watchlist`);
  $("#watchlist-output").innerHTML = items.length
    ? `<table class="table"><tr><th>Ticker</th><th>Empresa</th><th>Notas</th></tr>${
        items.map((w) => `<tr><td>${w.ticker}</td><td>${w.company_name || "—"}</td><td>${w.notes || ""}</td></tr>`).join("")
      }</table>`
    : "Watchlist vacía.";
}

$("#btn-wl-add").addEventListener("click", async () => {
  const t = $("#wl-ticker").value.trim().toUpperCase();
  if (!t) return;
  try {
    await api(`${API}/watchlist`, { method: "POST", body: JSON.stringify({ ticker: t }) });
    $("#wl-ticker").value = "";
    await refreshWatchlist();
    toast(`${t} añadido`);
  } catch (e) { toast(e.message); }
});

$("#btn-wl-scan").addEventListener("click", async () => {
  try {
    const r = await api(`${API}/watchlist/scan`, { method: "POST" });
    toast(`Scan: ${r.scanned} tickers, ${r.alerts} alertas`);
  } catch (e) { toast(e.message); }
});

$("#btn-alerts-refresh").addEventListener("click", async () => {
  try {
    const alerts = await api(`${API}/alerts`);
    $("#alerts-output").innerHTML = alerts.length
      ? alerts.map((a) => `[${a.severity}] ${a.ticker}: ${a.message}`).join("\n")
      : "Sin alertas activas.";
  } catch (e) { $("#alerts-output").textContent = e.message; }
});

loadProviders();
refreshWatchlist().catch(() => {});
