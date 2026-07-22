"""Fault injection for camera PIR image assembly.

AlImageManager is pure logic - no HA, no panel, no serial - so the reassembly path can be
driven directly with synthetic F4 data, including the corrupt kind a real panel produces.

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
    """Baseline: an undamaged image reassembles byte for byte."""
    m = _start()
    for seq, payload in _chunks():
        assert m.addData(payload, seq) is True
    assert m.isImageComplete()
    assert bytes(m.getImage(ZONE, IMAGE_ID)) == JPEG


def test_dropped_chunk_wedges_the_image():
    """A single lost F4-05 stops every later chunk being accepted.

    addBufferData only advances _next_sequence on a match, so once one chunk goes missing
    the expected sequence never catches up and the rest of the image is rejected.
    """
    m = _start()
    accepted = 0
    for i, (seq, payload) in enumerate(_chunks()):
        if i == 3:
            continue  # the panel drops one chunk mid image
        accepted += m.addData(payload, seq) is True
    assert accepted == 3, "everything after the gap is refused, not just the missing chunk"
    assert not m.isImageComplete()
    assert m.hasStartedSequence(), "and the record is left in progress indefinitely"


def test_oversized_chunk_overruns_the_buffer():
    """An over-long chunk grows the bytearray past size, so completion can never trigger.

    _buffer[_current:_current+datalen] = data is a slice assignment: on a bytearray it
    extends rather than truncating. _current then exceeds _size and the completion test
    (_current == size) is an equality, so it is skipped over and never fires again.
    """
    m = _start()
    seqs = _chunks()
    for seq, payload in seqs[:-1]:
        assert m.addData(payload, seq) is True
    seq, payload = seqs[-1]
    m.addData(payload + b"\xff" * 32, seq)  # panel sends a longer chunk than declared
    assert not m.isImageComplete(), "overrun sails past the == size check"
    assert len(m._current_image.buffer) > len(JPEG), "buffer grew beyond the declared size"


def test_truncated_transfer_needs_the_timeout():
    """A transfer that stops dead never completes; only the timeout clears it."""
    m = _start()
    for seq, payload in _chunks()[:4]:
        assert m.addData(payload, seq) is True
    assert not m.isImageComplete()

    m.terminateIfExceededTimeout(40)
    assert m.hasStartedSequence(), "still in progress before the timeout elapses"

    m._current_image._last -= timedelta(seconds=41)
    m.terminateIfExceededTimeout(40)
    assert not m.hasStartedSequence(), "timeout is what rescues a dead transfer"


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
    m.addData(*reversed(_chunks()[0]))  # addData(databuffer, sequence)
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
