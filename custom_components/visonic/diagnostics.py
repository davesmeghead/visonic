"""Diagnostics support for Visonic Integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import CONF_DOWNLOAD_CODE, CONF_SERVER_HOST, CONF_SERVER_PORT
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicException
from .visonic_data_types import VisonicPanelData

REDACT_ME = (CONF_DOWNLOAD_CODE, CONF_SERVER_HOST, CONF_SERVER_PORT,
             CONF_HOST, CONF_PORT, CONF_DEVICE,
             )

async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics."""
    vcd: VisonicPanelData = entry.runtime_data
    coordinator: VisonicCoordinator = vcd.coordinator
    if coordinator is None:
        raise VisonicException("Diagnostics has been given invalid coordinator", 101)

    if not coordinator:
        diagdata = {
            "integration connected": "no",
            "panel connected": "no",
        }
        return async_redact_data(diagdata, REDACT_ME)

    diagdata = await coordinator.get_diagnostic_data()
    ev = {"entry": entry.options, **diagdata}
    return async_redact_data(ev, REDACT_ME)
