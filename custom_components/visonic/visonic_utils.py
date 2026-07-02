"""Simple Utility Functions for Home Assistant integration."""

###################################################################################
############### Utility functions associated with hass and hass.data  #############
###################################################################################

from homeassistant.config_entries import ConfigEntry, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_PANEL_NUMBER,
    CONF_SERVER_NUMBER,
    DOMAIN,
    PANELS,
    SERVERS,
    TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG,
    TRANSLATE_EXCEPTION_NUMBER_NOT_UNIQUE,
)
from .visonic_types import VisonicConfigData, VisonicDomainData, VisonicEntryKey


def get_panels(data: VisonicDomainData) -> list[int]:
    """Return a list of known Panels."""
    return [
        entry.panel_id
        for entry in data[PANELS].values()
    ]

def get_servers(data: VisonicDomainData) -> list[int]:
    """Return a list of known Servers."""
    return [
        entry.server_id
        for entry in data[SERVERS].values()
    ]

def get_panel_by_id(hass: HomeAssistant, panel_id: int) -> VisonicConfigData | None:
    """Find VCD by panel id."""
    data = hass.data[VisonicEntryKey]
    for panel in data[PANELS].values():
        assert isinstance(panel, VisonicConfigData)  # runtime check
        if panel.panel_id == panel_id:
            return panel
    return None

#def get_server_by_id(hass: HomeAssistant, server_id: int) -> VisonicServerData | None:
#    """Find VCD by panel id."""
#    data = hass.data[VisonicEntryKey]
#    for server in data[SERVERS].values():
#        assert isinstance(server, VisonicServerData)  # runtime check
#        if server.server_id == server_id:
#            return server
#    return None

def create_key(account: str, panel: str) -> str | None:
    """Create key function."""
    return f"{account}_{panel}" if account and panel else None

def get_next_panel_id(hass: HomeAssistant) -> int:
    """Get a unique panel number."""
    used = set(get_panels(hass.data[VisonicEntryKey]))
    i = 0
    while i in used:
        i += 1
    return i

#def is_panel_id_unique(hass: HomeAssistant, panel: int) -> bool:
#    """Get a unique panel number."""
#    used = set(get_panels(hass.data[VisonicEntryKey]))
#    return panel not in used

def _check_ident_valid(entry: ConfigEntry, valid_idents: list[int], conf: str, mess: str) -> int:
    if (ident := entry.data.get(conf, entry.data.get(conf))) is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG,
            translation_placeholders={"ident": mess},

        )
    # When here, server_id/panel_id should be unique in the hubs configured so far.
    if ident in valid_idents:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_NUMBER_NOT_UNIQUE,
            translation_placeholders={"ident": mess, "ref": ident},
        )
    return ident

async def check_panel_is_unique(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Ensure that the user selected panel id is unique. This must be enforced."""
    return _check_ident_valid(entry, get_panels(hass.data[VisonicEntryKey]), CONF_PANEL_NUMBER, "panel")

async def check_server_is_unique(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Ensure that the user selected server id is unique. This must be enforced."""
    return _check_ident_valid(entry, get_servers(hass.data[VisonicEntryKey]), CONF_SERVER_NUMBER, "server")
