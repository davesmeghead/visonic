"""Dedicated "Visonic Cameras" media source exposing saved PIR camera captures, grouped per camera."""

from __future__ import annotations

import mimetypes
import os

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
    async_resolve_media as _resolve_media,
)
from homeassistant.core import HomeAssistant

from .const import CONF_IMAGE_MEDIA_PATH, DEFAULT_IMAGE_MEDIA_PATH, DOMAIN

_IMAGE_EXT = (".gif", ".jpg", ".jpeg", ".png")


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
        return await self.hass.async_add_executor_job(self._browse, base, parts)

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
                elif name.lower().endswith(_IMAGE_EXT):
                    children.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=child_ident,
                            media_class=MediaClass.IMAGE,
                            media_content_type=mimetypes.guess_type(name)[0] or "image/gif",
                            title=name,
                            can_play=True,
                            can_expand=False,
                        )
                    )
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
