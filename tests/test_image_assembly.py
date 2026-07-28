"""Fault injection for camera PIR image assembly.

AlImageManager is pure logic - no HA, no panel, no serial - so the reassembly path can be
driven directly with synthetic F4 data, including the corrupt kind a real panel produces.

These assert the behaviour we want rather than the behaviour we have, so a gap shows up as a
failure with the requirement spelled out next to it.

Run with:  python3 -m pytest tests/ -v      (or just: python3 tests/test_image_assembly.py)
"""

from datetime import timedelta
import importlib.util
import pathlib
import sys
import types

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components/visonic/direct/pyvisonic"


def _load():
    """Load py_sensor_image without importing the HA integration package."""
    pkg = types.ModuleType("pv")
    pkg.__path__ = [str(PKG)]
    sys.modules["pv"] = pkg
    for name in ("py_utils", "py_sensor_image"):
        spec = importlib.util.spec_from_file_location(f"pv.{name}", PKG / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"pv.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["pv.py_sensor_image"]


img = _load()



ZONE, UID, IMAGE_ID = 2, 0x14, 1
JPEG = bytes.fromhex("ffd8ffdb") + b"\x00" * 92 + bytes.fromhex("ffd9")  # 98 bytes
CHUNK = 14  # 7 chunks of 14 bytes


def _start(size=len(JPEG), crc=None, totalimages=1, image_id=IMAGE_ID):
    """A manager with an image sequence started, ready for data at sequence 0x10."""
    m = img.AlImageManager()
    assert m.create(ZONE, 1)
    assert m.setCurrent(
        zone=ZONE, unique_id=UID, image_id=image_id, size=size,
        sequence=0x00, lastimage=True, totalimages=totalimages, crc=crc,
    )
    return m


def _chunks(data=JPEG, n=CHUNK):
    """(sequence, payload) pairs as the panel sends them - sequence steps by 0x10."""
    return [((0x10 * (i + 1)) & 0xFF, data[o:o + n]) for i, o in enumerate(range(0, len(data), n))]


def test_clean_image_assembles():
    """An undamaged image reassembles byte for byte."""
    m = _start()
    for seq, payload in _chunks():
        assert m.addData(payload, seq) is True
    assert m.isImageComplete()
    assert bytes(m.getImage(ZONE, IMAGE_ID)) == JPEG


def test_a_dropped_chunk_is_refused_so_the_image_can_be_restarted():
    """A lost F4-05 must stop the image dead rather than assemble a hole.

    Recovery is whole-image: the caller stops the manager on a False and NAKs, and the panel
    resends the image from a fresh F4-03 - it does not resend the single missing F4-05.
    """
    m = _start()
    seqs = _chunks()
    for seq, payload in seqs[:3]:
        assert m.addData(payload, seq) is True

    for seq, payload in seqs[4:]:            # one chunk lost, the rest arrive out of order
        assert m.addData(payload, seq) is False, "must not accept data past the gap"
    assert not m.isImageComplete()


def test_a_refused_chunk_lets_the_panel_restart_the_image_from_scratch():
    """After a refusal the caller stops the manager, and the resent image assembles cleanly.

    The teardown is what makes the sequence counter irrelevant: setCurrent builds a fresh
    record from the new F4-03, so whatever state the failed attempt left behind is dropped.
    """
    m = _start()
    seqs = _chunks()
    assert m.addData(seqs[0][1], seqs[0][0]) is True
    assert m.addData(b"\x00" * CHUNK, 0x99) is False, "out of sequence is refused"

    m.stop()                                  # what the caller does on a False, then NAKs
    assert not m.hasStartedSequence()

    assert m.setCurrent(                      # panel restarts the image with a new F4-03
        zone=ZONE, unique_id=UID, image_id=IMAGE_ID, size=len(JPEG),
        sequence=0x00, lastimage=True, totalimages=1,
    ), "the resent header must be accepted"
    for seq, payload in _chunks():
        assert m.addData(payload, seq) is True
    assert m.isImageComplete()
    assert bytes(m.getImage(ZONE, IMAGE_ID)) == JPEG


def test_an_oversized_chunk_is_refused_rather_than_overrunning_the_buffer():
    """Too much data must be refused so the caller can restart the image.

    Slice assignment on a bytearray extends rather than truncating, so an over-long chunk
    pushes _current past _size. Completion is tested with _current == size, an equality
    that has then been stepped over, so the image never completes and sits in progress
    until the timeout - silently, because nothing returned False.
    """
    m = _start()
    seqs = _chunks()
    for seq, payload in seqs[:-1]:
        assert m.addData(payload, seq) is True

    seq, payload = seqs[-1]
    assert m.addData(payload + b"\xff" * 32, seq) is False, "over-long chunk is refused"
    assert len(m._current_image.buffer) == len(JPEG), "buffer must not grow past the declared size"


def test_truncated_transfer_is_cleared_by_the_timeout():
    """A transfer that stops dead never completes; the timeout is what releases it."""
    m = _start()
    for seq, payload in _chunks()[:4]:
        assert m.addData(payload, seq) is True
    assert not m.isImageComplete()

    m.terminateIfExceededTimeout(40)
    assert m.hasStartedSequence(), "still in progress before the timeout elapses"

    m._current_image._last -= timedelta(seconds=41)
    m.terminateIfExceededTimeout(40)
    assert not m.hasStartedSequence(), "timeout rescues a dead transfer"


def test_sequence_active_spans_the_gap_between_images():
    """isSequenceActive stays true between images, where hasStartedSequence does not."""
    m = _start()
    for seq, payload in _chunks():
        m.addData(payload, seq)
    assert m.isImageComplete()
    assert not m.hasStartedSequence(), "per image flag clears as each image completes"
    assert m.isSequenceActive(), "but the download as a whole is still underway"


def test_sequence_active_self_clears_after_an_abandoned_download():
    """An abandoned download must not suppress B0 polling forever."""
    m = _start()
    seq, payload = _chunks()[0]
    assert m.addData(payload, seq) is True
    assert m.isSequenceActive()

    m._last_activity -= timedelta(seconds=16)
    assert not m.isSequenceActive(seconds=15), "window lapses, caller resumes"
    m.stop()
    assert not m.isSequenceActive()


# Real frames captured off the wire, used as CRC vectors. Body is the frame without the 0x0D
# preamble and without the two CRC bytes and 0x0A footer, which is what f4_crc16 is fed.
WIRE_VECTORS = [
    ("f4 07 ack",   "f4 07 00 01 04 06 70 00 00", (0xA9, 0x7C)),
    ("f4 10 ack",   "f4 10 00 01 04 00 06 70 00", (0x6D, 0xC3)),
    ("f4 01 panel", "f4 01 00 00 00",             (0xE4, 0xC0)),
]


def test_f4_crc16_matches_frames_captured_off_the_wire():
    """The CRC must reproduce real panel and Powerlink frames byte for byte."""
    for name, body, expected in WIRE_VECTORS:
        got = img.f4_crc16(bytes.fromhex(body.replace(" ", "")))
        assert got == expected, f"{name}: got {got}, wire says {expected}"


def test_a_good_image_passes_its_header_crc():
    """An intact image matches the CRC the panel declared for it."""
    m = _start(crc=img.f4_crc16(JPEG))
    for seq, payload in _chunks():
        assert m.addData(payload, seq) is True
    _, ir = m.getLastImageRecord()
    assert ir.isChecksumValid()


def test_a_corrupted_image_fails_its_header_crc():
    """A single flipped byte is caught, which the per-chunk F4-05 CRC cannot do reliably."""
    m = _start(crc=img.f4_crc16(JPEG))
    chunks = _chunks()
    damaged = bytearray(chunks[2][1])
    damaged[0] ^= 0xFF                       # one byte, mid image
    chunks[2] = (chunks[2][0], bytes(damaged))
    for seq, payload in chunks:
        assert m.addData(payload, seq) is True
    _, ir = m.getLastImageRecord()
    assert not ir.isChecksumValid(), "a flipped byte must not pass"


def test_an_image_with_no_declared_crc_is_not_treated_as_bad():
    """No CRC means unchecked, not failed - an unchecked image is still served."""
    m = _start(crc=None)
    for seq, payload in _chunks():
        assert m.addData(payload, seq) is True
    _, ir = m.getLastImageRecord()
    assert ir.isChecksumValid()


def test_first_header_total_of_0xff_does_not_overwrite_a_known_total():
    """The panel says 0xFF in the first header of a capture, which means "not told yet"."""
    m = _start(totalimages=img.TOTAL_IMAGES_UNKNOWN)
    assert m.ImageZone[ZONE].totalimages == img.TOTAL_IMAGES_UNKNOWN, "still unknown after the first header"
    for seq, payload in _chunks():
        m.addData(payload, seq)

    assert m.setCurrent(zone=ZONE, unique_id=UID, image_id=2, size=len(JPEG),
                        sequence=0x00, lastimage=False, totalimages=4)
    assert m.ImageZone[ZONE].totalimages == 4, "the real total is taken when the panel sends it"

    for seq, payload in _chunks():
        m.addData(payload, seq)
    assert m.setCurrent(zone=ZONE, unique_id=UID, image_id=3, size=len(JPEG),
                        sequence=0x00, lastimage=False, totalimages=img.TOTAL_IMAGES_UNKNOWN)
    assert m.ImageZone[ZONE].totalimages == 4, "0xFF must not clobber what we already knew"


def test_a_bad_image_is_retried_a_bounded_number_of_times():
    """Retries are capped so an unrecoverable image cannot stall the capture forever."""
    m = _start()
    for n in range(1, img.MAX_IMAGE_ATTEMPTS + 1):
        assert m.note_attempt(ZONE, IMAGE_ID) == n
        expect_more = n < img.MAX_IMAGE_ATTEMPTS
        assert m.attempts_left(ZONE, IMAGE_ID) is expect_more
    assert not m.attempts_left(ZONE, IMAGE_ID), "must give up rather than loop"
    m.stop()
    assert m.attempts_left(ZONE, IMAGE_ID), "a new capture starts the count again"


def test_discarding_a_bad_image_removes_it_from_the_store():
    """A failed image must not be left behind for the user to see."""
    m = _start(crc=(0x00, 0x00))             # a CRC the image cannot match
    for seq, payload in _chunks():
        m.addData(payload, seq)
    assert m.getImage(ZONE, IMAGE_ID) is not None
    _, ir = m.getLastImageRecord()
    assert not ir.isChecksumValid()

    m.discard_last()
    assert m.getImage(ZONE, IMAGE_ID) is None, "the bad image is dropped"
    assert m.getLastImageRecord() == (None, None)


def test_an_in_progress_image_reports_itself_as_in_progress():
    """isImageDataInProgress must be true while an image is part built.

    It used to read last_image, which is only assigned once an image completes, and a
    completed record has _ongoing False - so it could never be true and every caller was
    dead code, including the stuck-image timeout.
    """
    m = _start()
    assert not m.isImageDataInProgress(), "nothing received yet"

    seq, payload = _chunks()[0]
    assert m.addData(payload, seq) is True
    assert m.isImageDataInProgress(), "part built image must report in progress"

    for seq, payload in _chunks()[1:]:
        m.addData(payload, seq)
    assert m.isImageComplete()
    assert not m.isImageDataInProgress(), "a finished image is not in progress"


def test_a_dead_transfer_does_not_block_the_next_request():
    """A part built image the panel abandoned must not lock out later captures.

    There is one in-flight record for the whole manager, because an F4-05 carries no zone and
    the panel only sends one image at a time. Correct, but it meant a transfer that died left
    create() refusing every request, for every camera, until HA restarted.
    """
    m = _start()
    seq, payload = _chunks()[0]
    assert m.addData(payload, seq) is True          # then the panel goes quiet
    assert m.hasStartedSequence()

    assert m.create(ZONE, 1) is False, "a live transfer still blocks, as it should"
    assert m.create(9, 1) is False, "and blocks other cameras too, since there is one link"

    m._current_image._last -= timedelta(seconds=img.IMAGE_TRANSFER_TIMEOUT + 1)
    assert m.create(9, 1) is True, "but a dead transfer is dropped rather than blocking forever"


def test_a_record_with_no_data_yet_is_still_released_by_the_timeout():
    """A header that arrives and is then never followed by data must not block for ever.

    isOngoing() requires _current > 0, so a record holding zero bytes is invisible to
    isImageDataInProgress() while still making hasStartedSequence() true - which is what
    create() refuses on. The timeout has to key off the record existing, not off it having
    received something.
    """
    m = _start()
    assert m.hasStartedSequence(), "the header created a record"
    assert not m.isImageDataInProgress(), "but no data has arrived for it"

    m._current_image._last -= timedelta(seconds=img.IMAGE_TRANSFER_TIMEOUT + 1)
    m.terminateIfExceededTimeout(img.IMAGE_TRANSFER_TIMEOUT)
    assert not m.hasStartedSequence(), "an empty stale record must be released too"


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except AssertionError as ex:
            failed += 1
            print(f"FAIL  {name}\n        {ex}")
        else:
            passed += 1
            print(f"pass  {name}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
