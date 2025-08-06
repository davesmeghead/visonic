"""Create a connection to a Visonic PowerMax or PowerMaster Alarm System and Create a Simple Entity to Report Status only."""

from typing import Any

import logging
from enum import IntEnum
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import Entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.siren import DOMAIN as SIREN_DOMAIN
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.components.siren import SirenEntity, SirenEntityFeature
#from homeassistant.config_entries import ConfigEntry

from .client import VisonicClient
from . import VisonicConfigEntry
from .const import (
    DOMAIN,
    VISONIC_TRANSLATION_KEY,
    MANUFACTURER,
    PANEL_ATTRIBUTE_NAME,
)

from .pyconst import AlPanelStatus, AlSensorDevice
from .pyenum import EventDataEnum

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1
SUPPORT_FLAGS = SirenEntityFeature.TURN_OFF | SirenEntityFeature.TURN_ON

async def async_setup_entry(
    hass: HomeAssistant,
    entry: VisonicConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Visonic Alarm Siren"""
    #_LOGGER.debug(f"[async_setup_entry] start")

    @callback
    def async_add_siren() -> None:
        """Add Visonic Siren"""
        entities: list[Entity] = []
        entities.append(VisonicSiren(hass = hass, client = entry.runtime_data.client))
        _LOGGER.debug(f"[async_setup_entry] adding entity")
        async_add_entities(entities)

    entry.runtime_data.dispatchers[SIREN_DOMAIN] = async_dispatcher_connect(hass, f"{DOMAIN}_{entry.entry_id}_add_{SIREN_DOMAIN}", async_add_siren )
    #_LOGGER.debug("[async_setup_entry] exit")

class VisonicSiren(SirenEntity):
    """Representation of a visonic siren device."""

    _attr_translation_key: str = VISONIC_TRANSLATION_KEY
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, client: VisonicClient):
        """Initialize a Visonic security alarm."""
        self._client = client
        self.hass = hass
        client.onChange(callback = self.onClientChange)
        #self._partition_id = partition_id
        self._mystate = False
        pname = client.getMyString()
        self._myname = pname + "s01"
        self._panel = client.getPanelID()
        self.external = False
        self.trigger = ""
        self.alarmReason = ""
        _LOGGER.debug(f"[VisonicSiren] panel {self._panel}, siren {self._myname}")
        self._attr_supported_features = SUPPORT_FLAGS
        self._attr_is_on = False
#        self._attr_available_tones = None
#        if available_tones is not None:
#            self._attr_supported_features |= SirenEntityFeature.TONES
#        if support_volume_set:
#            self._attr_supported_features |= SirenEntityFeature.VOLUME_SET
#        if support_duration:
#            self._attr_supported_features |= SirenEntityFeature.DURATION
        self._attr_available_tones = None # available_tones

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the siren on."""
        self.external = True
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the siren off."""
        self.external = False
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    async def async_will_remove_from_hass(self):
        """Remove from hass."""
        await super().async_will_remove_from_hass()
        _LOGGER.debug(f"[async_will_remove_from_hass] {self._myname} panel {self._panel}")
        self._client = None

    # The callback handler from the client. All we need to do is schedule an update.
    def onClientChange(self):
        """HA Event Callback."""
        #_LOGGER.debug(f"siren onChange {self.entity_id=}   {self.available=}")
        if self.hass is not None and self.entity_id is not None:
            self.schedule_update_ha_state(True)

    def isPanelConnected(self) -> bool:
        """Are we connected to the Alarm Panel."""
        # If we are starting up or have been removed then assume we need a valid code
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        #_LOGGER.debug(f"alarm control panel available {self.entity_id=}")
        if self._client is None:
            return False
        return self._client.isPanelConnected()

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        #_LOGGER.debug(f"alarm control panel unique_id {self.entity_id=}")
        return slugify(self._myname)

    @property
    def name(self):
        """Return the name of the alarm."""
        #_LOGGER.debug(f"alarm control panel name {self.entity_id=}")
        return self._myname

    @property
    def device_info(self):
        """Return information about the device."""
        if self._client is not None:
            return {
                "manufacturer": MANUFACTURER,
                "identifiers": {(DOMAIN, self._myname)},
                "name": f"{self._myname}",
#                "model": pm,
                # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
            }
        return {
            "manufacturer": MANUFACTURER,
            "identifiers": {(DOMAIN, self._myname)},
            "name": f"{self._myname}",
            "model": None,
            # "model": "Alarm Panel",
            # "via_device" : (DOMAIN, "Visonic Intruder Alarm"),
        }

    def update(self):
        """Get the state of the device."""
        #_LOGGER.debug(f"[update] {self.entity_id=}")
        oldstate = self._mystate
        self._mystate = False   # If panel disconnected then set to False
        if self.isPanelConnected():
            stl = self._client.getSirenTriggerList()
            ptu = self._client.getPartitionsInUse()
            
            self.alarmReason = ""

            if ptu is None:
                isa, dev = self._client.isSirenActive()
                psd = self._client.getPanelStatusDict()
                #_LOGGER.debug(f"[update]    dict {psd}")
                if EventDataEnum.ALARM in psd:
                    self.alarmReason = psd[EventDataEnum.ALARM]
            else:
                isa = False
                dev = None
                for p in ptu:
                    a, b = self._client.isSirenActive(p)
                    #_LOGGER.debug(f"[update]    {self.entity_id=}  {p=}  {a=}  {b}")
                    if a:
                        isa = True
                        dev = b
                    psd = self._client.getPanelStatusDict(p)
                    #_LOGGER.debug(f"[update]    partition {p}  dict {psd}")
                    if EventDataEnum.ALARM in psd and psd[EventDataEnum.ALARM] in stl:
                        self.alarmReason = psd[EventDataEnum.ALARM]
                #_LOGGER.debug(f"[update]    {self.entity_id=}  {isa=}   {dev=}")

            if isa or self.alarmReason in stl:
                self._mystate = True
                _LOGGER.debug(f"[update]    siren triggered due to {isa=} or {self.alarmReason}")

            if not oldstate and self._mystate:
                self.trigger = ""
                # only set this when self._mystate goes from False to True
                if isa and dev is not None:
                    dname = dev.createFriendlyName()
                    pname = self._client.getMyString()
                    name = pname.lower() + dname.lower()
                    self.trigger = name

    @property
    def is_on(self) -> bool:
        """Return true if siren is on."""
        return self._mystate or self.external

    @property
    def extra_state_attributes(self):  #
        """Return the state attributes of the device."""
        trigger = "trigger"
        attr = {}
        attr[EventDataEnum.ALARM] = "none"
        attr[trigger] = ""
        if self.external:
            attr[EventDataEnum.ALARM] = "external"
        elif self._mystate:
            attr[EventDataEnum.ALARM] = self.alarmReason
            attr[trigger] = self.trigger
            
        attr[PANEL_ATTRIBUTE_NAME] = self._panel
        return attr
