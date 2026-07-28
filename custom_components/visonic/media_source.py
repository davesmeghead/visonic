"""Dedicated "Visonic Cameras" media source exposing saved PIR camera captures, grouped per camera."""

from __future__ import annotations

from datetime import timedelta
import mimetypes
import os
import re

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
    async_resolve_media as _resolve_media,
)
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from .const import CONF_IMAGE_MEDIA_PATH, DEFAULT_IMAGE_MEDIA_PATH, DOMAIN

_FRAME_RE = re.compile(r"_frame\d+\.jpg$", re.I)   # the stills ffmpeg is fed, not captures in their own right
_THUMB_VALID = timedelta(days=1)                  # how long a poster URL stays signed for

_IMAGE_EXT = (".gif", ".jpg", ".jpeg", ".png")
_VIDEO_EXT = (".mp4",)
_AUDIO_EXT = (".wav",)
_MEDIA_EXT = _IMAGE_EXT + _VIDEO_EXT + _AUDIO_EXT


def _media_class(name: str) -> MediaClass:
    """Media class for a capture file: the clip, its audio, or a still."""
    lowered = name.lower()
    if lowered.endswith(_VIDEO_EXT):
        return MediaClass.VIDEO
    if lowered.endswith(_AUDIO_EXT):
        return MediaClass.MUSIC
    return MediaClass.IMAGE


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the Visonic camera-capture media source."""
    return VisonicMediaSource(hass)


class VisonicMediaSource(MediaSource):
    """Provide Visonic camera captures grouped per camera."""

    name = "Visonic Cameras"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _base_dir(self) -> str:
        """Return the directory captures are saved to (mirrors platform_manager)."""
        configured = DEFAULT_IMAGE_MEDIA_PATH
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            configured = entries[0].options.get(CONF_IMAGE_MEDIA_PATH, DEFAULT_IMAGE_MEDIA_PATH)
        if os.path.isabs(configured):
            return configured
        media_dirs = self.hass.config.media_dirs or {}
        root = media_dirs.get("local") or next(iter(media_dirs.values()), None) or self.hass.config.path("media")
        return os.path.join(root, configured)

    @staticmethod
    def _safe_parts(identifier: str | None) -> list[str]:
        """Split an identifier into safe path parts (no traversal)."""
        return [p for p in (identifier or "").split("/") if p and p not in ("..", ".")]

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse the capture folders and clips."""
        base = self._base_dir()
        parts = self._safe_parts(item.identifier)
        result = await self.hass.async_add_executor_job(self._browse, base, parts)
        for child in result.children or []:
            if child.thumbnail:
                child.thumbnail = self._sign_media_path(child.thumbnail)
        return result

    def _sign_media_path(self, abs_path: str) -> str | None:
        """Turn an on-disk media path into a signed URL the frontend may load."""
        for key, root in (self.hass.config.media_dirs or {}).items():
            rel = os.path.relpath(abs_path, root)
            if not rel.startswith(".."):
                url = f"/media/{key}/{rel.replace(os.sep, '/')}"
                return async_sign_path(self.hass, url, _THUMB_VALID)
        return None

    @staticmethod
    def _poster_for(directory: str, name: str) -> str | None:
        """Absolute path of a frame to use as a clip's poster, or None for non-clips."""
        if not name.lower().endswith(_VIDEO_EXT):
            return None
        stem = os.path.splitext(name)[0]
        frames = sorted(
            f for f in os.listdir(directory)
            if f.startswith(f"{stem}_frame") and f.lower().endswith(".jpg")
        )
        if not frames:
            return None
        # A frame from the middle of the burst says more than the first one.
        return os.path.join(directory, frames[len(frames) // 2])

    def _browse(self, base: str, parts: list[str]) -> BrowseMediaSource:
        """Build the browse tree for a directory (executor thread)."""
        ident = "/".join(parts)
        is_root = not parts
        target = os.path.join(base, *parts) if parts else base
        children: list[BrowseMediaSource] = []
        if os.path.isdir(target):
            for name in sorted(os.listdir(target), reverse=True):
                path = os.path.join(target, name)
                child_ident = f"{ident}/{name}" if ident else name
                if name.startswith((".", "@")):
                    continue          # Synology @eaDir and friends
                if os.path.isdir(path):
                    children.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=child_ident,
                            media_class=MediaClass.DIRECTORY,
                            media_content_type="",
                            title=name,
                            can_play=False,
                            can_expand=True,
                            children_media_class=MediaClass.IMAGE,
                        )
                    )
                elif name.lower().endswith(_MEDIA_EXT) and not _FRAME_RE.search(name):
                    child = BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=child_ident,
                        media_class=_media_class(name),
                        media_content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                        title=name,
                        can_play=True,
                        can_expand=False,
                    )
                    # A clip has no poster of its own, so borrow one of the frames it was built
                    # from. Stashed unsigned here because signing needs the event loop; the caller
                    # turns it into a URL.
                    if (poster := self._poster_for(target, name)) is not None:
                        child.thumbnail = poster
                    children.append(child)
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=ident,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title="Visonic Cameras" if is_root else os.path.basename(target),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY if is_root else MediaClass.IMAGE,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a capture to a playable URL via HA's local media source."""
        base = self._base_dir()
        parts = self._safe_parts(item.identifier)
        path = os.path.join(base, *parts)
        if not await self.hass.async_add_executor_job(os.path.isfile, path):
            raise Unresolvable("Capture not found")
        media_dirs = self.hass.config.media_dirs or {}
        for key, root in media_dirs.items():
            rel = os.path.relpath(path, root)
            if not rel.startswith(".."):
                uri = "media-source://media_source/" + key + "/" + rel.replace(os.sep, "/")
                return await _resolve_media(self.hass, uri)
        raise Unresolvable("Capture is outside the configured media directories")
