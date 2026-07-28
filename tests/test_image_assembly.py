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


def _start(size=len(JPEG)):
    """A manager with an image sequence started, ready for data at sequence 0x10."""
    m = img.AlImageManager()
    assert m.create(ZONE, 1)
    assert m.setCurrent(
        zone=ZONE, unique_id=UID, image_id=IMAGE_ID, size=size,
        sequence=0x00, lastimage=True, totalimages=1,
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
