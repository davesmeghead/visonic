"""Sensor imagery."""

# ruff: noqa: G004

from datetime import datetime, timedelta
import logging

from .py_const import IMAGE_TRANSFER_TIMEOUT
from .py_utils import f4_crc16, get_utc_time

log = logging.getLogger(__name__)

TOTAL_IMAGES_UNKNOWN = 0xFF   # what the panel puts in the first F4-03 header of a capture
MAX_IMAGE_ATTEMPTS = 3        # asks for a bad image this many times before giving up on it


class ImageRecord:
    """Image Record Class. The details of an individual image."""

    def __init__(self, zone: int, image_id: int, size: int, next_seq: int, lastimage: bool, parent: ImageZoneClass, crc: tuple[int, int] | None = None) -> None:
        """Initialize the ImageRecord."""
        self._current_id = image_id               # The image_id is the image number from the panel.  The panel outputs a sequence of images.
        self._buffer: bytearray = bytearray(size) # Data buffer
        self._lastimage: bool = lastimage         # Boolean, is this the last image that the panel is sending
        self._current: int = 0                    # current position in the buffer as if gets filled with data
        self._next_sequence: int = next_seq       # The panel sends the data in a series of messages that are sequenced
        self._ongoing = True                      # Are we creating the image or have we finished
        self._last = get_utc_time()               # Date/Time of the last data from the panel, used for timeouts
        self._zone: int = zone                    # The zone that the image is from
        self._size: int = size                    # The size of the image in bytes
        self.parent: ImageZoneClass = parent      # The parent ImageZoneClass
        self._crc: tuple[int, int] | None = crc   # CRC-16 of the finished image, from the F4-03 header

    def __str__(self) -> str:
        """Return a string representation."""
        return f"{self.size=}  {self._current=}  {self._ongoing=}  {hex(self._next_sequence)=}     {self.lastimage=}    {self.parent=}"

    @property
    def last_time(self) -> datetime:
        """Return the last time stamp for this image."""
        return self._last

    @property
    def buffer(self) -> bytearray:
        """Return the image buffer for this image."""
        return self._buffer

    @property
    def zone(self) -> int:
        """Return the zone for this image."""
        return self._zone

    @property
    def image_id(self) -> int:
        """Return the image id for this image."""
        return self._current_id

    @property
    def current_position(self) -> int:
        """Return the current position in building this image."""
        return self._current

    @property
    def size(self) -> int:
        """Return the size for this image."""
        return self._size

    @property
    def lastimage(self) -> bool:
        """Return the last image bool for this image."""
        return self._lastimage

    def hasParent(self):
        """There should always be a parent but just check."""
        return self.parent is not None

    @property
    def crc(self) -> tuple[int, int] | None:
        """Return the CRC the panel declared for this image, or None if it did not."""
        return self._crc

    def isChecksumValid(self) -> bool:
        """Does the assembled image match the CRC the panel put in its F4-03 header.

        This is the only integrity check on an image that actually works. The per-chunk CRC in
        the F4-05 messages cannot be used: 46 of 205 chunks on a clean wire fail their own CRC
        every time, including the ones carrying the JPEG SOF and SOS markers. This one is over
        the finished buffer and holds for the audio clip as well as the JPEGs.

        Returns True when no CRC was supplied, so an unchecked image is never treated as bad.
        """
        if self._crc is None:
            return True
        return f4_crc16(self._buffer) == self._crc

    def addBufferData(self, databuffer, sequence) -> bool:
        """Add image buffer data."""
        if self._ongoing:
            if self._next_sequence is not None and sequence == self._next_sequence:
                datalen = len(databuffer)
                if self._current + datalen > self.size:
                    log.debug("[handle_msgtypeF4]       ERROR: Attempt to add too much image data and the record size has been exceeded")
                    return False
                self._next_sequence = (self._next_sequence + 0x10) & 0xFF
                self._last = get_utc_time()
                self._buffer[self._current: self._current+datalen] = databuffer
                self._current = self._current + datalen
                if self._current == self.size:
                    # Could check the data for a jpg Start of Image "FF D8", Quantization Table "FF DB" and Start of Frame "FF C0", and maybe end of image "FF D9"
                    #   So "ff d8 ff db" at the start and "ff d9" at the end
                    #      "ff c0" is in the middle for the start of the image data itself
                    self._ongoing = False
                log.debug(f"[handle_msgtypeF4]         {self}")
                return True
            log.debug("[handle_msgtypeF4]       ERROR: Received Image data out of sequence")
        else:
            log.debug("[handle_msgtypeF4]       ERROR: Attempt to add image data and the record has not been created")
        return False

    def isImageComplete(self) -> bool:
        """Is the image complete."""
        return not self._ongoing

    def isOngoing(self) -> bool:
        """Is the image data ongoing."""
        return self._ongoing and self._current > 0

class ImageZoneClass:
    """Image Zone Class."""

    def __init__(self) -> None:
        """Initialize the ImageZoneClass."""
        self.start = get_utc_time()                 # Start time
        self.count = 0                              # How many images did the user ask for, this defaults to 11 as we can't set this to the panel and 11 is how many the panel sends anyway
        self.totalimages = TOTAL_IMAGES_UNKNOWN     # The panel only tells us from the second header onwards
        self.unique_id = -1                         # Each sequence has a unique id
        self.images: dict[int, ImageRecord] = { }   # Image Store, images are replaced when a new one is sent
        self.degraded = False

    def __str__(self) -> str:
        """Return a string representation."""
        return f"{self.totalimages=}  {self.count=}  {self.unique_id=}  {self.start=}"

    def add_replace(self, image_id: int, image_record: ImageRecord):
        """Add or replace an image in the image list, and make it current."""
        if image_id in self.images:
            del self.images[image_id]
        self.images[image_id] = image_record

    def delete(self, image_id: int):
        """Delete an image from the image list."""
        if image_id in self.images:
            del self.images[image_id]

class AlImageManager:
    """Image Manager Class."""

    def __init__(self) -> None:
        """Initialize the AlImageManager."""
        self.ImageZone: dict[int, ImageZoneClass] = {}    # Zone and Image Store
        self.last_image = None                            # A shortcut to the last successfully built image
        # These 3 variables are used whilst creating an image and getting the image data piece by piece
        self._current_zone: int | None = None             # when not None then building an image for this zone number
        self._current_id: int | None = None
        self._current_image: ImageRecord = None           # The current image being built
        self._last_activity: datetime | None = None       # last time any F4 image data arrived, for isSequenceActive
        self._attempts: dict[tuple[int, int], int] = {}   # (zone, image_id) -> times the panel has sent it

    def note_attempt(self, zone: int, image_id: int) -> int:
        """Count an attempt at an image and return how many there have now been."""
        key = (zone, image_id)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        return self._attempts[key]

    def attempts_left(self, zone: int, image_id: int) -> bool:
        """Is it worth asking the panel for this image again."""
        return self._attempts.get((zone, image_id), 0) < MAX_IMAGE_ATTEMPTS

    def discard_last(self):
        """Drop the image just completed, so a failed one is not served to the user."""
        if self.last_image is not None:
            zone, image_id = self.last_image.zone, self.last_image.image_id
            if zone in self.ImageZone:
                self.ImageZone[zone].delete(image_id)
        self.last_image = None

    def reset_current(self):
        """Reset the in-progress image build state."""
        self._current_zone = None            # when not None then building an image for this zone number
        self._current_id = None
        self._current_image = None           # The current image being built

    def stop(self):
        """Terminate the image creation."""
        self.reset_current()
        self.last_image = None
        self._last_activity = None
        self._attempts = {}

    def isSequenceActive(self, seconds: int = 15) -> bool:
        """Is a camera image download underway, including the gaps between images.

        hasStartedSequence() only covers a single image - reset_current() runs as each
        one completes - so it reads False during the several second gap before the panel
        starts the next. This spans the whole sequence.

        Deliberately a time window and not a flag: a download that is abandoned mid way
        (panel stops sending, integration restarts) must not suppress the caller forever,
        so it self clears `seconds` after the last F4 message.
        """
        if self._last_activity is None:
            return False
        return (get_utc_time() - self._last_activity) < timedelta(seconds=seconds)

    def isImageComplete(self) -> bool:
        """Is the image complete."""
        return self._current_image is None and self.last_image is not None and self.last_image.isImageComplete()

    def isImageDataInProgress(self) -> bool:
        """Is an image part built right now. Note last_image is the finished one, not this."""
        return self._current_image is not None and self._current_image.isOngoing()

    def terminateIfExceededTimeout(self, seconds) -> tuple[int, int] | None:
        """Abandon the in-progress image if the panel has gone quiet for too long.

        Returns (zone, image_id) when a capture was abandoned so the caller can tell the user,
        or None when there was nothing to abandon.
        """
        if self._current_image is not None:
            interval = get_utc_time() - self._current_image.last_time
            if interval is not None and interval >= timedelta(seconds=seconds):
                zone, image_id = self._current_image.zone, self._current_image.image_id
                log.warning(f"[AlImageManager]  Abandoning image {image_id} for zone "
                            f"{zone} after {seconds}s with no data, "
                            f"({self._current_image.current_position} of {self._current_image.size} bytes)")
                self.stop()
                return (zone, image_id)
        return None

    def create(self, zone, count) -> bool:
        """Create a new image record. But does not start a new image."""
        # set up an entry in ImageZone with no images
        #    count is the number of images that the user asked for
        if self.hasStartedSequence():
            # Only one image is ever in flight: an F4-05 carries no zone, so the receiver cannot
            # tell two apart. Refusing here is therefore right, but a record left behind by a panel
            # that stopped sending must not block every camera, so drop a dead one first.
            self.terminateIfExceededTimeout(IMAGE_TRANSFER_TIMEOUT)
            if self.hasStartedSequence():
                return False
        if zone not in self.ImageZone:
            self.ImageZone[zone] = ImageZoneClass()
        self.last_image = None
        self._attempts = {}
        self.ImageZone[zone].count = count
        self.ImageZone[zone].degraded = False
        log.debug(f'[AlImageManager]  Create JPG: zone = {zone}   start time = {self.ImageZone[zone].start}   count = {self.ImageZone[zone].count}')
        return True

    def hasStartedSequence(self) -> bool:
        """Has started image sequence."""
        return self._current_image is not None

    def setCurrent(self, zone, unique_id, image_id, size, sequence, lastimage, totalimages, crc: tuple[int, int] | None = None) -> bool:
        """Start a new (current) image record."""
        if self.hasStartedSequence() or zone not in self.ImageZone:
            log.debug(f'[AlImageManager]  Setup not successful for zone = {self._current_zone}    unique_id = {hex(unique_id)}    image_id = {image_id}')
            return False

        # Set or update the image zone parameters
        self.ImageZone[zone].unique_id = unique_id
        # The panel sends 0xFF in the first header of a capture and the real total in every one
        # after, so treat 0xFF as "not told yet" and keep whatever we already know.
        if totalimages != TOTAL_IMAGES_UNKNOWN:
            self.ImageZone[zone].totalimages = totalimages

        # Always replace the existing ImageRecord if one already exists
        image_record = ImageRecord(zone = zone, image_id = image_id, size = size, lastimage = lastimage, next_seq = (sequence + 0x10) & 0xFF, parent = self.ImageZone[zone], crc = crc)
        self._current_zone = zone
        self._current_id = image_id
        self._current_image = image_record
        self._last_activity = get_utc_time()
        self.last_image = None

        log.debug(f'[AlImageManager]  setCurrent zone = {self._current_zone}    unique_id = {hex(unique_id)}    image_id = {image_id}')
        log.debug(f"[AlImageManager]             {image_record}")
        return True

    def addData(self, databuffer, sequence) -> bool:
        """Add image data to the current image record."""
        if not self._current_image:
            return False
        self._last_activity = get_utc_time()
        insequence = self._current_image.addBufferData(databuffer, sequence)
        if self._current_image.isImageComplete():
            self.last_image = self._current_image
            self.ImageZone[self._current_zone].add_replace(self._current_id, self._current_image)
            self.reset_current()
        return insequence

    def getLastImageRecord(self) -> tuple[ImageZoneClass, ImageRecord]:
        """Get the last image record."""
        if self.last_image is not None and self.last_image.hasParent():
            return (self.last_image.parent, self.last_image)
        return (None, None)

    def getCurrentImageRecord(self) -> tuple[ImageZoneClass, ImageRecord]:
        """Get the last image record."""
        if self._current_image is not None and self._current_image.hasParent():
            return (self._current_image.parent, self._current_image)
        return (None, None)

    def isValidImage(self, zone: int, image_id: int) -> bool:
        """Is the image valid."""
        return self.isValidZone(zone) and image_id in self.ImageZone[zone].images

    def isValidZone(self, zone: int) -> bool:
        """Is the zone valid."""
        return zone in self.ImageZone

    def getImage(self, zone: int, image_id: int) -> bytearray | None:
        """Get the image buffer."""
        return self.ImageZone[zone].images[image_id].buffer if self.isValidImage(zone, image_id) else None

    #def getImageList(self, zone) -> list:
    #    """Get the list of images for a zone."""
    #    return list(self.ImageZone[zone].images) if self.isValidZone(zone) else []
