"""Diagnostics support for Visonic Integration."""
from __future__ import annotations
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VisonicConfigEntry
from .client import VisonicClient

_LOGGER = logging.getLogger(__name__)

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics."""
    diagdata = {}
    cdata = entry.runtime_data
    if cdata.client is not None:
        visonic = { } 
        client : VisonicClient = cdata.client
        if ( piu := client.getPartitionsInUse() ) is not None:
            partition = {}
            for p in piu:
                partition[f"partition {p}"] = client.getPanelStatusDict(p)
            B = client.getClientStatusDict()
            visonic = { **partition, **B } 
            #_LOGGER.error(f"async_get_config_entry_diagnostics {entry.as_dict()} {visonic}")
        else:
            A = client.getPanelStatusDict()
            B = client.getClientStatusDict()
            visonic = { **A, **B } 
            #_LOGGER.error(f"async_get_config_entry_diagnostics {entry.as_dict()} {visonic}")
        diagdata = {
            "entry": entry.as_dict(),
            "client connected": 'yes',
            "panel connected": 'yes' if client.isPanelConnected() else 'no',
            "visonic": visonic,
            "sensor": client.dumpSensorsToStringList(),
            "switch": client.dumpSwitchesToStringList(),
            "clientlog": client.getStrLog(),
        }
    else:
        diagdata = {
            "entry": entry.as_dict(),
            "client connected": 'no',
            "panel connected": 'no',
        }

    return async_redact_data(diagdata, ("download_code","host","port","path"))
