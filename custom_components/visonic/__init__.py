"""Integration for a Visonic PowerMax or PowerMaster Alarm System."""

from copy import deepcopy
import logging
from types import MappingProxyType
from typing import Any

from probatio import UNDEFINED as PROBATIO_UNDEFINED

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, CONF_NAME, CONF_PATH, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv

try:
    from homeassistant.helpers.typing import Undefined as TYPE_UNDEFINED
except ImportError:
    from homeassistant.helpers.typing import UNDEFINED as TYPE_UNDEFINED

from .connection_test import ConnectionTest

# Most of the CONF_ are only imported because of the migration function from one version to the next
from .const import (
    CONF_ALARM_NOTIFICATIONS,
    CONF_ARM_CODE_AUTO,
    CONF_ARM_CODE_AUTO_KEYPAD_ENTITY,
    CONF_DEVICE_BAUD,
    CONF_EMER_OFF_DELAY,
    CONF_EMULATION_MODE,
    CONF_ENABLE_KEYPAD_ALARM_ENTITY,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_EXCLUDE_SENSOR,
    CONF_EXCLUDE_SWITCH,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_MOTION_OFF_DELAY,
    CONF_PANEL_NUMBER,
    CONF_USAGE,
    DEFAULT_DEVICE_BAUD,
    DOMAIN,
    FORM_CLOUD,
    FORM_DEVICE,
    FORM_DEVICE_NO_PANEL,
    FORM_ETHERNET,
    FORM_PARAM10,
    FORM_PARAM11,
    FORM_PARAM12,
    FORM_PARAM13,
    FORM_PARAM14,
    FORM_PARAM15,
    FORM_POWERLINK,
    FORM_SERIAL,
    PANELS,
    PLATFORMS,
    TEXT_TITLE,
    TRANSLATE_EXCEPTION_AUTHORISATION_FAILURE,
    TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
    TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
    TRANSLATE_EXCEPTION_PANEL_COORDINATOR_MISSING,
)
from .coordinator_base import VisonicCoordinator
from .coordinator_cloud import VisonicCloudCoordinator
from .coordinator_direct import VisonicDirectCoordinator
from .create_schema import FormItems, build_config_items
from .exceptions import VisonicAuthException, VisonicException
from .log_events import logEvents
from .services import async_register_services
from .utils import format_int_list
from .visonic_data_types import (
    VisonicDomainData,
    VisonicEntryKey,
    VisonicPanelData,
    check_panel_is_unique,
    get_next_panel_id,
)
from .visonic_types import AvailableNotifications, DeviceType, EmulationMode

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def terminate_coordinator(coordinator: VisonicCoordinator | None):
    """Terinate the coordinator."""
    if coordinator is not None:
        if coordinator.data is not None:
            coordinator.data = None  # stop all activity in the hub
        _LOGGER.debug("........... Terminating Panel Connection")
        await coordinator.async_panel_stop()
        _LOGGER.debug("........... Terminating Home Assistant Coordinator")
        await coordinator.async_shutdown()


async def async_setup(hass: HomeAssistant, _base_config: dict[str, Any]) -> bool:
    """Set up the visonic component."""
    _LOGGER.info("Starting Visonic Component")
    # Clear all data
    hass.data.setdefault(VisonicEntryKey, {PANELS: {}})
    # Register the services
    await async_register_services(hass)
    return True


async def async_setup_client(
    hass: HomeAssistant, entry: ConfigEntry, device_type: DeviceType, panel_id: int
) -> bool:
    """Setup a client that either:.

    1. Connects directly to a visonic panel using ethernet/wifi or rs232/serial/USB hardware.
    2. Connects to the visonic cloud server
    3. Connects to the API in Home Assistant for an ESPHome device with a serial_proxy
    """

    async def close_connection(data: VisonicDomainData, coordinator):
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        await terminate_coordinator(coordinator)
        if data:
            data[PANELS].pop(entry.entry_id, None)
        entry.runtime_data = None

    coordinator = None
    data: VisonicDomainData = hass.data[VisonicEntryKey]
    try:
        # Create the event logger and coordinator for this panel
        event_logger = logEvents(hass, entry, _LOGGER, panel_id)
        if device_type == DeviceType.CLOUD:
            # create coordinator to the cloud server
            coordinator = VisonicCloudCoordinator(hass, entry, panel_id, event_logger)
        else:
            # create coordinator to the panel
            coordinator = VisonicDirectCoordinator(hass, entry, panel_id, event_logger)
        # The direct coordinator needs these setting up before the async_panel_connect call
        vcd = VisonicPanelData(coordinator, panel_id)
        entry.runtime_data = vcd
        data.setdefault(PANELS, {})[entry.entry_id] = vcd
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if await coordinator.async_panel_connect():
            if isinstance(coordinator, VisonicCloudCoordinator):
                coordinator.start_polling()
            # Set the runtime data defaults
            return True
        await close_connection(data, coordinator)
    # Catch any exception and report it as a config error, connection failure
    except VisonicAuthException as error:
        await close_connection(data, coordinator)
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_AUTHORISATION_FAILURE,
            translation_placeholders={"panel_id": panel_id},
        ) from error
    except (VisonicException, TimeoutError, ConnectionError, OSError) as error:
        await close_connection(data, coordinator)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
            translation_placeholders={"panel_id": panel_id},
        ) from error
    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up visonic from a config entry. This is the main entry point."""

    # This function is called with the flow data to create a hub connection to the alarm panel
    device_type: str = entry.data.get(CONF_TYPE)
    if device_type is None:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
        )
    try:
        device_type_enum = DeviceType(device_type)
    except (TypeError, ValueError):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
        ) from None

    unique_id = await check_panel_is_unique(hass, entry)

    _LOGGER.info(
        "***************** creating connection %d to a %s device ******************",
        unique_id,
        device_type_enum,
    )
    _LOGGER.debug("Entry id=%s", entry.entry_id)
    # _LOGGER.debug(f" Entry data={entry.data}   options={entry.options}")

    # Test the connection to the device, if it fails then ask the config entry to try again "later"
    connection_tester = ConnectionTest()
    error = await connection_tester.test_connection(
        device_type=device_type_enum, data=entry.data
    )
    if error is not None:
        # Connection error
        _LOGGER.info(
            "***************** creating connection %d to a %s device, test connection failed so trying again later ******************",
            unique_id,
            device_type_enum,
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=error,
            translation_placeholders={"panel_id": unique_id},
        )

    # Connection test success so create the necessary servers and clients
    #    The test does not include cloud authentication, just a TCP connection test to the remote server
    match device_type_enum:
        case DeviceType.ETHERNET | DeviceType.SERIAL | DeviceType.CLOUD:
            success = await async_setup_client(hass, entry, device_type_enum, unique_id)
            _LOGGER.info(
                "***************** creating connection %d to a %s device, success=%s ******************",
                unique_id,
                device_type_enum,
                success,
            )
            if not success:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key=TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
                    translation_placeholders={"panel_id": unique_id},
                )
            return
    raise ConfigEntryError(
        translation_domain=DOMAIN,
        translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:  # noqa: C901
    """Migrate old schema configuration entry to new."""
    # This function is called when VERSION changes in the ConfigFlow
    # If the config schema ever changes then use this function to convert from old to new config parameters
    version = entry.version
    changed = False

    # Removed from config settings, the user now selects the target/desired emulation mode
    CONF_FS = "force_standard"  # noqa: N806

    _LOGGER.info("**************** migrating connection ******************")
    _LOGGER.info("Migrating from version %s", version)

    data = deepcopy(dict(MappingProxyType(entry.data)))
    options = deepcopy(dict(MappingProxyType(entry.options)))

    if version == 1:
        # Leave CONF_FS in place but use it to add CONF_EMULATION_MODE
        _LOGGER.debug(" CONF_FS from %s", options.get(CONF_FS))
        if CONF_FS in options and isinstance(options[CONF_FS], bool):
            _LOGGER.debug(" CONF_FS from %s", options[CONF_FS])
            if options[CONF_FS]:
                _LOGGER.info("  Force standard set, using %s", EmulationMode.STANDARD)
                options[CONF_EMULATION_MODE] = EmulationMode.STANDARD
            else:
                _LOGGER.info(
                    "  Force standard not set, using %s", EmulationMode.POWERLINK
                )
                options[CONF_EMULATION_MODE] = EmulationMode.POWERLINK
            _LOGGER.info(" Emulation mode set to %s", options[CONF_EMULATION_MODE])
        version = 2
        changed = True

    if version == 2:
        CONF_FORCE_AUTOENROLL = "force_autoenroll"  # noqa: N806
        CONF_AUTO_SYNC_TIME = "sync_time"  # noqa: N806
        if CONF_FS in options:
            del options[CONF_FS]  # decided to remove it
        if CONF_FORCE_AUTOENROLL in options:
            del options[CONF_FORCE_AUTOENROLL]
        if CONF_AUTO_SYNC_TIME in options:
            del options[CONF_AUTO_SYNC_TIME]
        _LOGGER.debug(" Updated config settings to remove unused data")

        if CONF_MOTION_OFF_DELAY in options:
            # Add the 2 new timeouts with the same values as the old setting
            options[CONF_MAGNET_CLOSED_DELAY] = options[CONF_MOTION_OFF_DELAY]
            options[CONF_EMER_OFF_DELAY] = options[CONF_MOTION_OFF_DELAY]
            _LOGGER.info("   Added additional trigger delay settings")

        options[CONF_ALARM_NOTIFICATIONS] = [
            AvailableNotifications.CONNECTION,
            AvailableNotifications.SIREN,
        ]
        _LOGGER.debug(" Alarm Notification list set to default")
        version = 3
        changed = True

    if version in [3, 4, 5]:
        if CONF_DEVICE not in data and CONF_PATH in data:
            data[CONF_DEVICE] = data.pop(CONF_PATH)
        if data.get(CONF_TYPE) == "usb":
            data[CONF_TYPE] = "serial"

        if CONF_PANEL_NUMBER not in options and CONF_PANEL_NUMBER not in data:
            # We have to assume that multiple panels will be updated at the same time, otherwise it gets complicated
            # Create a panel number that is unique
            data[CONF_PANEL_NUMBER] = get_next_panel_id(hass)
        if (
            CONF_ESPHOME_ENTITY_SELECT not in options
            and CONF_ESPHOME_ENTITY_SELECT not in data
        ):
            data[CONF_ESPHOME_ENTITY_SELECT] = ""

        # Split the data and options correctly:
        #     data is defined by the user on first creation and then only by reconfigure
        #     options can be edited easily and are pushed in to the integration without reload

        # Create a new data and options
        data_out: dict[str, Any] = {}
        options_out: dict[str, Any] = {}

        # These contain all data and options settings
        data_items = [
            FORM_DEVICE,
            FORM_DEVICE_NO_PANEL,
            FORM_ETHERNET,
            FORM_SERIAL,
            FORM_CLOUD,
            FORM_POWERLINK,
        ]
        option_items = [
            FORM_PARAM10,
            FORM_PARAM11,
            FORM_PARAM12,
            FORM_PARAM13,
            FORM_PARAM14,
            FORM_PARAM15,
        ]

        # Build a set with all the "data" keys. Using a set removes duplication.
        key_data_list: set[str] = set()
        for d in data_items:
            key_data_list.update(FormItems[d])

        # Build a set with all the "option" keys. Using a set removes duplication.
        key_option_list: set[str] = set()
        for o in option_items:
            key_option_list.update(FormItems[o])

        # Set all data and option values to their defaults
        config_items = (
            build_config_items()
        )  # get a temporary list of all items to get their default
        missing = (key_data_list | key_option_list) - config_items.keys()
        if missing:
            _LOGGER.warning("Missing config items: %s", missing)

        for key in key_data_list:
            default = config_items[key].default
            if default in [TYPE_UNDEFINED, PROBATIO_UNDEFINED]:
                _LOGGER.info("Missing config data key: %s", key)
                data_out[key] = None
            else:
                data_out[key] = deepcopy(default)

        for key in key_option_list:
            default = config_items[key].default
            if default in [TYPE_UNDEFINED, PROBATIO_UNDEFINED]:
                _LOGGER.info("Missing config options key: %s", key)
                options_out[key] = None
            else:
                options_out[key] = deepcopy(default)

        # add title and name in to the set (not user settings but saved by the integration)
        key_data_list.add(TEXT_TITLE)
        data_out[TEXT_TITLE] = ""
        key_data_list.add(CONF_NAME)
        data_out[CONF_NAME] = ""

        # By here we have 2 dicts with all their config key settings and default values

        CONF_EXCLUDE_X10 = "exclude_x10"  # noqa: N806
        # exclude_list explanation:
        #   CONF_EXCLUDE_X10 changed to CONF_EXCLUDE_SWITCH after the for loops
        #   CONF_DEVICE_BAUD is set after the for loops as it is no longer a user setting
        #   CONF_NAME and CONF_USAGE are only used in the language files for discovery and zero_conf
        exclude_list = (CONF_EXCLUDE_X10, CONF_DEVICE_BAUD, CONF_NAME, CONF_USAGE)
        # Use the existing value to update the defaults from the current data and options
        for key, value in data.items():
            if key in key_data_list:
                data_out[key] = value
            elif key in key_option_list:
                options_out[key] = value
            elif key not in exclude_list:
                _LOGGER.warning("User data setting %s not in either config list", key)
        for key, value in options.items():
            if key in key_data_list:
                data_out[key] = value
            elif key in key_option_list:
                options_out[key] = value
            elif key not in exclude_list:
                _LOGGER.warning(
                    "User options setting %s not in either config list", key
                )

        if "force_numeric_keypad" in options:
            options_out["force_numeric_keypad"] = options["force_numeric_keypad"]

        # Baud has been removed from the user config, but we need to copy the old value across
        data_out[CONF_DEVICE_BAUD] = data.get(CONF_DEVICE_BAUD, DEFAULT_DEVICE_BAUD)
        # New CONF names have been added but this is the only change of CONF name that has been made
        data_out[CONF_EXCLUDE_SWITCH] = data.get(
            CONF_EXCLUDE_X10, ""
        )  # changed to CONF_EXCLUDE_SWITCH
        # Make sure that they are strings and not lists
        try:
            data_out[CONF_EXCLUDE_SENSOR] = format_int_list(
                data_out.get(CONF_EXCLUDE_SENSOR, "")
            )
        except ValueError:
            _LOGGER.info(
                "Migration of exclude sensor list to version %s unsuccessful, list contains invalid values; setting it to an empty string",
                entry.version,
            )
            data_out[CONF_EXCLUDE_SENSOR] = ""
        try:
            data_out[CONF_EXCLUDE_SWITCH] = format_int_list(
                data_out.get(CONF_EXCLUDE_SWITCH, "")
            )
        except ValueError:
            _LOGGER.info(
                "Migration of exclude switch list to version %s unsuccessful, list contains invalid values; setting it to an empty string",
                entry.version,
            )
            data_out[CONF_EXCLUDE_SWITCH] = ""

        version = 6
        data = data_out
        options = options_out
        changed = True

    if version == 6:
        if CONF_PATH in data:
            data[CONF_DEVICE] = data.pop(CONF_PATH)
        if data.get(CONF_TYPE, "") == "usb":
            data[CONF_TYPE] = "serial"
        data[CONF_ENABLE_KEYPAD_ALARM_ENTITY] = options.pop(
            "force_numeric_keypad", False
        )
        options[CONF_ARM_CODE_AUTO_KEYPAD_ENTITY] = options[CONF_ARM_CODE_AUTO]
        version = 7
        changed = True

    if changed:
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=version
        )
        _LOGGER.info("Migration to version %s successful", entry.version)
    else:
        _LOGGER.info("Migration. Nothing changed, version is currently %s", version)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unload visonic entry."""
    # This function is called to terminate a hub connection to the alarm panel

    # _LOGGER.info(
    #    "UNLOAD ENTRY CALLED\n%s",
    #    "".join(traceback.format_stack())
    # )

    _LOGGER.info("**************** terminating connection ****************")
    p = ""

    device_type: str = entry.data.get(CONF_TYPE)
    if device_type is None:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
        )
    try:
        device_type_enum = DeviceType(device_type)
    except (TypeError, ValueError):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
        ) from None

    data: VisonicDomainData | None = hass.data.get(VisonicEntryKey)

    _LOGGER.info("........... async_unload_entry %s ...........", device_type_enum)

    if device_type_enum in (DeviceType.ETHERNET, DeviceType.SERIAL, DeviceType.CLOUD):
        # Do the opposite of async_setup_client
        vcd: VisonicPanelData | None = (
            data[PANELS].get(entry.entry_id) if data is not None else None
        )
        _LOGGER.debug("........... Unloading Platforms")
        try:
            await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
        finally:
            if vcd is not None:
                _LOGGER.debug("........... Unloading Client %s", vcd.panel_id)
                p = str(vcd.panel_id)
                try:
                    await terminate_coordinator(vcd.coordinator)
                finally:
                    if data is not None:
                        data[PANELS].pop(entry.entry_id, None)
        if vcd is None:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key=TRANSLATE_EXCEPTION_PANEL_COORDINATOR_MISSING,
            )
        _LOGGER.debug(
            "************** Connection %s terminate success ***************", p
        )
        return

    raise ConfigEntryError(
        translation_domain=DOMAIN,
        translation_key=TRANSLATE_EXCEPTION_INVALID_CONFIG_ENTRY,
    )
