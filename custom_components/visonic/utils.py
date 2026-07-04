"""Simple Utility Functions."""

import asyncio
from datetime import UTC, datetime
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .const import DOMAIN, VISONIC_UNIQUE_NAME

_LOGGER = logging.getLogger(__name__)

###################################################################################
#################  General Utility Functions ######################################
###################################################################################

def convert_bytearray(s: str) -> bytearray:
    """Convert string to bytearray."""
    return bytearray.fromhex(s)

# get the current date and time - Local
def get_local_time() -> datetime:
    """Return the current local time."""
    return datetime.now(UTC).astimezone()

# get the current date and time - UTC
def get_utc_time() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)

def capitalize(s: str):
    """Capitalize the first letter of a string and lowercase the rest."""
    return s[:1].upper() + s[1:].lower() if s else ""

def titlecase(s: str) -> str:
    """Title case a string, handling apostrophes correctly."""
    return re.sub(r"[A-Za-z]+('[A-Za-z]+)?", lambda m: m.group(0).capitalize(), s)

def hexify(v: int) -> str:
    """Convert integer to hex string without 0x prefix."""
    return f"{hex(v)[2:]}"

def to_bool(val: bool | int | str | None) -> bool:
    """Convert value to boolean."""
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        v = val.lower()
        return v not in ["no", "false", "0"]
    _LOGGER.warning("Unable to decode boolean value %s    type is %s", val, type(val))
    return False

def print_partition(part : int | set | list) -> str:
    """Convert a partition value or set or list to a string, also adding 1 to the value."""
    if isinstance(part, int):
        part = [part]
    tmp = [a+1 for a in part if a >= 0]
    return ''.join(c for c in str(tmp) if c not in "{}[]() ")

def create_base_prefix(panel_ident: int) -> str:
    """Get my string."""
    if panel_ident > 0:
        return f"{DOMAIN}_p{panel_ident}_"
    return f"{DOMAIN}_"

def create_sensor_label(id: int) -> str:
    """Create a zone sensor label string."""
    return f"Z{id:0>2}"

def create_siren_label(id: int) -> str:
    """Create a zone sensor label string."""
    return f"S{id:0>2}"

def create_switch_label(id: int) -> str:
    """Create a zone switch label string."""
    if id == 0:
        return "PGM"
    return f"X{id:0>2}"

def create_device_label(prefix: str, id: int) -> str:
    """Create a device label string."""
    return f"{prefix}{id:02d}"

def create_sensor_unique_id(panel_ident: int, id: int) -> str:
    """Create a zone sensor unique_id string."""
    return slugify(create_base_prefix(panel_ident) + create_sensor_label(id))

def create_siren_unique_id(panel_ident: int, id: int) -> str:
    """Create a zone sensor unique_id string."""
    return slugify(create_base_prefix(panel_ident) + create_siren_label(id))

def create_switch_unique_id(panel_ident: int, id: int) -> str:
    """Create a zone switch unique_id string."""
    return slugify(create_base_prefix(panel_ident) + create_switch_label(id))

def create_device_unique_id(panel_ident: int, prefix: str, id: int) -> str:
    """Create a device unique_id string."""
    return slugify(create_base_prefix(panel_ident) + create_device_label(prefix, id))

async def kill_asyncio_task(
    task: asyncio.Task[None] | None,
    timeout: float = 1.0,
) -> bool:
    """Cancel and await a task with timeout protection."""
    if not task or task.done():
        return True  # Consider this success as the task is non existant
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout)
    except TimeoutError:
        return False  # Timed out so assume unsuccessful
    except asyncio.CancelledError:
        # Catch it but do nothing
        pass
    return True  # No exception

def getAlarmPanelUniqueIdent(panel_ident: int) -> str:
    """Get alarm panel unique ident."""
    if panel_ident > 0:
        return VISONIC_UNIQUE_NAME + " P" + str(panel_ident)
    return VISONIC_UNIQUE_NAME

def to_string(array_alpha: bytearray, gap = " ") -> str:
    """Convert bytearray to string."""
    #    return ("".join(("%02x"+gap) % b for b in array_alpha))[:-len(gap)] if len(gap) > 0 else ("".join("%02x" % b for b in array_alpha))
    if len(gap) > 0:
        return "".join(f"{b:02x}{gap}" for b in array_alpha)[:-len(gap)]
    return "".join(f"{b:02x}" for b in array_alpha)

def parse_int_list(value: str | list[int]) -> list[int]:
    """Parse comma seperated string to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if value.strip() == "":
        return []
    # We could ignore 2 commas together but we want it to be done properly
    if value.find(",,") >= 0:
        raise ValueError
    result = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError as err:
            raise ValueError from err
    return result

def decode_code_from_dict_or_str(data: str | dict[str, Any] | None) -> str:
    """Decode the alarm code."""
    if data is not None:
        if isinstance(data, str):
            if len(data) == 4:
                return data
        elif "code" in data:
            if len(data["code"]) == 4:
                return data["code"]
    return ""

@callback
def _update_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    _kwargs: dict[str, Any],
) -> None:
    hass.config_entries.async_update_entry(entry, **_kwargs)

def update_config_entry_threadsafe(
    hass: HomeAssistant,
    entry: ConfigEntry,
    **kwargs: Any,
) -> None:
    """Update a config entry from any thread.

    Always schedules the update on Home Assistant's event loop.
    """
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    if not kwargs:
        return

    hass.loop.call_soon_threadsafe(
        _update_entry,
        hass,
        entry,
        kwargs,
    )
