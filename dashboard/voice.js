/**
 * Monarch Capital voice assistant —
 * STT: Web Speech API (+ text fallback on iOS)
 * TTS: ElevenLabs (friendly secretary / Friday voice) with browser SpeechSynthesis fallback
 */
(function () {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const isTouch = "ontouchstart" in window || navigator.maxTouchPoints > 0;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  let deps = null;
  let recognition = null;
  let listening = false;
  let gotResult = false;
  let synth = window.speechSynthesis;
  let elevenConfigured = null; // null = unknown, true/false after probe
  let currentAudio = null;
  let chatSessionId = localStorage.getItem("monarch_voice_session") || "";
  let assistantName = "Viernes";

  function ensureSessionId() {
    if (!chatSessionId) {
      chatSessionId = (crypto.randomUUID && crypto.randomUUID())
        || `vs_${Date.now()}_${Math.random().toString(16).slice(2)}`;
      localStorage.setItem("monarch_voice_session", chatSessionId);
    }
    return chatSessionId;
  }

  function appendTranscript(role, text) {
    const box = $("#voice-transcript");
    if (!box || !text) return;
    const div = document.createElement("div");
    div.className = `voice-bubble ${role}`;
    const who = role === "user" ? "Tú" : assistantName;
    div.innerHTML = `<span class="who">${who}</span>${escapeHtmlVoice(text)}`;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    while (box.children.length > 24) box.removeChild(box.firstChild);
  }

  function escapeHtmlVoice(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function $(s) { return document.querySelector(s); }

  function setStatus(text, active) {
    const el = $("#voice-status");
    const btn = $("#btn-voice");
    if (el) el.textContent = text;
    if (btn) btn.classList.toggle("voice-active", !!active);
  }

  function unlockSpeech() {
    if (!synth) return;
    try {
      synth.resume();
      const u = new SpeechSynthesisUtterance(" ");
      u.volume = 0.01;
      synth.speak(u);
    } catch { /* ignore */ }
  }

  function pickSpanishVoice() {
    if (!synth) return null;
    const voices = synth.getVoices();
    return voices.find((v) => v.lang.startsWith("es-MX"))
      || voices.find((v) => v.lang.startsWith("es-ES"))
      || voices.find((v) => v.lang.startsWith("es"))
      || null;
  }

  function stopAudio() {
    if (currentAudio) {
      try {
        currentAudio.pause();
        currentAudio.src = "";
      } catch { /* ignore */ }
      currentAudio = null;
    }
    if (synth) {
      try { synth.cancel(); } catch { /* ignore */ }
    }
  }

  function speakBrowser(text, onEnd) {
    if (!synth) {
      deps?.toast?.(text.slice(0, 120));
      if (onEnd) onEnd();
      return;
    }
    synth.cancel();
    synth.resume();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "es-MX";
    u.rate = 0.95;
    const voice = pickSpanishVoice();
    if (voice) u.voice = voice;
    u.onend = () => { if (onEnd) onEnd(); };
    u.onerror = () => { if (onEnd) onEnd(); };
    synth.speak(u);
  }

  async function probeElevenStatus() {
    if (!deps?.API) return false;
    try {
      const token = localStorage.getItem("nexbuy_token");
      const headers = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const r = await fetch(`${deps.API}/voice/tts/status`, { headers });
      if (!r.ok) return false;
      const data = await r.json();
      return !!data.configured;
    } catch {
      return false;
    }
  }

  async function speakEleven(text, onEnd) {
    const token = localStorage.getItem("nexbuy_token");
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;

    const r = await fetch(`${deps.API}/voice/tts`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text }),
    });
    if (r.status === 401) {
      localStorage.removeItem("nexbuy_token");
      location.href = "/login";
      throw new Error("Sesión expirada");
    }
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText || "TTS falló");
    }

    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    stopAudio();

    await new Promise((resolve) => {
      const audio = new Audio(url);
      currentAudio = audio;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        URL.revokeObjectURL(url);
        if (currentAudio === audio) currentAudio = null;
        resolve();
      };
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(finish);
    });
    if (onEnd) onEnd();
  }

  async function speak(text, onEnd) {
    if (!text) {
      if (onEnd) onEnd();
      return;
    }
    stopAudio();

    if (elevenConfigured === null) {
      elevenConfigured = await probeElevenStatus();
    }

    if (elevenConfigured && deps?.API) {
      try {
        await speakEleven(text, onEnd);
        return;
      } catch (e) {
        // One soft retry after re-probe; then browser fallback
        elevenConfigured = await probeElevenStatus();
        if (elevenConfigured) {
          try {
            await speakEleven(text, onEnd);
            return;
          } catch { /* fall through */ }
        }
      }
    }

    speakBrowser(text, onEnd);
  }

  // Shared entry for app.js analysis narration
  window.speakAssistant = speak;

  const ERROR_ES = {
    "not-allowed": "Permiso de micrófono denegado. Actívalo en ajustes del navegador.",
    "no-speech": "No escuché nada. Mantén pulsado el micrófono y habla cerca.",
    "network": "Reconocimiento de voz requiere internet.",
    "aborted": "",
    "audio-capture": "No encuentro el micrófono.",
    "service-not-allowed": "El navegador bloqueó el micrófono en esta página.",
  };

  async function dispatchUiAction(action) {
    if (!action || !deps) return;
    const [cmd, arg] = action.includes(":") ? action.split(/:(.+)/) : [action, ""];

    switch (cmd) {
      case "refresh":
        await deps.loadDashboard();
        if (deps.loadAlpacaBook) {
          try { await deps.loadAlpacaBook(); } catch { /* ignore */ }
        }
        break;
      case "ticker":
        if (arg && $("#global-ticker")) {
          $("#global-ticker").value = arg;
          if (deps.focusTechView) deps.focusTechView({ force: true });
        }
        break;
      case "analyze":
        if (arg) {
          $("#global-ticker").value = arg;
          if (deps.focusTechView) deps.focusTechView({ force: true });
          await deps.runAnalyze();
          if (deps.speakAnalyzeResult) await deps.speakAnalyzeResult(arg);
        }
        break;
      case "scroll": {
        const el = document.getElementById(arg);
        el?.scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      }
      case "discovery":
        deps.switchToTab("discovery");
        if (arg && $("#disc-themes")) {
          $("#disc-themes").value = arg;
          await deps.runDiscoveryResearch();
        }
        break;
      default:
        break;
    }
  }

  async function handleTranscript(text) {
    const trimmed = (text || "").trim();
    if (!trimmed) {
      speak("No te escuché, jefe. Prueba de nuevo.");
      return;
    }

    appendTranscript("user", trimmed);
    setStatus(`Viernes escucha: "${trimmed}"…`, true);
    try {
      const body = {
        text: trimmed,
        session_id: ensureSessionId(),
      };
      if (deps.getPortfolioId()) body.portfolio_id = deps.getPortfolioId();
      const result = await deps.api(`${deps.API}/voice/chat`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (result.session_id) {
        chatSessionId = result.session_id;
        localStorage.setItem("monarch_voice_session", chatSessionId);
      }
      if (result.assistant_name) {
        assistantName = result.assistant_name;
        const nameEl = $("#voice-assistant-name");
        if (nameEl) nameEl.textContent = assistantName;
      }
      appendTranscript("assistant", result.speech || "");

      if (result.requires_confirmation) {
        setStatus("Esperando: di «confirma» o «cancela», jefe", true);
        deps.toast("Viernes: orden pendiente — di confirma o cancela", 6000);
      } else {
        const mode = result.mode === "chat" ? "conversación" : (result.mode || "listo");
        setStatus(result.success ? `${assistantName} · ${mode}` : "Sin resultado", false);
      }

      const actions = Array.isArray(result.ui_actions) && result.ui_actions.length
        ? result.ui_actions
        : (result.ui_action ? [result.ui_action] : []);

      speak(result.speech, async () => {
        for (const action of actions) {
          await dispatchUiAction(action);
        }
      });
    } catch (e) {
      setStatus("Error", false);
      const msg = "Perdón jefe, hubo un error procesando eso.";
      appendTranscript("assistant", msg);
      speak(msg);
      deps.toast("Voz: " + e.message);
    }
  }

  function stopListening() {
    if (recognition && listening) {
      try { recognition.stop(); } catch { /* ignore */ }
    }
  }

  function startListening() {
    if (!SpeechRecognition) return;
    if (listening) return;

    gotResult = false;
    unlockSpeech();

    recognition = new SpeechRecognition();
    recognition.lang = "es-MX";
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => {
      listening = true;
      setStatus(isTouch ? "Mantén pulsado y habla…" : "Escuchando…", true);
    };

    recognition.onend = () => {
      listening = false;
      if (!gotResult) {
        setStatus("Toca o mantén 🎙 y habla", false);
      }
    };

    recognition.onerror = (ev) => {
      listening = false;
      const msg = ERROR_ES[ev.error] || `Error micrófono: ${ev.error}`;
      setStatus("Toca o mantén 🎙 y habla", false);
      if (msg) {
        deps.toast(msg);
        speak(msg);
      }
    };

    recognition.onresult = (ev) => {
      let interim = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (interim) setStatus(`…${interim}`, true);
      if (finalText) {
        gotResult = true;
        setStatus(`"${finalText.trim()}"`, false);
        handleTranscript(finalText.trim());
      }
    };

    try {
      recognition.start();
    } catch (e) {
      deps.toast("No se pudo iniciar el micrófono. Usa el campo de texto.");
    }
  }

  function bindMicButton(btn) {
    if (!SpeechRecognition) return;

    if (isTouch) {
      btn.title = "Mantén pulsado y habla";
      const onDown = (e) => {
        e.preventDefault();
        unlockSpeech();
        startListening();
      };
      const onUp = (e) => {
        e.preventDefault();
        setTimeout(stopListening, 400);
      };
      btn.addEventListener("touchstart", onDown, { passive: false });
      btn.addEventListener("touchend", onUp, { passive: false });
      btn.addEventListener("mousedown", onDown);
      btn.addEventListener("mouseup", onUp);
      btn.addEventListener("mouseleave", () => { if (listening) stopListening(); });
    } else {
      btn.title = "Clic para hablar";
      btn.onclick = () => {
        unlockSpeech();
        if (listening) stopListening();
        else startListening();
      };
    }
  }

  function bindTextFallback() {
    const input = $("#voice-text-input");
    const sendBtn = $("#btn-voice-send");
    if (!input) return;

    const submit = () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      unlockSpeech();
      handleTranscript(text);
    };

    sendBtn?.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
  }

  window.initVoiceModule = function initVoiceModule(options) {
    deps = options;
    const btn = $("#btn-voice");
    if (!btn) return;

    bindTextFallback();

    if (synth) {
      synth.getVoices();
      synth.addEventListener("voiceschanged", () => {}, { once: true });
    }

    // Probe ElevenLabs + assistant status in background
    probeElevenStatus().then((ok) => { elevenConfigured = ok; });
    ensureSessionId();
    (async () => {
      try {
        const token = localStorage.getItem("nexbuy_token");
        const headers = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        const r = await fetch(`${deps.API}/voice/assistant/status`, { headers });
        if (!r.ok) return;
        const st = await r.json();
        if (st.assistant_name) {
          assistantName = st.assistant_name;
          const nameEl = $("#voice-assistant-name");
          if (nameEl) nameEl.textContent = assistantName;
        }
        if (!st.openai_configured) {
          setStatus("Viernes en modo comandos (falta OPENAI_API_KEY para chat libre)", false);
        }
      } catch { /* ignore */ }
    })();

    if (!SpeechRecognition || isIOS) {
      const hint = isIOS
        ? "iPhone: escribe a Viernes abajo. Ej: jefe, ¿cómo está el mercado?"
        : "Escribe a Viernes. Ej: simula 3200 al 12% · cambia la estrategia · precio NVDA";
      setStatus(hint, false);
      btn.disabled = true;
      btn.title = "Micrófono no disponible — usa el campo de texto";
      $("#voice-text-input")?.focus();
      return;
    }

    bindMicButton(btn);
    setStatus(isTouch ? "Mantén pulsado 🎙 — habla con Viernes" : "Clic en 🎙 — habla con Viernes", false);
  };
})();
