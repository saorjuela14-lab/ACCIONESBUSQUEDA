/**
 * NexBuy voice assistant — Web Speech API + fallback texto (iOS).
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

  function speak(text, onEnd) {
    if (!text) {
      if (onEnd) onEnd();
      return;
    }
    if (!synth) {
      deps?.toast(text.slice(0, 120));
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
    const trimmed = (text || "").trim();
    if (!trimmed) {
      speak("No entendí. Prueba: cómo está el mercado.");
      return;
    }

    setStatus(`Procesando: "${trimmed}"…`, true);
    try {
      const body = { text: trimmed };
      if (deps.getPortfolioId()) body.portfolio_id = deps.getPortfolioId();
      const result = await deps.api(`${deps.API}/voice/command`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setStatus(result.success ? "Listo ✓" : "Sin resultado", false);
      speak(result.speech, async () => {
        if (result.ui_action) await dispatchUiAction(result.ui_action);
      });
    } catch (e) {
      setStatus("Error", false);
      speak("Hubo un error procesando el comando.");
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

    if (!SpeechRecognition || isIOS) {
      const hint = isIOS
        ? "iPhone: escribe el comando abajo (voz limitada en Safari). La respuesta sí se escucha."
        : "Escribe el comando abajo. Ej: cómo está el mercado";
      setStatus(hint, false);
      btn.disabled = true;
      btn.title = "Micrófono no disponible — usa el campo de texto";
      $("#voice-text-input")?.focus();
      return;
    }

    bindMicButton(btn);
    setStatus(isTouch ? "Mantén pulsado 🎙 y habla" : "Clic en 🎙 y habla", false);
  };
})();
