"""Diagnostics support for Visonic Integration."""
from __future__ import annotations

import logging

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
#from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import VisonicConfigEntry
#from .client import VisonicClient
#from .const import (
#    DOMAIN,
#)

_LOGGER = logging.getLogger(__name__)

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics."""
    fred = {}
    cdata = entry.runtime_data
    B = cdata.client.getClientStatusDict()
    if cdata.client is not None:
        A = cdata.client.getPanelStatusDict()
        visonic = { **A, **B } 
        _LOGGER.error(f"async_get_config_entry_diagnostics {entry.as_dict()} {visonic}")
        fred = {
            "entry": entry.as_dict(),
            "visonic": visonic,
            "panel connected": 'yes' if cdata.client.isPanelConnected() else 'no',
            "sensor": cdata.client.dumpSensorsToStringList(),
            "switch": cdata.client.dumpSwitchesToStringList(),
            "clientlog": cdata.client.getStrLog(),
        }
    else:
        fred = {
            "entry": entry.as_dict(),
            "visonic": B,
            "panel connected": 'no',
        }

    return async_redact_data(fred, ("override_code","download_code","host","port"))
