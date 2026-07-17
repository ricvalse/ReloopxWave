"""WhatsApp inbound media helpers — MIME↔extension map, kind resolution and
size/type policy.

Shared by the 360dialog client (download), the worker (store), the storage
bucket (migration 0046) and the tests. Kept provider-agnostic so a second BSP
can reuse the same policy. Mirrors Amalia's `storage.py` MIME map, minus the
flat-key sprawl (`services/backend/app/services/storage.py:11-31`).
"""

from __future__ import annotations

import mimetypes

# WhatsApp message `type` values that carry a downloadable media object. Text /
# interactive / location / contacts are handled elsewhere (no binary).
MEDIA_KINDS: frozenset[str] = frozenset({"image", "audio", "video", "document", "sticker"})

# Media the bot can't meaningfully act on → hand straight to a human (the file
# is still downloaded and shown in the inbox). Lighter media (image/audio/…)
# still get a bot reply. Matches Amalia's policy (`whatsapp.py:892-913`).
HANDOFF_KINDS: frozenset[str] = frozenset({"video", "document"})

# MIME → file extension. Explicit map for the common WhatsApp types so keys stay
# stable (`mimetypes.guess_extension` is locale-dependent for a few of these);
# fall back to the stdlib guess, then `.bin`.
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "audio/ogg": ".ogg",
    "audio/ogg; codecs=opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/amr": ".amr",
    "application/pdf": ".pdf",
}

# Allowed inbound MIME types. The Supabase bucket (migration 0046) enforces the
# same set at the storage layer; we screen early so a rejected type gets a
# legible `meta.media.error = "unsupported_mime"` instead of an opaque 400.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "video/mp4",
        "video/3gpp",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/amr",
        "application/pdf",
    }
)

# Only images are handed to the model as a vision block (Amalia does the same —
# `agent.py:453`). Everything else degrades to text (caption / transcription /
# placeholder).
VISION_MIME_PREFIX = "image/"

# Per-file byte ceilings. WhatsApp's own limits are ~16 MB media / 100 MB docs;
# we cap below the 20 MB Supabase bucket limit (migration 0046) so the storage
# layer never rejects an upload we accepted. Oversize media → `error="too_large"`,
# no bytes stored (unlike Amalia, which buffers unbounded — `storage.py` has no
# size guard at all).
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def normalize_mime(mime: str | None) -> str:
    """Strip parameters (`; codecs=opus`) and lowercase. `None`→`""`."""
    if not mime:
        return ""
    return mime.split(";", 1)[0].strip().lower()


def ext_for_mime(mime: str | None) -> str:
    """File extension (with leading dot) for a MIME type, `.bin` fallback."""
    if not mime:
        return ".bin"
    raw = mime.strip().lower()
    if raw in _MIME_TO_EXT:
        return _MIME_TO_EXT[raw]
    base = normalize_mime(mime)
    if base in _MIME_TO_EXT:
        return _MIME_TO_EXT[base]
    guessed = mimetypes.guess_extension(base) if base else None
    return guessed or ".bin"


def is_allowed_mime(mime: str | None) -> bool:
    return normalize_mime(mime) in ALLOWED_MIME_TYPES


def is_vision_mime(mime: str | None) -> bool:
    return normalize_mime(mime).startswith(VISION_MIME_PREFIX)


def max_bytes_for_kind(kind: str) -> int:
    return MAX_DOCUMENT_BYTES if kind == "document" else MAX_MEDIA_BYTES
