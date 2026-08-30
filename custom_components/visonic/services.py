"""Services/Actions for the Visonic PowerMax or PowerMaster Alarm System."""

import asyncio
import logging

import voluptuous as vol

from homeassistant.const import ATTR_CODE, ATTR_ENTITY_ID, CONF_COMMAND
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import NoEntitySpecifiedError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    ALARM_PANEL_COMMAND,
    ALARM_PANEL_EVENTLOG,
    ALARM_PANEL_RECONNECT,
    ALARM_PANEL_SWITCH,
    ALARM_PANEL_ZONEINFO,
    ALARM_SENSOR_BYPASS,
    ALARM_SENSOR_IMAGE,
    ATTR_BYPASS,
    ATTR_DURATION,
    CONF_SWITCH_COMMAND,
    DOMAIN,
    PANELS,
    TRANSLATE_EXCEPTION_SERVICE_CONFIG_ENTRY_NOT_FOUND,
    TRANSLATE_EXCEPTION_SERVICE_DEVICE_NO_IN_CONFIG,
    TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_DEVICE,
    TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_REGISTRY,
    TRANSLATE_EXCEPTION_SERVICE_INVALID_DEVICE_FOR_ENTITY,
    TRANSLATE_EXCEPTION_SERVICE_NO_ENTITY_SPECIFIED,
)
from .coordinator_base import VisonicCoordinator
from .exceptions import VisonicException
from .visonic_data_types import VisonicEntryKey, VisonicPanelData
from .visonic_types import AlarmPanelCommand, AlarmSwitchCommand

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)  # type: ignore  # noqa: PGH003

# The 7 schemas for the HA service calls
#     Get the Panels Event Log
#     Bypass/Arm individual zone sensors
#     Turn switches PGM/Switch on/off
#     Reconnect to the panel if there is a disconnection
#     Arm/Disarm the panel
#     Request an image from a Camera PIR zone sensor
#     Request status information

ALARM_SCHEMA_EVENTLOG = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_COMMAND = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_COMMAND): vol.In(
            AlarmPanelCommand.members()
        ),
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_SWITCH = vol.Schema(
    {
        vol.Required(
            ATTR_ENTITY_ID
        ): cv.entity_ids,  # pyright: ignore[reportUnknownMemberType]
        vol.Required(CONF_SWITCH_COMMAND): vol.In(
            AlarmSwitchCommand.members()
        ),
    }
)

ALARM_SCHEMA_RECONNECT = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

ALARM_SCHEMA_BYPASS = vol.Schema(
    {
        vol.Required(
            ATTR_ENTITY_ID
        ): cv.entity_ids,  # pyright: ignore[reportUnknownMemberType]
        vol.Required(ATTR_BYPASS, default=False): cv.boolean,
        vol.Optional(ATTR_CODE, default=""): cv.string,
    }
)

ALARM_SCHEMA_ZONE_INFO = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)

ALARM_SCHEMA_IMAGE = vol.Schema(
    {
        # Accept a list as well as a single entity, so one press can ask every camera. The panel
        # serialises image transfers anyway, and the coordinator queues them.
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_DURATION, default=5): cv.positive_int,
    }
)

def get_config_from_call(
    hass: HomeAssistant,
    call: ServiceCall,
    allow_multiple: bool,
    service_name: str,
) -> dict[str, VisonicPanelData]:
    """Get ids."""
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    if not entity_ids:
        raise NoEntitySpecifiedError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_SERVICE_NO_ENTITY_SPECIFIED,
            translation_placeholders={"service": service_name},
        )

    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    retval: dict[str, VisonicPanelData] = {}
    for entity_id in entity_ids:
        entity_entry = ent_reg.async_get(entity_id)
        if not entity_entry:
            raise NoEntitySpecifiedError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_REGISTRY,
                translation_placeholders={"service": service_name, "entity": entity_id},
            )
        if not entity_entry.device_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_SERVICE_ENTITY_NOT_IN_DEVICE,
                translation_placeholders={"service": service_name, "entity": entity_id},
            )

        device = dev_reg.async_get(entity_entry.device_id)
        if not device:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_SERVICE_INVALID_DEVICE_FOR_ENTITY,
                translation_placeholders={"service": service_name, "entity": entity_id},
            )
        if not device.config_entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_SERVICE_DEVICE_NO_IN_CONFIG,
                translation_placeholders={"service": service_name, "entity": entity_id},
            )

        entry_id = next(iter(device.config_entries))

        try:
            retval[entity_id] = hass.data[VisonicEntryKey][PANELS][entry_id]
        except KeyError as ex:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_SERVICE_CONFIG_ENTRY_NOT_FOUND,
                translation_placeholders={"service": service_name, "entity": entity_id},
            ) from ex

        if not allow_multiple:
            return retval
    return retval


async def async_register_services(hass: HomeAssistant):
    """Register the services for the visonic component."""

    async def async_service_panel_eventlog(call: ServiceCall) -> None:
        """Handler for event log service."""
        _LOGGER.info("Event log called")
        vcd_dict = get_config_from_call(hass, call, False, "event log")
        vcd = next(iter(vcd_dict.values()))
        coordinator: VisonicCoordinator = vcd.coordinator
        if coordinator is None:
            raise VisonicException("async_service_panel_eventlog has been given invalid coordinator", 101)
        await coordinator.async_service_panel_eventlog(call)

    async def async_service_panel_reconnect(call: ServiceCall) -> None:
        """Handler for panel reconnect service."""
        vcd_dict = get_config_from_call(hass, call, False, "panel reconnect")
        vcd = next(iter(vcd_dict.values()))
        coordinator: VisonicCoordinator = vcd.coordinator
        if coordinator is None:
            raise VisonicException("async_service_panel_reconnect has been given invalid coordinator", 101)
        await coordinator.async_service_panel_reconnect(
            call=call
        )  # user has explicitly asked for this

    async def async_service_panel_command(call: ServiceCall) -> ServiceResponse:
        """Handler for panel command service."""
        _LOGGER.info("Service Panel command called")
        vcd_dict = get_config_from_call(hass, call, False, "panel command")
        vcd = next(iter(vcd_dict.values()))
        coordinator: VisonicCoordinator = vcd.coordinator
        if coordinator is None:
            raise VisonicException("async_service_panel_command has been given invalid coordinator", 101)
        did_bypass_sensor = await coordinator.async_service_panel_command(call)
        _LOGGER.info("Service Panel command called - command complete, starting delay")
        # It takes up to a second for the command to be sent to the panel and then to get any changes in the sensor bypass state
        await asyncio.sleep(1.0)
        if did_bypass_sensor:
            await asyncio.sleep(0.2)
        _LOGGER.info(
            "Service Panel command called - getting panel info to return to HA"
        )
        return await coordinator.async_service_panel_zoneinfo(call)

    async def async_service_panel_switch(call: ServiceCall) -> None:
        """Handler for panel command service."""
        _LOGGER.info("Service Panel switch called")
        vcd_dict = get_config_from_call(hass, call, True, "panel switch")
        for vcd in vcd_dict.values():
            coordinator: VisonicCoordinator = vcd.coordinator
            if coordinator is None:
                raise VisonicException("async_service_panel_switch has been given invalid coordinator", 101)
            await coordinator.async_service_panel_switch(call)

    async def async_service_sensor_bypass(call: ServiceCall) -> None:
        """Handler for sensor bypass service."""
        _LOGGER.info("Service Panel sensor bypass called")
        vcd_dict = get_config_from_call(hass, call, True, "sensor bypass")
        for vcd in vcd_dict.values():
            coordinator: VisonicCoordinator = vcd.coordinator
            if coordinator is None:
                raise VisonicException("async_service_sensor_bypass has been given invalid coordinator", 101)
            await coordinator.async_service_sensor_bypass(call)

    async def async_service_panel_zoneinfo(call: ServiceCall) -> ServiceResponse:
        """Handler for panel zones service."""
        _LOGGER.info("Service Panel zones called")
        vcd_dict = get_config_from_call(hass, call, False, "panel info")
        vcd = next(iter(vcd_dict.values()))
        coordinator: VisonicCoordinator = vcd.coordinator
        if coordinator is None:
            raise VisonicException("async_service_panel_zoneinfo has been given invalid coordinator", 101)
        return await coordinator.async_service_panel_zoneinfo(call)

    async def async_service_sensor_image(call: ServiceCall) -> None:
        """Handler for sensor image service."""
        _LOGGER.info("Service Panel sensor image update called")
        vcd_dict = get_config_from_call(hass, call, False, "sensor image")
        vcd = next(iter(vcd_dict.values()))
        coordinator: VisonicCoordinator = vcd.coordinator
        if coordinator is None:
            raise VisonicException("async_service_sensor_image has been given invalid coordinator", 101)
        await coordinator.async_service_sensor_image(call)

    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_EVENTLOG,
        async_service_panel_eventlog,
        schema=ALARM_SCHEMA_EVENTLOG,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_COMMAND,
        async_service_panel_command,
        schema=ALARM_SCHEMA_COMMAND,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_SWITCH,
        async_service_panel_switch,
        schema=ALARM_SCHEMA_SWITCH,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_RECONNECT,
        async_service_panel_reconnect,
        schema=ALARM_SCHEMA_RECONNECT,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_SENSOR_BYPASS,
        async_service_sensor_bypass,
        schema=ALARM_SCHEMA_BYPASS,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_PANEL_ZONEINFO,
        async_service_panel_zoneinfo,
        schema=ALARM_SCHEMA_ZONE_INFO,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        ALARM_SENSOR_IMAGE,
        async_service_sensor_image,
        schema=ALARM_SCHEMA_IMAGE,
    )
