(() => {
  const API = "/api/v1";
  let desk = "gold";
  let statusCache = null;

  function token() {
    return localStorage.getItem("nexbuy_token") || localStorage.getItem("monarch_token") || "";
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    if (res.status === 401) {
      location.href = "/login";
      throw new Error("Sesión expirada");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText || "Error");
    return data;
  }

  function $(sel) { return document.querySelector(sel); }
  function money(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return `$${Number(v).toLocaleString("es-CO", { maximumFractionDigits: 2 })}`;
  }

  function fillSymbolSelects(strategy) {
    const opts = (strategy.symbols || []).map(
      (s) => `<option value="${s.symbol}">${s.symbol} — ${s.label}</option>`
    ).join("");
    $("#brief-symbol").innerHTML = opts;
    $("#order-symbol").innerHTML = opts;
  }

  function renderUniverse(st) {
    const quotes = st.quotes || {};
    const el = $("#universe");
    el.innerHTML = (st.strategy.symbols || []).map((s) => {
      const q = quotes[s.symbol] || {};
      const px = q.current_price != null ? money(q.current_price) : "—";
      return `<div class="ma-card"><div><div class="sym">${s.symbol}</div><div class="ma-muted">${s.label} · ${s.notes || ""}</div></div><div>${px}</div></div>`;
    }).join("") || "Sin símbolos";
  }

  function renderBook(st) {
    const pos = st.positions || [];
    const orders = st.open_orders || [];
    $("#book-out").textContent =
      `Posiciones (${pos.length})\n` +
      (pos.map((p) => `${p.symbol} qty=${p.qty} mv=${p.market_value} pnl=${p.unrealized_pl}`).join("\n") || "(vacío)") +
      `\n\nÓrdenes abiertas (${orders.length})\n` +
      (orders.map((o) => `${o.symbol} ${o.side} ${o.qty || o.notional} ${o.status}`).join("\n") || "(vacío)");
  }

  async function loadStatus() {
    const st = await api(`${API}/beta/multiasset/${desk}/status`);
    statusCache = st;
    $("#desk-title").textContent = st.strategy?.name || desk;
    $("#desk-thesis").textContent = st.strategy?.thesis || "";
    $("#desk-broker").textContent = st.broker_message || "";
    $("#desk-disclaimer").textContent = st.strategy?.disclaimer || "";
    $("#kpi-equity").textContent = money(st.equity);
    $("#kpi-cash").textContent = money(st.cash);
    $("#kpi-pos").textContent = String((st.positions || []).length);
    fillSymbolSelects(st.strategy || { symbols: [] });
    renderUniverse(st);
    renderBook(st);
  }

  async function runBrief() {
    const symbol = $("#brief-symbol").value;
    const out = $("#brief-out");
    out.textContent = "Analizando con agentes especializados…";
    try {
      const b = await api(`${API}/beta/multiasset/${desk}/brief/${encodeURIComponent(symbol)}`);
      const recClass = b.recommendation === "buy" ? "rec-buy" : b.recommendation === "sell" ? "rec-sell" : "rec-hold";
      out.innerHTML =
        `<p><span class="${recClass}">${(b.recommendation || "").toUpperCase()}</span> · score ${b.score} · conf ${(b.confidence * 100).toFixed(0)}%</p>` +
        `<p>${b.summary || ""}</p>` +
        (b.entry_hint != null ? `<p class="ma-muted">Entrada ~${money(b.entry_hint)} · stop ${money(b.stop_hint)} · target ${money(b.target_hint)}</p>` : "") +
        (b.votes || []).map((v) =>
          `<div class="vote"><strong>${v.label_es}</strong> (${v.score.toFixed?.(1) ?? v.score}) — ${v.summary}</div>`
        ).join("");
    } catch (e) {
      out.textContent = e.message || String(e);
    }
  }

  async function loadHistory() {
    const el = $("#history-out");
    el.textContent = "Cargando…";
    try {
      const r = await api(`${API}/beta/multiasset/history?desk=${desk}&limit=40`);
      const items = r.items || [];
      if (!items.length) {
        el.textContent = "Sin operaciones aún en esta mesa.";
        return;
      }
      el.innerHTML = items.map((h) =>
        `<div class="row"><b>${h.symbol}</b> ${h.side} · qty=${h.qty ?? "—"} notional=${h.notional ?? "—"} · ${h.status || ""} · ${h.created_at ? new Date(h.created_at).toLocaleString() : ""}<div class="ma-muted">${h.note || ""}</div></div>`
      ).join("");
    } catch (e) {
      el.textContent = e.message || String(e);
    }
  }

  function pct(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return `${Number(v).toFixed(1)}%`;
  }

  async function loadStats() {
    try {
      const s = await api(`${API}/beta/multiasset/track-record?desk=${desk}&window_days=90`);
      $("#st-win").textContent = pct(s.trades_win_rate_pct);
      $("#st-brief").textContent = pct(s.brief_hit_rate_pct);
      $("#st-pnl").textContent = money(s.trades_total_pnl_usd);
      $("#stats-disclaimer").textContent = s.disclaimer || "";
      const fb = $("#stats-feedback");
      fb.innerHTML = (s.feedback || []).map((t) => `<li>${t}</li>`).join("") || "<li class='ma-muted'>Sin feedback aún.</li>";
      const errs = s.error_patterns || [];
      $("#stats-errors").innerHTML = errs.length
        ? errs.map((e) =>
            `<div class="row"><b class="ma-tag-err">${e.label_es}</b> · ${e.count}` +
            (e.share_pct != null ? ` (${e.share_pct}%)` : "") +
            `<div class="ma-muted">${e.hint_es || ""}</div></div>`
          ).join("")
        : "Sin errores evaluados aún — cierra trades o espera MTM.";
      const agents = s.agents || [];
      $("#stats-agents").innerHTML = agents.length
        ? agents.map((a) =>
            `<div class="row"><b>${a.label_es}</b> · acierto ${pct(a.hit_rate_pct)} · n=${a.samples}` +
            ` <span class="ma-muted">✓${a.hits} ✗${a.misses}</span></div>`
          ).join("")
        : "Sin muestras de agentes.";
      const recent = s.recent_closed || [];
      $("#stats-trades").innerHTML = recent.length
        ? recent.map((t) => {
            const ok = t.was_correct === true ? "acierto" : t.was_correct === false ? "error" : "—";
            const cls = t.was_correct === true ? "ma-tag-ok" : t.was_correct === false ? "ma-tag-err" : "";
            return `<div class="row"><b>${t.symbol}</b> ${t.recommendation} · PnL ${t.pnl_pct != null ? t.pnl_pct.toFixed(2) + "%" : "—"} · <span class="${cls}">${ok}</span>` +
              (t.error_tag ? ` · ${t.error_tag}` : "") +
              (t.is_sim ? " · sim" : "") +
              `<div class="ma-muted">${t.eval_notes || ""}</div></div>`;
          }).join("")
        : "Sin trades cerrados.";
    } catch (e) {
      $("#stats-feedback").innerHTML = `<li>${e.message || e}</li>`;
    }
  }

  async function runEvaluate() {
    try {
      const r = await api(`${API}/beta/multiasset/evaluate`, { method: "POST", body: "{}" });
      alert(`MTM: evaluados ${r.evaluated || 0} (correctos ${r.correct || 0}, errores ${r.incorrect || 0})`);
      await loadStats();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function runAutopilot() {
    try {
      $("#desk-broker").textContent = "Autopilot multi-asset corriendo…";
      const r = await api(`${API}/beta/multiasset/autopilot/run`, { method: "POST", body: "{}" });
      if (r.skipped) {
        alert(`Autopilot omitido: ${r.skipped}`);
      } else {
        const desks = r.desks || {};
        const lines = Object.entries(desks).map(([k, v]) => {
          if (v.error) return `${k}: error ${v.error}`;
          if (v.skipped) return `${k}: ${v.skipped}`;
          let line = `${k}: +${(v.buys || []).length} buys / −${(v.sells || []).length} sells (budget $${v.budget})`;
          if (v.reason) line += ` · ${v.reason}`;
          const top = (v.scanned || []).slice(0, 3).map((s) =>
            `${s.symbol} ${s.rec || ""} score=${s.score ?? "—"}` + (s.skip ? ` (${s.skip})` : "")
          );
          if (top.length) line += `\n  ` + top.join(" | ");
          return line;
        });
        const mode = r.allocation_mode === "offhours_crypto_100" ? "Modo: 100% crypto (mercado US cerrado)\n" : "";
        alert(`${mode}Deployable $${r.deployable_usd ?? "—"}\n` + lines.join("\n"));
      }
      await loadStatus();
      await loadHistory();
      await loadStats();
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  async function submitOrder(ev) {
    ev.preventDefault();
    const msg = $("#order-msg");
    const dry = $("#order-dry").checked;
    const qtyRaw = $("#order-qty").value;
    const notionalRaw = $("#order-notional").value;
    const body = {
      desk,
      symbol: $("#order-symbol").value,
      side: $("#order-side").value,
      qty: qtyRaw ? Number(qtyRaw) : null,
      notional: notionalRaw ? Number(notionalRaw) : null,
      dry_run: dry,
      confirm: !dry,
      note: "beta UI",
    };
    if (!dry && !confirm(`¿Enviar orden PAPER ${body.side} ${body.symbol}?`)) return;
    msg.textContent = "Enviando…";
    try {
      const r = await api(`${API}/beta/multiasset/execute`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      msg.textContent = r.message || (r.ok ? "OK" : "Falló");
      await loadStatus();
      await loadHistory();
      await loadStats();
    } catch (e) {
      msg.textContent = e.message || String(e);
    }
  }

  document.querySelectorAll(".ma-tab").forEach((btn) => {
    btn.onclick = async () => {
      document.querySelectorAll(".ma-tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      desk = btn.dataset.desk;
      await loadStatus();
      await loadHistory();
      await loadStats();
    };
  });

  $("#btn-refresh").onclick = async () => { await loadStatus(); await loadHistory(); await loadStats(); };
  $("#btn-brief").onclick = runBrief;
  $("#btn-history").onclick = loadHistory;
  $("#btn-stats").onclick = loadStats;
  $("#btn-evaluate").onclick = runEvaluate;
  $("#btn-autopilot") && ($("#btn-autopilot").onclick = runAutopilot);
  $("#order-form").onsubmit = submitOrder;

  (async () => {
    if (!token()) {
      location.href = "/login";
      return;
    }
    try {
      await loadStatus();
      await loadHistory();
      await loadStats();
    } catch (e) {
      $("#desk-broker").textContent = e.message || String(e);
    }
  })();
})();
