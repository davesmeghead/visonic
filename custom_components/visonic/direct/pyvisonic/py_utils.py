"""Utility Functions."""

# ruff: noqa: G004

from datetime import UTC, datetime
import logging
import re

log = logging.getLogger(__name__)


def hexify(v: int) -> str:
    """Convert integer to hex string without 0x prefix."""
    return f"{hex(v)[2:]}"

def convert_bytearray(st: str) -> bytearray:
    """Convert hex string to bytearray."""
    return bytearray.fromhex(st)

# Convert byte array to a string of hex values
def toString(array_alpha: bytearray, gap = " ") -> str:
    """Convert byte array to a string of hex values."""
#    return ("".join(("%02x"+gap) % b for b in array_alpha))[:-len(gap)] if len(gap) > 0 else "".join(f"{b:02x}" for b in array_alpha)
    return gap.join(f"{b:02x}" for b in array_alpha)

def to_bool(val: bool | str | int) -> bool:
    """Convert various types to boolean."""
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        v = val.lower()
        return v not in ["no", "false", "0"]
    log.warning(f"Unable to decode boolean value {val}    type is {type(val)}")
    return False

def capitalize(s: str) -> str:
    """Capitalize the first letter of a string and lowercase the rest."""
    return s[0].upper() + s[1:].lower()

def titlecase(s: str) -> str:
    """Title case a string."""
    return re.sub(r"[A-Za-z]+('[A-Za-z]+)?", lambda word: capitalize(word.group(0)), s)

# get the current date and time
def get_local_time() -> datetime:
    """Get the current local date and time with timezone info."""
    return datetime.now(UTC).astimezone()

# get the current date and time
def get_utc_time() -> datetime:
    """Get the current UTC date and time."""
    return datetime.now(tz=UTC)

def b2i(data: bytearray | bytes, big_endian: bool = False) -> int:
    """Convert bytes to int."""
    return int.from_bytes(
        data if isinstance(data, (bytearray, bytes)) else bytes(data),
        "big" if big_endian else "little"
    )


def f4_crc16(body: bytearray | bytes) -> tuple[int, int]:
    """CRC-16/CCITT (poly 0x1021, init 0, no xorout) for F4 messages, output byte-swapped.

    Used for three things: validating a received F4 frame, building the F4-07/F4-10 acks,
    and checking a reassembled camera image against the CRC in its F4-03 header. Verified
    byte-for-byte against a real Powerlink on the wire (104/105 captured acks).
    """
    crc = 0
    for by in body:
        crc ^= by << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFF, (crc >> 8) & 0xFF
