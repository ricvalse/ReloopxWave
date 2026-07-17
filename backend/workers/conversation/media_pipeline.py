"""Concrete inbound-media pipeline for the worker.

Implements the `ai_core.conversation_service.MediaPipeline` protocol: downloads
WhatsApp media via the per-merchant 360dialog client, enforces size/MIME policy,
stores the bytes in the private `whatsapp-media` Supabase bucket, transcribes
voice notes with Whisper, and mints vision `ImagePart`s for the reply path.

Dependency inversion (mirrors `WhatsAppReplySender`): ai_core stays unaware of
360dialog / Supabase Storage / OpenAI; this module wires them together.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from ai_core.llm import ImagePart
from integrations import build_whatsapp_sender
from integrations.supabase_storage import SupabaseStorage
from integrations.whatsapp.media import (
    ext_for_mime,
    is_allowed_mime,
    is_vision_mime,
    max_bytes_for_kind,
    normalize_mime,
)
from shared import Settings, get_logger

logger = get_logger(__name__)


class WhatsAppMediaPipeline:
    """Download → policy-check → store → (audio) transcribe. Never raises."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _storage(self) -> SupabaseStorage:
        return SupabaseStorage(
            project_url=self._settings.supabase_url,
            service_role_key=self._settings.supabase_service_role_key,
            bucket=self._settings.supabase_media_bucket,
        )

    async def fetch_and_store(
        self,
        *,
        api_key: str,
        waba_base_url: str | None,
        phone_number_id: str,
        merchant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        media_id: str,
        kind: str,
        mime: str | None,
    ) -> dict[str, Any]:
        """Return a `meta.media` patch: on success `{storage_path, mime,
        size_bytes[, transcription]}`; on failure `{error}`. Best-effort."""
        sender = build_whatsapp_sender(
            phone_number_id=phone_number_id,
            api_key=api_key,
            waba_base_url=waba_base_url,
        )
        try:
            downloaded = await sender.download_media(media_id=media_id)
        except Exception as e:
            logger.warning("media.download_error", media_id=media_id, error=str(e))
            return {"error": "download_failed"}
        finally:
            await sender.close()

        if downloaded is None:
            return {"error": "download_failed"}
        data, resp_mime = downloaded
        # Prefer the response content-type, fall back to the webhook-declared mime.
        effective_mime = normalize_mime(resp_mime) or normalize_mime(mime)

        if len(data) > max_bytes_for_kind(kind):
            logger.info("media.too_large", media_id=media_id, size=len(data), kind=kind)
            return {"error": "too_large"}
        if not is_allowed_mime(effective_mime):
            logger.info("media.unsupported_mime", media_id=media_id, mime=effective_mime)
            return {"error": "unsupported_mime"}

        storage_path = f"{merchant_id}/{conversation_id}/{message_id}{ext_for_mime(effective_mime)}"
        try:
            await self._storage().upload_bytes(
                storage_path, data, content_type=effective_mime or "application/octet-stream"
            )
        except Exception as e:
            logger.warning("media.upload_error", media_id=media_id, error=str(e))
            return {"error": "storage_failed"}

        patch: dict[str, Any] = {
            "storage_path": storage_path,
            "mime": effective_mime,
            "size_bytes": len(data),
        }
        if kind == "audio":
            transcription = await self._transcribe(data, effective_mime)
            if transcription:
                patch["transcription"] = transcription
        logger.info(
            "media.stored",
            media_id=media_id,
            kind=kind,
            size_kb=len(data) // 1024,
            transcribed=("transcription" in patch),
        )
        return patch

    async def load_image(self, *, storage_path: str, mime: str | None) -> ImagePart | None:
        """Load a stored image and return it as a base64 vision part, or None."""
        if not is_vision_mime(mime):
            return None
        try:
            data = await self._storage().download(storage_path)
        except Exception as e:
            logger.warning("media.load_error", storage_path=storage_path, error=str(e))
            return None
        return ImagePart(
            mime=normalize_mime(mime) or "image/jpeg",
            b64=base64.standard_b64encode(data).decode("ascii"),
        )

    async def _transcribe(self, data: bytes, mime: str) -> str | None:
        """Transcribe a voice note with Whisper (Italian). Best-effort → None."""
        if not self._settings.openai_api_key:
            return None
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._settings.openai_api_key, timeout=60.0)
            ext = ext_for_mime(mime).lstrip(".") or "ogg"
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=(f"audio.{ext}", data, mime or "audio/ogg"),
                language="it",
            )
            text = getattr(resp, "text", None)
            return text.strip() if text else None
        except Exception as e:
            logger.warning("media.transcription_failed", error=str(e))
            return None
