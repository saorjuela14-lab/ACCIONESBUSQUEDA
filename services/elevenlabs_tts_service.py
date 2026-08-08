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

    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id)

    def status(self) -> dict:
        settings = get_settings()
        return {
            "configured": self.configured(),
            "provider": "elevenlabs",
            "voice_id": settings.elevenlabs_voice_id or None,
            "model_id": settings.elevenlabs_model_id,
            "fallback": "browser_speech_synthesis",
        }

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (audio_bytes, content_type)."""
        settings = get_settings()
        api_key = (settings.elevenlabs_api_key or "").strip()
        voice_id = (settings.elevenlabs_voice_id or "").strip()
        if not api_key or not voice_id:
            raise ElevenLabsTTSError("ElevenLabs no está configurado (ELEVENLABS_API_KEY / VOICE_ID).")

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
