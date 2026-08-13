"""Helper classes for the coordinator."""

from collections import deque
from datetime import timedelta
import os
import re
import time

from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    CAMERA_CLIP_EVENT,
    CONF_IMAGE_MEDIA_PATH,
    CONF_IMAGE_QUEUE_MAX,
    CONF_IMAGE_SINGLE_FRAME,
    DEFAULT_IMAGE_MEDIA_PATH,
    DEFAULT_IMAGE_QUEUE_MAX,
    DOMAIN,
    IMAGE_DOWNLOAD_MAX,
    IMAGE_DOWNLOAD_TIMEOUT,
    IMAGE_FRAME_DURATION_MS,
    IMAGE_SEQUENCE_GAP,
    IMAGE_SEQUENCE_MAX_FRAMES,
    PANEL_ATTRIBUTE_NAME,
)
from .log_events import logEvents
from .utils import create_sensor_unique_id
from .visonic_types import ImageQueueState

CLIP_URL_VALID = timedelta(days=1)  # how long the signed clip/poster URLs stay usable
_FRAME_RE = re.compile(r"_frame\d+\.jpg$", re.IGNORECASE)  # a saved still from a capture, not a finished clip

class ImageManager:
    """Generic Image Manager."""

    def __init__(
        self,
        hass: HomeAssistant,
        panelident: int,
        entry: ConfigEntry,
        logger: logEvents,
    ) -> None:
        """Initialize the Event Logger."""
        self.hass = hass
        self.entry = entry
        self.logger = logger
        self.panel_ident = panelident
        # Latest JPEG frame served to the image entity, per camera sensor id
        self._sensor_jpeg: dict[int, bytearray] = {}
        # Buffered JPEG frames of the current capture, and per-sensor sequence bookkeeping
        self._sensor_frames: dict[int, list[bytes]] = {}
        self._sensor_seq_name: dict[int, str] = {}
        self._sensor_last_frame: dict[int, float] = {}
        self._sensor_frame_no: dict[int, int] = {}
        # The capture's audio clip (the panel sends a RIFF/WAVE clip as the last "image" of a sequence)
        self._sensor_audio: dict[int, bytes] = {}
        self._image_activity: float = 0.0
        self._image_download_start: float = 0.0
        self._image_active_sensor: int | None = None
        self._image_queue: deque[tuple[int, str | None, int]] = deque()
        # Cameras whose current capture was asked for as a single still (duration 0)
        self._sensor_stills_only: dict[int, bool] = {}


    @staticmethod
    def _is_wav(data: bytes) -> bool:
        """True if the buffer is a RIFF/WAVE clip rather than a JPEG frame."""
        return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"

    def delete_all_sensor_jpeg(self, sensor_id: int) -> None:
        """Delete all sensor JPEG frames. Leave those already on disk but clear out all data structures."""
        # Remove the sensor_id entries from all dictionaries
        for store in (self._sensor_frames, self._sensor_seq_name, self._sensor_last_frame, self._sensor_frame_no, self._sensor_jpeg, self._sensor_audio, self._sensor_stills_only):
            store.pop(sensor_id, None)
        # Remove all sensor_id entries from the queue
        self._image_queue = deque(
            item for item in self._image_queue if item[0] != sensor_id
        )
        self.logger.logstate_warning("[delete_all_sensor_jpeg] sensor id %s", sensor_id)

    def set_sensor_jpeg(self, sensor_id: int, data: bytearray | None, is_audio: bool = False) -> None:
        """Buffer a camera frame (or the capture's audio clip) and render the capture to MP4."""
        if not data:
            for store in (self._sensor_frames, self._sensor_seq_name, self._sensor_last_frame, self._sensor_frame_no, self._sensor_jpeg, self._sensor_audio):
                store.pop(sensor_id, None)
            return
        now = self._mark_image_activity(sensor_id)
        last = self._sensor_last_frame.get(sensor_id)
        self._sensor_last_frame[sensor_id] = now
        # Trust the caller's classification first: it comes from the image_id in the F4-03 header,
        # which survives damage to the payload. Sniffing RIFF only works while the magic is intact.
        is_wav = is_audio or self._is_wav(bytes(data))
        # The panel closes every capture with its audio clip, so a frame arriving after one belongs
        # to the next capture. That marker is exact, where the time gap is only a guess - and now
        # that the image count is settable a short capture finishes well inside the gap, so two
        # requests in a row would otherwise land in the same file set.
        if (sensor_id not in self._sensor_frames or last is None
                or now - last > IMAGE_SEQUENCE_GAP
                or (sensor_id in self._sensor_audio and not is_wav)):
            self._sensor_frames[sensor_id] = []
            self._sensor_seq_name[sensor_id] = time.strftime("%Y%m%d_%H%M%S")
            self._sensor_frame_no[sensor_id] = 0
            self._sensor_audio.pop(sensor_id, None)
        # The clip is IMA ADPCM WAV and arrives as the last "image" of the sequence. It is not a
        # frame, so keep it aside and re-render so the MP4 gains its audio.
        if is_wav:
            # Keep it as the end-of-capture marker either way: dropping it here is what makes the
            # next capture merge into this one. It just is not written out or muxed for a still.
            self._sensor_audio[sensor_id] = bytes(data)
            if self.stills_only(sensor_id):
                return
            frames = self._sensor_frames.get(sensor_id) or []
            if frames:
                self.hass.async_add_executor_job(
                    self._render_sensor_media, sensor_id, list(frames), frames[-1],
                    self._sensor_seq_name[sensor_id], self._sensor_frame_no[sensor_id],
                    self._camera_folder(sensor_id), bytes(data),
                )
            return
        frames = self._sensor_frames[sensor_id]
        if frames and (self.entry.options.get(CONF_IMAGE_SINGLE_FRAME, False)
                       or self.stills_only(sensor_id)):
            return
        new_frame = bytes(data)
        # Drop a frame we already hold for this capture. The panel sometimes re-sends images part
        # way through a sequence, byte for byte identical, and not always adjacently: a 5,6,5,6,5,6
        # run would slip past a check against only the previous frame and put a visible stutter in
        # the clip. Resends are identical, so comparing content catches them wherever they land.
        if new_frame in frames:
            return
        self._sensor_frame_no[sensor_id] += 1
        frame_no = self._sensor_frame_no[sensor_id]
        frames.append(new_frame)
        del frames[:-IMAGE_SEQUENCE_MAX_FRAMES]
        # Resolve the per-camera folder here (event-loop thread) so the executor call does no
        # registry lookups.
        self.hass.async_add_executor_job(
            self._render_sensor_media, sensor_id, list(frames), new_frame,
            self._sensor_seq_name[sensor_id], frame_no, self._camera_folder(sensor_id),
            self._sensor_audio.get(sensor_id),
        )

    def camera_folder(self, sensor_id: int) -> str:
        """Public name for the per-camera media sub-folder, used by the image entity."""
        return self._camera_folder(sensor_id)

    def camera_name(self, sensor_id: int) -> str:
        """Display name for a camera zone: the device name, else the zone number."""
        return self._camera_device_name(sensor_id) or f"Zone {sensor_id}"

    def _camera_device_name(self, sensor_id: int) -> str | None:
        """Name the user gave this camera's device, or None if it has no device yet."""
        try:
            dev = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, create_sensor_unique_id(self.panel_ident, sensor_id))}
            )
        except Exception:  # noqa: BLE001
            return None
        return (dev.name_by_user or dev.name) if dev is not None else None

    def _camera_folder(self, sensor_id: int) -> str:
        """Per-camera media sub-folder: the camera device name, else the zone number (event-loop thread)."""
        name = self._camera_device_name(sensor_id)
        if name and (folder := slugify(name)):
            return folder
        return f"zone{sensor_id}"

    @staticmethod
    def _ffmpeg_binary() -> str | None:
        """Locate an ffmpeg binary; HA ships one but it is not always on PATH."""
        import shutil

        if found := shutil.which("ffmpeg"):
            return found
        for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/ffmpeg/ffmpeg"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    @staticmethod
    def _wav_duration(data: bytes) -> float | None:
        """Seconds of audio in a RIFF/WAVE buffer, or None if the header cannot be read."""
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None
        pos, byte_rate, n_data = 12, None, None
        while pos + 8 <= len(data):
            chunk = data[pos:pos + 4]
            size = int.from_bytes(data[pos + 4:pos + 8], "little")
            body = pos + 8
            if chunk == b"fmt " and body + 12 <= len(data):
                byte_rate = int.from_bytes(data[body + 8:body + 12], "little")
            elif chunk == b"data":
                n_data = min(size, len(data) - body)
            pos = body + size + (size & 1)   # chunks are word aligned
        if not byte_rate or not n_data:
            return None
        return n_data / byte_rate

    def _announce_clip(self, sensor_id: int, cam_folder: str, directory: str, filename: str) -> None:
        """Say a capture finished rendering (executor thread).

        The path only exists in here, so without this an automation has no way to find the clip it
        should act on - a template cannot list a directory. Naming the camera needs the device
        registry, so the event itself is fired on the event loop.
        """
        path = os.path.join(directory, filename)
        stem = os.path.splitext(filename)[0]
        # A clip carries no still of its own. The frames it was built from are right here, so hand
        # one over for anything that needs a picture - a notification thumbnail, a poster frame.
        frames = sorted(
            f for f in os.listdir(directory)
            if f.startswith(f"{stem}_frame") and f.lower().endswith(".jpg")
        )
        poster = os.path.join(directory, frames[len(frames) // 2]) if frames else None
        self.hass.loop.call_soon_threadsafe(
            self._fire_clip_event, sensor_id, cam_folder, filename, path, poster
        )

    def _media_base(self) -> str:
        """Directory captures are filed under, absolute or relative to HA's media root."""
        configured = self.entry.options.get(CONF_IMAGE_MEDIA_PATH, DEFAULT_IMAGE_MEDIA_PATH)
        if os.path.isabs(configured):
            return configured
        # Relative goes under HA's media directory so captures land where the Media browser looks
        # ({"local": "/media"} in a container, {"local": "<config>/media"} otherwise).
        media_dirs = self.hass.config.media_dirs or {}
        root = media_dirs.get("local") or next(iter(media_dirs.values()), None) or self.hass.config.path("media")
        return os.path.join(root, configured)

    def _media_url(self, path: str | None) -> str | None:
        """Path under a configured media dir as the URL that serves it, or None if outside them."""
        if path is None:
            return None
        for key, root in (self.hass.config.media_dirs or {}).items():
            rel = os.path.relpath(path, root)
            if not rel.startswith(".."):
                return f"/media/{key}/{rel.replace(os.sep, '/')}"
        return None

    @callback
    def _fire_clip_event(self, sensor_id: int, cam_folder: str, filename: str, path: str, poster: str | None) -> None:
        """Fire the finished-capture event (event-loop thread).

        Signed copies of both URLs go out alongside the plain ones: /media needs authentication, and
        a consumer handed the path in YAML - a notification attachment, say - has no way to sign it
        itself.
        """
        media_url = self._media_url(path)
        poster_url = self._media_url(poster)
        self.hass.bus.async_fire(CAMERA_CLIP_EVENT, {
            PANEL_ATTRIBUTE_NAME: self.panel_ident,
            "zone": sensor_id,
            "camera": self.camera_name(sensor_id),
            "folder": cam_folder,
            "file": filename,
            "path": path,
            "media_url": media_url,
            "poster_url": poster_url,
            "signed_media_url": async_sign_path(self.hass, media_url, CLIP_URL_VALID) if media_url else None,
            "signed_poster_url": async_sign_path(self.hass, poster_url, CLIP_URL_VALID) if poster_url else None,
        })

    def _render_sensor_media(self, sensor_id: int, frames: list[bytes], frame: bytes, seq_name: str, frame_no: int, cam_folder: str, audio: bytes | None = None) -> None:
        """Save this frame plus the capture's audio, and mux the sequence into an MP4 (executor thread).

        A capture is a run of JPEG frames closed by an IMA ADPCM WAV clip, so the natural artefact is
        a video with sound. The clip is built when the audio arrives (the panel's own end-of-capture
        marker) so ffmpeg runs once per capture rather than once per frame. Falls back to an animated
        GIF where ffmpeg is unavailable.
        """
        import io
        import subprocess

        # Per-camera sub-folder so captures are browsable by camera in the media browser.
        directory = os.path.join(self._media_base(), cam_folder)
        stem = f"panel{self.panel_ident}_zone{sensor_id}_{seq_name}"
        # The image entity shows the latest still; the clip itself lands in the media browser.
        self._sensor_jpeg[sensor_id] = bytearray(frame)
        try:
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, f"{stem}_frame{frame_no:02d}.jpg"), "wb") as handle:
                handle.write(frame)
        except OSError as ex:
            self.logger.logstate_warning("Unable to save camera frame for zone %s: %s", sensor_id, ex)
            return
        wav_path = os.path.join(directory, f"{stem}.wav")
        if audio:
            try:
                with open(wav_path, "wb") as handle:
                    handle.write(audio)
            except OSError as ex:
                self.logger.logstate_warning("Unable to save camera audio for zone %s: %s", sensor_id, ex)
        # Build the clip once the panel closes the capture with its audio, and only if there is
        # more than a single still to animate.
        if not audio or len(frames) < 2:
            return
        if ffmpeg := self._ffmpeg_binary():
            # Pace the stills to the clip so the two end together. The panel's audio covers the
            # whole capture window - about 0.6s per frame - so a fixed 2fps runs the video short
            # and -shortest then truncates the sound. Fall back to the constant if the WAV header
            # cannot be read.
            fps = max(1, round(1000 / IMAGE_FRAME_DURATION_MS))
            if (secs := self._wav_duration(audio)) and secs > 0:
                fps = len(frames) / secs
            def _mux(with_audio: bool) -> bool:
                """Render the clip, with or without its soundtrack. True if ffmpeg was happy."""
                cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", f"{fps:.6f}",
                       "-i", os.path.join(directory, f"{stem}_frame%02d.jpg")]
                if with_audio:
                    cmd += ["-i", wav_path, "-c:a", "aac", "-shortest"]
                cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(directory, f"{stem}.mp4")]
                completed = subprocess.run(cmd, capture_output=True, timeout=120, check=False)  # noqa: S603
                if completed.returncode == 0:
                    return True
                self.logger.logstate_warning(
                    "ffmpeg could not build the clip for zone %s%s: %s", sensor_id,
                    "" if with_audio else " (even without audio)",
                    completed.stderr.decode(errors="replace").strip()[:200],
                )
                return False

            has_audio = os.path.isfile(wav_path)
            try:
                if _mux(has_audio):
                    self._announce_clip(sensor_id, cam_folder, directory, f"{stem}.mp4")
                    return
                # The soundtrack is unusable - a capture whose audio never passed its CRC will not
                # parse as a WAV at all. Still produce the video, silent, rather than losing it.
                if has_audio and _mux(False):
                    self.logger.logstate_warning("Zone %s clip rendered without its damaged audio", sensor_id)
                    self._announce_clip(sensor_id, cam_folder, directory, f"{stem}.mp4")
                    return
            except (OSError, subprocess.SubprocessError) as ex:
                self.logger.logstate_warning("Unable to run ffmpeg for zone %s: %s", sensor_id, ex)
        # No usable ffmpeg: fall back to an animated GIF so a clip is still produced (without audio).
        try:
            from PIL import Image
        except ImportError:
            return
        try:
            images = []
            for buffered in frames:
                try:
                    image = Image.open(io.BytesIO(buffered))
                    image.load()
                    images.append(image.convert("RGB"))
                except Exception:  # noqa: BLE001
                    continue
            if not images:
                return
            buffer = io.BytesIO()
            images[0].save(
                buffer,
                format="GIF",
                save_all=True,
                append_images=images[1:],
                duration=IMAGE_FRAME_DURATION_MS,
                loop=0,
            )
            with open(os.path.join(directory, f"{stem}.gif"), "wb") as handle:
                handle.write(buffer.getvalue())
            self._announce_clip(sensor_id, cam_folder, directory, f"{stem}.gif")
        except (OSError, ValueError) as ex:
            self.logger.logstate_warning("Unable to render/save camera clip for zone %s: %s", sensor_id, ex)

    def _mark_image_activity(self, sensor_id: int | None = None) -> float:
        """Record image activity; (re)start the burst clock only when previously idle. Returns now."""
        now = time.monotonic()
        if self._image_activity <= 0.0 or now - self._image_activity >= IMAGE_DOWNLOAD_TIMEOUT:
            self._image_download_start = now
        self._image_activity = now
        if sensor_id is not None:
            self._image_active_sensor = sensor_id
        return now

    def mark_image_request(self, sensor_id: int | None = None, duration: int = -1) -> None:
        """Record that an image download has been requested from the panel.

        duration 0 means the user wants a still rather than a clip, so the capture keeps the
        first frame and skips the video. -1 leaves the previous setting alone.
        """
        if sensor_id is not None and duration >= 0:
            self._sensor_stills_only[sensor_id] = duration == 0
        self._mark_image_activity(sensor_id)

    def stills_only(self, sensor_id: int) -> bool:
        """Was this camera's current capture asked for as a single still."""
        return self._sensor_stills_only.get(sensor_id, False)

    def image_download_active(self) -> bool:
        """True while frames arrive (within IMAGE_DOWNLOAD_TIMEOUT of the last, capped at IMAGE_DOWNLOAD_MAX from burst start)."""
        if self._image_activity <= 0.0:
            return False
        now = time.monotonic()
        return (
            now - self._image_activity < IMAGE_DOWNLOAD_TIMEOUT
            and now - self._image_download_start < IMAGE_DOWNLOAD_MAX
        )

    def image_download_sensor(self) -> int | None:
        """Zone of the camera currently downloading, or None when idle."""
        return self._image_active_sensor if self.image_download_active() else None

    def enqueue_image_request(self, sensor_id: int, eid: str | None, duration: int) -> ImageQueueState:
        """Return SEND (dispatch now), QUEUED, or FULL for an image-request press."""
        if not self.image_download_active() and not self._image_queue:
            return ImageQueueState.SEND
        max_depth = int(self.entry.options.get(CONF_IMAGE_QUEUE_MAX, DEFAULT_IMAGE_QUEUE_MAX))
        if max_depth <= 0 or len(self._image_queue) >= max_depth:
            return ImageQueueState.FULL
        self._image_queue.append((sensor_id, eid, duration))
        return ImageQueueState.QUEUED

    def pop_image_request(self) -> tuple[int, str | None, int] | None:
        """Return the next queued image request, or None if the queue is empty."""
        return self._image_queue.popleft() if self._image_queue else None

    def image_queue_depth(self) -> int:
        """Return the number of image requests currently waiting in the queue."""
        return len(self._image_queue)

    def reset_image_state(self) -> None:
        """Clear the download-active state and drop any queued requests (used on (re)connect)."""
        self._image_activity = 0.0
        self._image_download_start = 0.0
        self._image_active_sensor = None
        self._image_queue.clear()

    def _get_sensor_jpeg(self, sensor_id: int) -> bytearray | None:
        return self._sensor_jpeg.get(sensor_id)

    def get_jpg_image(self, sensor_id: int) -> bytearray | None:
        """Get the binary image data from a camera sensor."""
        return self._get_sensor_jpeg(sensor_id)

    def _newest_frame_on_disk(self, sensor_id: int, cam_folder: str) -> bytearray | None:
        """Most recent saved frame for a camera, or None if it has never captured (executor thread).

        Frame names carry the capture timestamp then the frame number, so they sort into order.
        """
        directory = os.path.join(self._media_base(), cam_folder)
        try:
            frames = sorted(f for f in os.listdir(directory) if _FRAME_RE.search(f))
            if not frames:
                return None
            with open(os.path.join(directory, frames[-1]), "rb") as handle:
                return bytearray(handle.read())
        except OSError:
            return None

    async def async_get_jpg_image(self, sensor_id: int) -> bytearray | None:
        """Get the binary image data from a camera sensor."""
        if (cached := self._get_sensor_jpeg(sensor_id)) is not None:
            return cached
        # Nothing buffered: the panel only sends frames on request, so after a restart the entity
        # has no picture at all until the next capture - even though every frame it has ever
        # received is sitting on disk. Naming the folder needs the device registry, so resolve it
        # here on the event loop and let the executor do the file work.
        frame = await self.hass.async_add_executor_job(
            self._newest_frame_on_disk, sensor_id, self._camera_folder(sensor_id)
        )
        if frame is not None:
            self._sensor_jpeg[sensor_id] = frame
        return frame
