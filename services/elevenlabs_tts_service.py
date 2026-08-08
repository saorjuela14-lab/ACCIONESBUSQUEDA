"""ElevenLabs text-to-speech for the Monarch voice assistant."""

from __future__ import annotations

import httpx

from config.settings import get_settings
from utils.logging import get_logger

logger = get_logger(__name__)

ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTSError(RuntimeError):
    """Raised when ElevenLabs TTS fails."""


class ElevenLabsTTSService:
    """Friendly desk-assistant voice (secretary / Friday style)."""

    def _api_key(self) -> str:
        return (get_settings().elevenlabs_api_key or "").strip()

    def key_looks_valid(self) -> bool:
        """ElevenLabs secret keys start with sk_. Key IDs are hex and must not be used."""
        key = self._api_key()
        return key.startswith("sk_") and len(key) > 20

    def configured(self) -> bool:
        settings = get_settings()
        return bool(self.key_looks_valid() and settings.elevenlabs_voice_id)

    def status(self) -> dict:
        settings = get_settings()
        key = self._api_key()
        key_present = bool(key)
        key_ok = self.key_looks_valid()
        hint = None
        if key_present and not key_ok:
            hint = (
                "La variable parece un Key ID, no la API key. "
                "En elevenlabs.io crea/rota una key que empiece por sk_ y pégala en ELEVENLABS_API_KEY."
            )
        elif not key_present:
            hint = "Define ELEVENLABS_API_KEY (sk_...) en el entorno."
        return {
            "configured": self.configured(),
            "provider": "elevenlabs",
            "voice_id": settings.elevenlabs_voice_id or None,
            "model_id": settings.elevenlabs_model_id,
            "fallback": "browser_speech_synthesis",
            "key_present": key_present,
            "key_format_ok": key_ok,
            "hint": hint,
        }

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (audio_bytes, content_type)."""
        settings = get_settings()
        api_key = self._api_key()
        voice_id = (settings.elevenlabs_voice_id or "").strip()
        if not api_key or not voice_id:
            raise ElevenLabsTTSError("ElevenLabs no está configurado (ELEVENLABS_API_KEY / VOICE_ID).")
        if not self.key_looks_valid():
            raise ElevenLabsTTSError(
                "ELEVENLABS_API_KEY inválida: parece un Key ID. "
                "Usa la key secreta que empieza por sk_ (Create/Rotate en elevenlabs.io)."
            )

        clean = (text or "").strip()
        if not clean:
            raise ElevenLabsTTSError("Texto vacío para TTS.")
        # Keep latency bounded for voice-bar replies
        if len(clean) > 2500:
            clean = clean[:2497] + "…"

        url = ELEVEN_TTS_URL.format(voice_id=voice_id)
        headers = {
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        payload = {
            "text": clean,
            "model_id": settings.elevenlabs_model_id,
            "voice_settings": {
                "stability": settings.elevenlabs_stability,
                "similarity_boost": settings.elevenlabs_similarity,
                "style": settings.elevenlabs_style,
                "use_speaker_boost": True,
            },
        }
        params = {"output_format": settings.elevenlabs_output_format}

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, headers=headers, json=payload, params=params)
        except httpx.HTTPError as exc:
            logger.warning("elevenlabs.tts.network_error", error=str(exc))
            raise ElevenLabsTTSError(f"Error de red con ElevenLabs: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:240]
            logger.warning(
                "elevenlabs.tts.http_error",
                status=resp.status_code,
                detail=detail,
            )
            raise ElevenLabsTTSError(f"ElevenLabs HTTP {resp.status_code}: {detail}")

        audio = resp.content
        if not audio:
            raise ElevenLabsTTSError("ElevenLabs devolvió audio vacío.")
        content_type = resp.headers.get("content-type", "audio/mpeg")
        return audio, content_type
