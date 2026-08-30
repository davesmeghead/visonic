"""Global Visonic Data Types."""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypedDict, cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util.hass_dict import HassEntryKey

from .const import (
    CONF_PANEL_NUMBER,
    CONF_SERVER_NUMBER,
    DOMAIN,
    PANELS,
    SERVERS,
    TRANSLATE_EXCEPTION_NO_UNIQUE_NUMBER_IN_CONFIG,
    TRANSLATE_EXCEPTION_NUMBER_NOT_UNIQUE,
)

if TYPE_CHECKING:
    # Imports used purely to define a type for type checking:
    #    This ensures no cyclic imports
    from .coordinator_base import VisonicCoordinator
    from .server import ServerProtocol, TCPServerConnection
    from .visonic_entity_types import DeviceState, PanelState, SensorState, SwitchState

###################################################################################################
# This set of classes define the data that is saved in the HASS config entry and run_time data
###################################################################################################
@dataclass
class VisonicPanelData:
    """The class that is saved as Home Assistant runtime data (for clients)."""
    # Made it a class just in case I want to include more parameters in future
    # Coordinator
    coordinator: VisonicCoordinator
    # panel identifier
    panel_id: int

@dataclass
class VisonicServerData:
    """The class that is saved as Home Assistant runtime data (for servers)."""
    server: TCPServerConnection
    # server identifier
    server_id: int
    lock: asyncio.Lock

@dataclass
class VisonicDiscoveryData:
    """The class that is saved as Home Assistant runtime data (discoveries)."""
    # panel identifier
    panel_id: int
    account: str
    panel: str
    protocol: ServerProtocol
    transport: asyncio.Transport

# This class is the data that is saved in hass.data[VisonicEntryKey]
#    PANELS:       These are the various client entries in the configuration
#    SERVERS:      These are the various tcp server entries in the configuration
#    DISCOVERIES:  These are the discoveries made by the tcp server
class VisonicDomainData(TypedDict):
    """Visonic domain data."""
    PANELS: dict[str, VisonicPanelData]         # Panels
    SERVERS: dict[str, VisonicServerData]        # TCP Servers
    DISCOVERIES: dict[str, VisonicDiscoveryData] # TCP Discoveries from TCP Servers

# Create the types for the Configuration Parameter Entry
VisonicEntryKey: HassEntryKey[VisonicDomainData] = HassEntryKey(DOMAIN)
type VisonicConfigEntry = ConfigEntry[VisonicPanelData]

def get_panels(data: VisonicDomainData) -> list[int]:
    """Return a list of known Panels."""
    return [entry.panel_id for entry in data[PANELS].values()]

def get_servers(data: VisonicDomainData) -> list[int]:
    """Return a list of known Servers."""
    return [entry.server_id for entry in data[SERVERS].values()]

def get_panel_by_id(hass: HomeAssistant, panel_id: int) -> VisonicPanelData | None:
    """Find VCD by panel id."""
    data = hass.data[VisonicEntryKey]
    for panel in data[PANELS].values():
        assert isinstance(panel, VisonicPanelData)  # runtime check
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
    ident = entry.data.get(conf)
    if ident is None:
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


# Data class that the coordinator uses to manage the data passed to the entities
@dataclass(frozen=True, slots=True)
class VisonicCoordinatorData:
    """Coordinator data for passing to entities. Make it frozen i.e. immutable."""

    # This is the data that is created as part of the coordinator data capture activities and used by all entities

    connected: bool = False      # Is the lower level connected to the panel
    ispowermaster: bool = False  # Is the panel confirmed as a powermaster
    mode: str = ""               # A string shown as an attribute to tell the user the status of the connection
    model: str | None = None     # The reported panel model
    statusdict: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    panelstate: PanelState | None = None  # Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    partition_armcode: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_show_keypad: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_code_arm_required: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_siren: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    partition_dict: Mapping[int, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    zones: Mapping[int, SensorState] = field(default_factory=lambda: MappingProxyType({}))
    switch: Mapping[int, SwitchState] = field(default_factory=lambda: MappingProxyType({}))
    device: Mapping[int, DeviceState] = field(default_factory=lambda: MappingProxyType({}))

    def _convert_recursive(self, convert_to_name: bool, obj: Any) -> Any:
        """Recursively convert Mappings to dicts and sets to lists."""
        if obj is None:
            return None
        if convert_to_name and isinstance(obj, Enum):
            return obj.name.capitalize()
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Mapping):
            mapping_obj = cast(Mapping[object, object], obj)
            return {
                k: self._convert_recursive(convert_to_name, v)
                for k, v in mapping_obj.items()
            }
        if isinstance(obj, (set, list, tuple)):
            iterable_obj = cast(Iterable[object], obj)
            return [self._convert_recursive(convert_to_name, v) for v in iterable_obj]
        return obj

    def as_dict(self, convert_to_name: bool = False) -> dict[str, Any]:
        """Return a fully mutable dictionary representation."""
        return {
            f.name: self._convert_recursive(convert_to_name, getattr(self, f.name))
            for f in fields(self)
        }
