"""Diagnostics support for Visonic Integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .client import VisonicClient
from .const import (
    DOMAIN,
    DOMAINCLIENT,
    DOMAINDATA,
)

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics."""
    client : VisonicClient = hass.data[DOMAIN][DOMAINCLIENT][entry.entry_id]
    data = {}
    if client is not None:
        visonic = { **client.getPanelStatus(False), **hass.data[DOMAIN][DOMAINDATA][entry.entry_id] }
        data = {
            "entry": entry.as_dict(),
            "visonic": visonic,
            "panel connected": 'yes' if client.isPanelConnected() else 'no',
            "sensor": client.dumpSensorsToStringList(),
            "switch": client.dumpSwitchesToStringList(),
            "clientlog": client.getStrLog(),
        }
    else:
        visonic = hass.data[DOMAIN][DOMAINDATA][entry.entry_id]
        data = {
            "entry": entry.as_dict(),
            "visonic": visonic,
            "panel connected": 'no',
        }

    return async_redact_data(data, ("override_code","download_code","host","port"))
