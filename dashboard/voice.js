/**
 * NexBuy voice assistant — Web Speech API + backend command router.
 */
(function () {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  let deps = null;
  let recognition = null;
  let listening = false;
  let synth = window.speechSynthesis;

  function $(s) { return document.querySelector(s); }

  function setStatus(text, active) {
    const el = $("#voice-status");
    const btn = $("#btn-voice");
    if (el) el.textContent = text;
    if (btn) btn.classList.toggle("voice-active", !!active);
  }

  function speak(text, onEnd) {
    if (!text || !synth) {
      if (onEnd) onEnd();
      return;
    }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "es-ES";
    u.rate = 1.0;
    u.pitch = 1.0;
    const voices = synth.getVoices();
    const es = voices.find((v) => v.lang.startsWith("es"));
    if (es) u.voice = es;
    u.onend = () => { if (onEnd) onEnd(); };
    u.onerror = () => { if (onEnd) onEnd(); };
    synth.speak(u);
  }

  async function dispatchUiAction(action) {
    if (!action || !deps) return;
    const [cmd, arg] = action.includes(":") ? action.split(/:(.+)/) : [action, ""];

    switch (cmd) {
      case "refresh":
        await deps.loadDashboard();
        break;
      case "analyze":
        if (arg) {
          $("#global-ticker").value = arg;
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
    setStatus("Procesando…", true);
    try {
      const body = { text };
      if (deps.getPortfolioId()) body.portfolio_id = deps.getPortfolioId();
      const result = await deps.api(`${deps.API}/voice/command`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setStatus(result.success ? "Listo" : "Error", false);
      speak(result.speech, async () => {
        if (result.ui_action) await dispatchUiAction(result.ui_action);
      });
    } catch (e) {
      setStatus("Error", false);
      speak("Hubo un error procesando el comando.");
      deps.toast("Voz: " + e.message);
    }
  }

  function startListening() {
    if (!SpeechRecognition) {
      deps.toast("Tu navegador no soporta reconocimiento de voz. Usa Chrome.");
      return;
    }
    if (listening) {
      recognition?.stop();
      return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "es-ES";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      listening = true;
      setStatus("Escuchando…", true);
    };
    recognition.onend = () => {
      listening = false;
      if ($("#voice-status")?.textContent === "Escuchando…") setStatus("Toca para hablar", false);
    };
    recognition.onerror = (ev) => {
      listening = false;
      setStatus("Toca para hablar", false);
      if (ev.error !== "aborted") deps.toast("Micrófono: " + ev.error);
    };
    recognition.onresult = (ev) => {
      const text = ev.results[0][0].transcript;
      setStatus(`"${text}"`, false);
      handleTranscript(text);
    };

    try {
      recognition.start();
    } catch (e) {
      deps.toast("No se pudo iniciar el micrófono");
    }
  }

  window.initVoiceModule = function initVoiceModule(options) {
    deps = options;
    const btn = $("#btn-voice");
    if (!btn) return;

    if (!SpeechRecognition) {
      setStatus("Voz no disponible", false);
      btn.disabled = true;
      return;
    }

    btn.onclick = startListening;
    setStatus("Toca para hablar", false);

    if (synth && synth.getVoices().length === 0) {
      synth.addEventListener("voiceschanged", () => {}, { once: true });
    }
  };
})();
