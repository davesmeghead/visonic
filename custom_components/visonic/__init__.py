"""Integration for a Visonic PowerMax or PowerMaster Alarm System."""

import asyncio
from copy import deepcopy
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import CONF_NAME, CONF_SOURCE, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cloud.coordinator_cloud import VisonicCloudCoordinator
from .connection_test import ConnectionTest

# Most of the CONF_ are only imported because of the migration function from one version to the next
from .const import (
    CONF_ALARM_NOTIFICATIONS,
    CONF_DEVICE_BAUD,
    CONF_EMER_OFF_DELAY,
    CONF_EMULATION_MODE,
    CONF_ESPHOME_ENTITY_SELECT,
    CONF_EXCLUDE_SWITCH,
    CONF_MAGNET_CLOSED_DELAY,
    CONF_MOTION_OFF_DELAY,
    CONF_PANEL_NUMBER,
    CONF_SERVER_HOST,
    CONF_SERVER_PORT,
    CONF_USAGE,
    DEFAULT_DEVICE_BAUD,
    DISCOVERIES,
    DOMAIN,
    FORM_CLOUD,
    FORM_DEVICE,
    FORM_ETHERNET,
    FORM_PARAM10,
    FORM_PARAM11,
    FORM_PARAM12,
    FORM_PARAM13,
    FORM_PARAM14,
    FORM_POWERLINK,
    FORM_SERIAL,
    FORM_TCP_DISCOVERED,
    FORM_TCP_SERVER,
    PANELS,
    PLATFORMS,
    SERVERS,
    TEXT_TITLE,
    TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
)
from .create_schema import FormItems, build_config_items
from .direct.coordinator_direct import VisonicDirectCoordinator
from .exceptions import VisonicException
from .log_events import logEvents
from .server import ServerProtocol, TCPServerConnection
from .services import async_register_services
from .visonic_types import (
    AvailableNotifications,
    DeviceType,
    EmulationMode,
    VisonicConfigData,
    VisonicDiscoveryData,
    VisonicDomainData,
    VisonicEntryKey,
    VisonicServerData,
)
from .visonic_utils import (
    check_panel_is_unique,
    check_server_is_unique,
    create_key,
    get_next_panel_id,
    get_panel_by_id,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, _base_config: dict[str, Any]) -> bool:
    """Set up the visonic component."""
    _LOGGER.info("Starting Visonic Component")
    # Clear all data
    hass.data.setdefault(VisonicEntryKey, {
        PANELS: {},
        SERVERS: {},
        DISCOVERIES: {}
    })
    # Register the services
    await async_register_services(hass)
    return True

async def async_setup_server(hass: HomeAssistant, entry: ConfigEntry, server_id: int) -> bool:
    """Setup a server that waits for a Powerlink Hardware connection from a visonic panel."""

    def panel_update_callback(
        account: str,
        panel: str,
        transport: asyncio.Transport | None,
        protocol: ServerProtocol | None,
        vp_ok: bool,
    ):
        key = create_key(account, panel)
        disc: VisonicDiscoveryData | None = hass.data[VisonicEntryKey][DISCOVERIES].get(key)
        if transport is None or protocol is None:
            _LOGGER.info("Callback to remove the discovery, account=%s  panel=%s", account, panel)
            # Delete the panel connection
            if key in hass.data[VisonicEntryKey][DISCOVERIES]:
                del hass.data[VisonicEntryKey][DISCOVERIES][key]
        elif disc:
            # The panel has been discovered
            # Update transport and protocol values (it might have disconnected and reconnected)
            disc.protocol = protocol
            disc.transport = transport
            vcd: VisonicConfigData | None = get_panel_by_id(hass, disc.panel_id)
            if vcd:
                # The panel has been configured
                if not isinstance(vcd.coordinator, VisonicDirectCoordinator):
                    _LOGGER.info("***************** Programme Error, coordinator wrong type %s ******************", type(vcd.coordinator))
                    return
                coordinator: VisonicDirectCoordinator = vcd.coordinator
                if coordinator.panel_id == disc.panel_id:    # Final check for consistency
                    # Found the existing config entry
                    if coordinator.hasStarted():
                        # TODO: Calling this will no longer work
                        coordinator.update_t_p(transport, protocol)
                    else:
                        #entry.async_create_background_task(hass, coordinator.async_server_connect(transport, protocol), name="Connect to server")
                        hass.async_create_task(coordinator.async_server_connect(transport, protocol), name="Connect to server")
                    return
            # If vcd is None then we can't do anything here, we're waiting for the discovery to kick in further down
        else:
            # The panel has not been discovered
            # Create a panel number that is unique
            panel_no = get_next_panel_id(hass)
            # New panel that we have no config for
            _LOGGER.info("Callback to set up discovery, account=%s  panel=%s, the allocated panel number is %s", account, panel, panel_no)
            vdd = VisonicDiscoveryData(panel_no, account, panel, protocol, transport)
            data = hass.data.setdefault(VisonicEntryKey, {})
            data.setdefault(DISCOVERIES, {})[key] = vdd
            # Create a new entry config_flow as a "discovery" for discovered entries
            #   The discovered panel has it's own config flow and setup
            # Calls async_step_integration_discovery in config_flow
            entry.async_create_background_task(
                hass,
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={CONF_SOURCE: SOURCE_INTEGRATION_DISCOVERY},
                    data={
                        CONF_TYPE: DeviceType.TCP_DISCOVERED,
                        CONF_PANEL_NUMBER: panel_no,
                        "account": account,
                        "panel": panel,
                        "server_key": key,
                    },
                ),
                name="Setting up TCP Server"
            )

    async def manage_tcp_server_stop_start(entry: ConfigEntry) -> bool:
        rtd: VisonicServerData = entry.runtime_data
        async with rtd.lock:
            if rtd.server:
                await rtd.server.async_stop()
            rtd.server = None
            host = entry.data.get(CONF_SERVER_HOST, "0.0.0.0")
            port = int(entry.data.get(CONF_SERVER_PORT, 5001))
            server = TCPServerConnection(hass=hass, entry=entry, connection_made_callback=panel_update_callback)
            success = await server.async_start(hass=hass, host=host, port=port)
            if success:
                rtd.server = server
            return success

    async def config_updated(hass: HomeAssistant, entry: ConfigEntry):
        _LOGGER.info("***************** config updated ******************")
        await manage_tcp_server_stop_start(entry) # use the config entry

    if server_id is None:
        return False
    vsd = VisonicServerData(None, server_id, asyncio.Lock())
    entry.runtime_data = vsd
    success = await manage_tcp_server_stop_start(entry)
    if success:
        data: VisonicDomainData = hass.data[VisonicEntryKey]
        data.setdefault(SERVERS, {})[entry.entry_id] = vsd
        entry.async_on_unload(
            entry.add_update_listener(config_updated)
        )
    return success

async def async_setup_discovered(hass: HomeAssistant, entry: ConfigEntry, panel_id: int) -> bool:
    """Setup a discovered visonic panel, probably sourced from a TCP Server creation."""
    # Panels that connect to port 5001 looking for a cloud server are redirected to a "discovered" connection by the TCP Server
    # create coordinator and connect to the panel
    # Hence, the TCP Server and Discovered devices are tied together
    try:
        # Create the coordinator for this panel
        event_logger = logEvents(hass, entry, _LOGGER, panel_id)
        coordinator = VisonicDirectCoordinator(hass, entry, panel_id, event_logger)
        # Set the runtime data defaults
        vcd = VisonicConfigData(coordinator, panel_id, None, {})
        entry.runtime_data = vcd
        data: VisonicDomainData = hass.data[VisonicEntryKey]
        data.setdefault(PANELS, {})[entry.entry_id] = vcd
        # Refresh the data for the first pass
        await coordinator.async_config_entry_first_refresh()

        account = entry.options.get("account", entry.data.get("account"))
        panel = entry.options.get("panel", entry.data.get("panel"))
        key = create_key(account, panel)
        vdd: VisonicDiscoveryData = hass.data.setdefault(VisonicEntryKey, {}).setdefault(DISCOVERIES, {}).get(key)

        if vdd is not None:
            _LOGGER.info("***************** got transport and protocol from config ******************")
            return await coordinator.async_server_connect(vdd.transport, vdd.protocol)
        # The panel has not connected to the tcp server yet to match it up.
        #    Create the discovery data and record it without transport and protocol set.
        vdd = VisonicDiscoveryData(panel_id, account, panel, None, None)
        data = hass.data.setdefault(VisonicEntryKey, {})
        data.setdefault(DISCOVERIES, {})[key] = vdd
        await coordinator.async_server_connect(None, None)

        # Fall through to the return True
    # Catch any exception and report it as a config error, connection failure
    except (TimeoutError, ConnectionError, OSError) as error:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
            translation_placeholders={"panel_id": panel_id},
        ) from error
    return True

async def async_setup_client(hass: HomeAssistant, entry: ConfigEntry, device_type: DeviceType, panel_id: int) -> bool:
    """Setup a client that either:.

    1. Connects directly to a visonic panel using ethernet/wifi or rs232/serial/USB hardware.
    2. Connects to the visonic cloud server
    3. Connects to the API in Home Assistant for an ESPHome device with a serial_proxy
    """
    try:
        # Create the coordinator for this panel
        event_logger = logEvents(hass, entry, _LOGGER, panel_id)
        if device_type == DeviceType.CLOUD:
            # create coordinator to the cloud server
            coordinator = VisonicCloudCoordinator(hass, entry, panel_id, event_logger)
        else:
            # create coordinator to the panel
            coordinator = VisonicDirectCoordinator(hass, entry, panel_id, event_logger)
        vcd = VisonicConfigData(coordinator, panel_id, None, {})
        entry.runtime_data = vcd
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # Set the runtime data defaults
        data: VisonicDomainData = hass.data[VisonicEntryKey]
        data.setdefault(PANELS, {})[entry.entry_id] = vcd
        if await coordinator.async_panel_connect():
            return True
    # Catch any exception and report it as a config error, connection failure
    except (VisonicException, TimeoutError, ConnectionError, OSError) as error:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=TRANSLATE_EXCEPTION_INITIAL_CONNECTION_FAILURE,
            translation_placeholders={"panel_id": panel_id},
        ) from error
    return False
#    except Exception as ex:
#        _LOGGER.info(f"**************** exception {ex}  *************")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up visonic from a config entry. This is the main entry point."""

    # This function is called with the flow data to create a hub connection to the alarm panel
    device_type: str = entry.data.get(CONF_TYPE)
    if device_type is None:
        return False
    device_type_enum = DeviceType(device_type)

    # Check for the id being unique
    unique_id = -1
    match device_type_enum:
        case DeviceType.TCP_SERVER:
            unique_id = await check_server_is_unique(hass, entry)
        case DeviceType.TCP_DISCOVERED:
            unique_id = await check_panel_is_unique(hass, entry)
        case DeviceType.ETHERNET | DeviceType.SERIAL | DeviceType.CLOUD:
            unique_id = await check_panel_is_unique(hass, entry)

    _LOGGER.info("***************** creating connection %d to a %s device ******************", unique_id, device_type_enum)
    _LOGGER.debug("Entry id=%s",entry.entry_id)
    # _LOGGER.debug(f" Entry data={entry.data}   options={entry.options}")

    # Test the connection to the device, if it fails then ask the config entry to try again "later"
    connection_tester = ConnectionTest()
    error = await connection_tester.test_connection(device_type=device_type_enum, user_input=entry.data)
    if error is not None:
        # Connection error
        _LOGGER.info("***************** creating connection %d to a %s device, test connection failed so trying again later ******************", unique_id, device_type_enum)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key=error,
            translation_placeholders={"panel_id": unique_id})

    # Connection test success so create the necessary servers and clients
    match device_type_enum:
        case DeviceType.TCP_SERVER:
            return await async_setup_server(hass, entry, unique_id)
        case DeviceType.TCP_DISCOVERED:
            return await async_setup_discovered(hass, entry, unique_id)
        case DeviceType.ETHERNET | DeviceType.SERIAL | DeviceType.CLOUD:
            success = await async_setup_client(hass, entry, device_type_enum, unique_id)
            _LOGGER.info("***************** creating connection %d to a %s device, success=%s ******************", unique_id, device_type_enum, success)
            if not success:
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key=error,
                    translation_placeholders={"panel_id": unique_id})
            return True
    _LOGGER.info("***************** creating connection %d to a %s device, FAILED ******************", unique_id, device_type_enum)
    return False

async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:  # noqa: C901
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
        version = 2

        _LOGGER.debug(" CONF_FS from %s", options[CONF_FS])
        if CONF_FS in options and isinstance(options[CONF_FS], bool):
            _LOGGER.debug(" CONF_FS from %s", options[CONF_FS])
            if options[CONF_FS]:
                _LOGGER.info("  Force standard set, using %s", EmulationMode.STANDARD)
                options[CONF_EMULATION_MODE] = EmulationMode.STANDARD
            else:
                _LOGGER.info("  Force standard not set, using %s", EmulationMode.POWERLINK)
                options[CONF_EMULATION_MODE] = EmulationMode.POWERLINK
            _LOGGER.info(" Emulation mode set to %s", options[CONF_EMULATION_MODE])
        changed = True

    if version == 2:
        version = 3

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
        changed = True

    if version in [3, 4, 5]:
        version = 6
        if CONF_PANEL_NUMBER not in options and CONF_PANEL_NUMBER not in data:
            # We have to assume that multiple panels will be updated at the same time, otherwise it gets complicated
            # Create a panel number that is unique
            data[CONF_PANEL_NUMBER] = get_next_panel_id(hass)
        if CONF_ESPHOME_ENTITY_SELECT not in options and CONF_ESPHOME_ENTITY_SELECT not in data:
            data[CONF_ESPHOME_ENTITY_SELECT] = ""

        # Split the data and options correctly:
        #     data is defined by the user on first creation and then only by reconfigure
        #     options can be edited easily and are pushed in to the integration without reload

        # Create a new data and options
        data_out : dict[str, Any] = {}
        options_out : dict[str, Any] = {}

        # These contain all data and options settings
        data_items = [FORM_DEVICE, FORM_ETHERNET, FORM_SERIAL, FORM_CLOUD, FORM_TCP_SERVER, FORM_TCP_DISCOVERED, FORM_POWERLINK]
        option_items = [FORM_PARAM10, FORM_PARAM11, FORM_PARAM12, FORM_PARAM13, FORM_PARAM14]

        # Build a set with all the "data" keys. Using a set removes duplication.
        key_data_list: set[str] = set()
        for d in data_items:
            key_data_list.update(FormItems[d])

        # Build a set with all the "option" keys. Using a set removes duplication.
        key_option_list: set[str] = set()
        for o in option_items:
            key_option_list.update(FormItems[o])

        # Set all data and option values to their defaults
        config_items = build_config_items() # get a temporary list of all items to get their default
        missing = (key_data_list | key_option_list) - config_items.keys()
        if missing:
            _LOGGER.warning("Missing config items: %s", missing)

        for key in key_data_list:
            data_out[key] = config_items[key].default
        for key in key_option_list:
            options_out[key] = config_items[key].default

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
                _LOGGER.warning("User options setting %s not in either config list", key)

        # New CONF names have been added but this is the only change of CONF name that has been made
        data_out[CONF_EXCLUDE_SWITCH] = data.get(CONF_EXCLUDE_X10, "")   # changed to CONF_EXCLUDE_SWITCH
        # Baud has been removed from the user config, but we need to copy the old value across
        data_out[CONF_DEVICE_BAUD] = data.get(CONF_DEVICE_BAUD, DEFAULT_DEVICE_BAUD)

        data = data_out
        options = options_out
        changed = True

    if changed:
        hass.config_entries.async_update_entry(entry, data=data, options=options, version=version)
        _LOGGER.info("Migration to version %s successful", entry.version)
    else:
        _LOGGER.info("Migration. Nothing changed, version is currently %s", version)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload visonic entry."""
    # This function is called to terminate a hub connection to the alarm panel

    #_LOGGER.info(
    #    "UNLOAD ENTRY CALLED\n%s",
    #    "".join(traceback.format_stack())
    #)

    _LOGGER.info("**************** terminating connection ****************")
    unload_ok = True
    p = ""

    data: VisonicDomainData = hass.data[VisonicEntryKey]
    device_type: str = entry.data.get(CONF_TYPE)
    if device_type is None:
        return False
    device_type_enum = DeviceType(device_type)
    _LOGGER.info("***************** async_unload_entry %s ******************", device_type_enum)
    if device_type_enum == DeviceType.TCP_SERVER:
        vsd : VisonicServerData | None = data[SERVERS].get(entry.entry_id)
        if vsd:
            p = str(vsd.server_id)
            async with vsd.lock:
                svr: TCPServerConnection = vsd.server
                if svr:
                    await svr.async_stop()
                unload_ok = True # await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
                entry.runtime_data = None
        _tmp = data[SERVERS].pop(entry.entry_id, None)

    elif device_type_enum in (DeviceType.TCP_DISCOVERED, DeviceType.ETHERNET, DeviceType.SERIAL, DeviceType.CLOUD):
        vcd : VisonicConfigData | None = data[PANELS].get(entry.entry_id)
        if vcd:
            # stop all activity in the hub
            p = str(vcd.panel_id)
            unload_ok = await vcd.coordinator.async_panel_stop()
            _LOGGER.debug("........... Killing Dispatchers")
            vcd.coordinator.platform_manager.terminate_all_dispatchers(entry)
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            _LOGGER.debug("***** terminate connection fail, no hub coordinator ****")
        _tmp = data[PANELS].pop(entry.entry_id, None)

    if unload_ok:
        _LOGGER.debug("************** Connection %s terminate success ***************", p)
    else:
        _LOGGER.debug("******* Connection %s terminate success (but with problems) ******", p)
    return unload_ok
