"""Create a Client connection to a Visonic PowerMax or PowerMaster Alarm System."""

# This child/parent class build up incorporates the interaction/interface to the low level pyvisonic library

import asyncio
from collections.abc import Callable
import contextlib
from datetime import datetime
import logging
import traceback

from requests import ConnectTimeout, HTTPError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE,
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    EVENT_CORE_CONFIG_UPDATE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.util import Any

from ..const import (  # noqa: TID252  # noqa: TID252
    CONF_DOWNLOAD_CODE,
    CONF_RETRY_CONNECTION_COUNT,
    CONF_RETRY_CONNECTION_DELAY,
    CONF_USER_CODE_SLOT,
    DEFAULT_DEVICE_HOST,
    DEFAULT_DEVICE_SERIAL,
    PE_NAME,
    PLATFORMS,
)
from ..log_events import logEvents  # noqa: TID252  # noqa: TID252
from ..platform_manager import PlatformManager  # noqa: TID252
from ..server import ServerProtocol  # noqa: TID252
from ..utils import getAlarmPanelUniqueIdent  # noqa: TID252
from ..visonic_entity_types import (  # noqa: TID252  # noqa: TID252
    AlarmPanelData,
    DeviceState,
    SwitchState,
)
from ..visonic_types import (  # noqa: TID252  # noqa: TID252
    AvailableNotifications,
    Connection_Status,
    DeviceType,
    PanelCondition,
)
from .client_maintain_interface import MaintainInterface
from .cvp import (
    CVP_Direct,
    CVP_Status,
    async_create_serial_client,
    async_create_tcp_client,
)
from .direct_types import AlarmSensorType, SensorStateExt
from .pyvisonic.py_abstract_classes import (
    AlGenericDevice,
    AlPanelInterface,
    AlPanelMode,
)
from .pyvisonic.py_enum import (
    AlCommandStatus,
    AlCondition,
    AlSensorCondition,
    AlTerminationType,
)
from .pyvisonic.py_exception import PyVisonicException
from .pyvisonic.py_sensor import AlSensorDeviceHelper
from .pyvisonic.py_visonic import VisonicProtocol
from .pyvisonic.py_visonic_devices import AlSwitchDeviceHelper

_LOGGER = logging.getLogger(__name__)

class ManageConnection(MaintainInterface):
    """Manage the connection to panel through the library."""

    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================
    # ======== Functions to make the connection to the panel and manage restarts etc ========================
    # =======================================================================================================
    # =======================================================================================================
    # =======================================================================================================

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        diagnostics: logEvents | None,
        force_standard_mode,
        disable_all_panel_commands,
        platform_manager : PlatformManager,
        panelident: int,
        state_changed_callback: Callable[..., None],
    ) -> None:
        """Initialize."""
        super().__init__(hass, entry, diagnostics, platform_manager, panelident, state_changed_callback)
        # These are variables used throughout this class and all child classes
        self._listeners_registered = False
        self.force_standard_mode = force_standard_mode
        self.disable_all_panel_commands = disable_all_panel_commands
        self.panel_disconnection_counter = 0
        self.download_code = str(entry.data.get(CONF_DOWNLOAD_CODE, ""))
        self.user_code_slot = int(entry.data.get(CONF_USER_CODE_SLOT, 1))
        self._management_task: asyncio.Task[None] = None
        self._initialise()
        # add update listener to unload.  The update listener is used when the user edits an existing configuration.
        self.language_decoder.update()
        self._management_task: asyncio.Task[None] = self.entry.async_create_background_task(self.hass, self.connection_manager(), "Connection Manager")

    def _initialise(self):
        """Initialise local variables to this class."""
        super()._initialise()
        self._reevaluate_connection = asyncio.Event()
        self._requested_state: Connection_Status = Connection_Status.READY_TO_START
        self._client_vis_protocol : CVP_Direct | None = None
        self._visonic_protocol: AlPanelInterface | None = None
        self._max_connection_attempts = int(
            self.entry.options.get(CONF_RETRY_CONNECTION_COUNT, 1)
        )

    def _register_event_listeners(self):
        """Register listeners."""
        # Listener to handle fired config update events
        def handle_core_config_updated(_event: object):
            # If the user has changed the Core HA configuration, they may have changed their language selection
            self.logger.logstate_debug(
                "[Visonic Setup] Core configuration has been Updated"
            )
            # hass = async_get_hass()
            self.language_decoder.update()
            if self._visonic_protocol is not None:
                self._visonic_protocol.set_log_events(
                    self.language_decoder.getLogEventList()
                )

        if self._listeners_registered:
            return
        self._listeners_registered = True
        # Listen for when EVENT_CORE_CONFIG_UPDATE is fired
        self.entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, handle_core_config_updated)
        )

    async def _send_baud_change_to_panel(self, baud) -> bool:
        """Send the commend to the panel to set the baud rate."""
        if self._visonic_protocol is not None:
            retval = await self._visonic_protocol.set_panel_baud(baud)
            if retval == AlCommandStatus.SUCCESS:
                self.logger.logstate_debug("    Baud set in panel")
            else:
                self.logger.logstate_debug("    Baud change failed in panel: %s", retval.name)
            return retval == AlCommandStatus.SUCCESS
        return False

    def _create_protocol(self):
        # Terminate any existing connection
        if self._visonic_protocol is not None:
            self._visonic_protocol.shutdown()
        # Create new protocol
        self.logger.logstate_debug("........... Creating Visonic Protocol")
        self._visonic_protocol = VisonicProtocol(
            force_standard_mode=self.force_standard_mode,
            disable_all_commands=self.disable_all_panel_commands,
            download_code=self.download_code,
            user_code_slot=self.user_code_slot,
            loop=self.hass.loop,
        )
        self._visonic_protocol.set_log_events(self.language_decoder.getLogEventList())
        self._visonic_protocol.on_panel_change(self.on_panel_change_handler)
        self._visonic_protocol.on_panel_event_log(self.on_panel_event_log_handler)
        self._visonic_protocol.on_problem(self.on_panel_problem)
        self._visonic_protocol.on_new_sensor(self.on_new_sensor)
        self._visonic_protocol.on_new_switch(self.on_new_switch)
        self._visonic_protocol.on_new_device(self.on_new_device)

    async def _async_stop_protocol(self):
        """Stop the protool connection."""
        # Shutdown the protocol handler and any tasks it uses
        if self._visonic_protocol is not None:
            self.logger.logstate_debug("........... Shutting down Visonic Protocol")
            self._visonic_protocol.shutdown()
            self._visonic_protocol = None

    async def _async_create_transport(self) -> bool:
        # Make a connection, the bool return is False if an Exception occurred
        try:
            device_type = self.entry.data.get(CONF_TYPE, "")
            self.logger.logstate_debug("Creating Visonic Transport, Comms Device Type is %s", device_type)
            match(device_type):
                case DeviceType.ETHERNET | DeviceType.TCP_DISCOVERED:
                    host = self.entry.data.get(CONF_HOST, DEFAULT_DEVICE_HOST)
                    port = int(self.entry.data.get(CONF_PORT, 0))
                    await async_create_tcp_client(
                        self.logger,
                        self.hass,
                        connection_status_callback=self.connection_status_callback,
                        vp=self._visonic_protocol,
                        address=host,
                        port=port,
                    )
                    return True
                case DeviceType.SERIAL:
                    path = self.entry.data.get(CONF_DEVICE, DEFAULT_DEVICE_SERIAL)
                    await async_create_serial_client(
                        self.logger,
                        self.hass,
                        connection_status_callback=self.connection_status_callback,
                        vp=self._visonic_protocol,
                        path=path,
                        baud=self._serial_baud_rate,
                    )
                    return True
                case _:
                    self.logger.logstate_error("[_async_create_transport] THIS FUNCTION SHOULD NOT BE CALLED FOR THIS DEVICE TYPE", device_type)
        except (ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError) as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logger.logstate_warning("........... _async_create_transport, caused connection exception %s", ex)
        except (OSError, TimeoutError) as ex:
            # Do not cause a full Home Assistant Exception, keep it local here
            self.logger.logstate_warning("........... _async_create_transport, caused exception %s", ex)
        return False

    async def _async_stop_transport(self):
        """Stop the transport connection."""
        # stop the serial/ethernet comms with the panel
        if self._client_vis_protocol is not None:
            self.logger.logstate_debug("........... Shutting down Visonic Transport")
            self._client_vis_protocol.close()
            await asyncio.sleep(0.0)
        self._client_vis_protocol = None
        await self._stop_panel_change_handler()

    def _kick_off_next_step(self, command: Connection_Status):
        """Create a step for the manager."""
        self._requested_state = command
        self._reevaluate_connection.set()

    def connection_status_callback(self, state: CVP_Status, cvp: CVP_Direct | None = None):
        """Callback from protocol CVP to handle connections and disconnections."""
        # cvp is only valid then CONNECTED, anything else invalidates any existing connection.
        self._client_vis_protocol = cvp if state == CVP_Status.CONNECTED and cvp is not None else None
        self._kick_off_next_step(Connection_Status(state))

    def connect(self) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""
        self._kick_off_next_step(Connection_Status.INITIAL_CREATE_PROTOCOL)
        return True

    async def _async_stop(self):
        """Stop the connection."""
        # stop the serial/ethernet comms with the panel
        await self._async_stop_transport()
        await self._async_stop_protocol()

    #async def async_reconnect_comms(self, force: bool = False):
    #    """Reconnect comms."""
    #    # ---- Reconnection not allowed by the user by setting config setting self._max_connection_attempts to 0 ----
    #    if not force and self._max_connection_attempts <= 0:
    #        self.logger.logstate_debug("Reconnect disabled by user for panel %s (0 attempts), stopping", self.panel_ident)
    #        return
    #    # ---- Try simple reconnect of comms ----
    #    # Leave the visonic protocol in place, reconnect the comms
    #    #  The protocol will pick up if the comms is established
    #    self._kick_off_next_step(Connection_Status.INITIAL_CREATE_TRANSPORT)

    def update_baud(self):
        """Set the baud rate."""
        self._kick_off_next_step(Connection_Status.BAUD_CHANGE)

    async def async_restart(self, force: bool = False):
        """Full Restart, stop the connection and start it again."""
        # ---- Reconnection not allowed by the user by setting config setting self._max_connection_attempts to 0 ----
        self._max_connection_attempts = int(
            self.entry.options.get(CONF_RETRY_CONNECTION_COUNT, 1)
        )
        if not force and self._max_connection_attempts <= 0:
            self.logger.logstate_debug("Restart disabled by user for panel %s (0 attempts), stopping", self.panel_ident)
            return
        # Stop and start again, recreate visonic protocol
        self._kick_off_next_step(Connection_Status.RESTART)

    async def async_panel_stop(self):
        """Redirector to stop and unload the hub."""
        if self._management_task:
            self._management_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._management_task
            self._management_task = None
        await self._async_stop()

    async def connection_manager(self):  # noqa: C901
        """Connection manager task."""

        # Connection manager is set up as a HA background task to start and maintain the transport (and protocol)

        self._wait_task = None

        def create_timer():
            async def timer_delay(now: datetime | None = None):
                self._kick_off_next_step(Connection_Status.RETRY_CREATE_TRANSPORT)

            delay_between_attempts = int(self.entry.options.get(CONF_RETRY_CONNECTION_DELAY, 60.0))  # seconds
            self.logger.logstate_debug(
                f"........... connection attempt delay {delay_between_attempts} seconds"
            )
            self._wait_task = async_call_later(
                self.hass,
                delay_between_attempts,
                timer_delay,
            )

        attempt_counter = 0
        force = False
        device_type = self.entry.data.get(CONF_TYPE, "")
        current_state: Connection_Status = Connection_Status.READY_TO_START

        # Note that Connection_Status.STOP can be requested at any time from any state
        allow_allways: list[Connection_Status] = [Connection_Status.STOP, Connection_Status.RESTART, Connection_Status.EXCEPTION]
        valid_transitions : dict[Connection_Status, set[Connection_Status]] = {
            Connection_Status.READY_TO_START :             {Connection_Status.INITIAL_CREATE_PROTOCOL},
            Connection_Status.INITIAL_CREATE_TRANSPORT :   {Connection_Status.CONNECTED,
                                                            Connection_Status.CONNECTION_PENDING,
                                                            Connection_Status.NO_CONNECTION_MADE,
                                                            Connection_Status.RETRY_CREATE_TRANSPORT },
            Connection_Status.INITIAL_CREATE_PROTOCOL :    {Connection_Status.INITIAL_CREATE_TRANSPORT },
            Connection_Status.RESTART :                    {Connection_Status.INITIAL_CREATE_PROTOCOL },
            Connection_Status.CONNECTION_PENDING :         {Connection_Status.INITIAL_CREATE_TRANSPORT, # user commanded async_reconnect_comms
                                                            Connection_Status.DISCONNECTED,
                                                            Connection_Status.CONNECTED,
                                                            Connection_Status.CLOSE_CONNECTION},
            Connection_Status.CONNECTED :                  {Connection_Status.INITIAL_CREATE_TRANSPORT, # user commanded async_reconnect_comms
                                                            Connection_Status.DISCONNECTED,
                                                            Connection_Status.CONNECTION_PENDING,
                                                            Connection_Status.BAUD_CHANGE,
                                                            Connection_Status.BAUD_CHANGE_RESET_PROTOCOL},
            Connection_Status.DISCONNECTED :               {Connection_Status.INITIAL_CREATE_TRANSPORT, # user commanded async_reconnect_comms
                                                            Connection_Status.CONNECTED,
                                                            Connection_Status.CONNECTION_PENDING,
                                                            Connection_Status.INITIAL_CREATE_PROTOCOL},
            Connection_Status.NO_CONNECTION_MADE :         {Connection_Status.CONNECTION_PENDING,
                                                            Connection_Status.CONNECTED,
                                                            Connection_Status.BAUD_CHANGE,
                                                            Connection_Status.BAUD_CHANGE_RESET_PROTOCOL,
                                                            Connection_Status.RETRY_CREATE_TRANSPORT},
            Connection_Status.NO_OPERATION :                set(Connection_Status),  # Allow all transitions
            Connection_Status.EXCEPTION :                  {Connection_Status.INITIAL_CREATE_PROTOCOL },
            Connection_Status.BAUD_CHANGE_RESET_PROTOCOL : {Connection_Status.NO_OPERATION,
                                                            Connection_Status.INITIAL_CREATE_PROTOCOL},
            Connection_Status.BAUD_CHANGE :                {Connection_Status.NO_OPERATION,
                                                            Connection_Status.INITIAL_CREATE_TRANSPORT},
            Connection_Status.CLOSE_CONNECTION :           {Connection_Status.READY_TO_START},
            Connection_Status.RETRY_CREATE_TRANSPORT :    {Connection_Status.READY_TO_START,
                                                            Connection_Status.CONNECTED,
                                                            Connection_Status.CONNECTION_PENDING,
                                                            Connection_Status.NO_CONNECTION_MADE,
                                                            Connection_Status.DISCONNECTED,
                                                            Connection_Status.INITIAL_CREATE_TRANSPORT},
            Connection_Status.STOP :                       set(),  # Stop means stop, do nothing else
        }

        while True:
            try:
                await self._reevaluate_connection.wait()
                self._reevaluate_connection.clear()
                if self._requested_state == current_state:
                    # Do not allow the same state condition to be executed sequentially
                    continue
                self.logger.logstate_info(f"Requested state {self._requested_state.name}")
                dest_set: set[Connection_Status] = valid_transitions.get(current_state, {Connection_Status.READY_TO_START})
                if self._requested_state not in allow_allways and (current_state in (Connection_Status.STOP, self._requested_state) or self._requested_state not in dest_set):
                    self.logger.logstate_info(f"   Disallowed, current state is {current_state.name}")
                    continue

                current_state = self._requested_state
                self.logger.logstate_info(f"Doing {current_state.name}")
                if self._wait_task is not None:
                    self._wait_task()
                    self._wait_task = None

                match (current_state):
                    case Connection_Status.READY_TO_START | Connection_Status.NO_OPERATION:
                        pass

                    case Connection_Status.RESTART | Connection_Status.EXCEPTION:
                        # 2 possibilities
                        #    - RESTART: Restart has been commanded
                        #    - EXCEPTION: The comms connection has had an exception and is therefore in an unknown state
                        # Stop protocol and transport
                        await self._async_stop()
                        await asyncio.sleep(1.0)
                        # Goto INITIAL_CREATE_PROTOCOL
                        self._kick_off_next_step(Connection_Status.INITIAL_CREATE_PROTOCOL)

                    case Connection_Status.DISCONNECTED:
                        # Assume that the transport has stopped and the protocol is OK
                        # Stop transport
                        await self._async_stop_transport()
                        await asyncio.sleep(1.0)
                        # goto INITIAL_CREATE_TRANSPORT
                        self._kick_off_next_step(Connection_Status.INITIAL_CREATE_TRANSPORT)

                    case Connection_Status.NO_CONNECTION_MADE:
                        # The comms connection has been attempted but has failed, there are no lower level retries
                        #    This only happens on the first attempt, after a CONNECTED, we get DISCONNECTED instead
                        # Stop transport
                        await self._async_stop_transport()
                        # Pause the protocol to stop it trying (as there's no transport connection)
                        self._visonic_protocol.pause()
                        # Kick off the timer to retry transport creation in "delay" time
                        create_timer()

                    case Connection_Status.CONNECTION_PENDING:
                        # Pause the protocol to stop it trying (as there's no transport connection)
                        self._visonic_protocol.pause()

                    case Connection_Status.CONNECTED:
                        # Record that we have started the system, the transport has been connected
                        self._system_started = True
                        # Connection to the panel has been initially successful
                        self.logger.logstate_debug("........... connection made")
                        self.platform_manager.create_ha_fire_event(
                            event_id=PanelCondition.CONNECTION,
                            datadictionary={
                                "state": "connected",
                                "attempt": attempt_counter + 1,
                            },
                        )
                        # Register the update listeners
                        self._register_event_listeners()
                        # Resume the protocol if it was paused (this has no action if not paused)
                        self._visonic_protocol.resume()

                    case Connection_Status.INITIAL_CREATE_PROTOCOL:
                        # Re-create the protocol as the current protocol has STOPPED
                        self._create_protocol()
                        # Pause it straight away, waiting for the connection to resume it
                        self._visonic_protocol.pause()
                        self._kick_off_next_step(Connection_Status.INITIAL_CREATE_TRANSPORT)

                    case Connection_Status.INITIAL_CREATE_TRANSPORT:
                        # Reset the attempt counter, always make the initial connection
                        attempt_counter = 0
                        # Pause the protocol to stop it trying (as there's no transport connection)
                        self._visonic_protocol.pause()
                        self.logger.logstate_debug("Client connecting comms.....")
                        self._client_vis_protocol = None
                        # If an exception occurs inside _async_create_transport then ignore it for the first connection
                        await self._async_create_transport()
                        # If nothing happens then at least trigger RETRY_CREATE_TRANSPORT after the user timeout
                        #     If the low level responds then the timer will be stopped
                        create_timer()

                    case Connection_Status.RETRY_CREATE_TRANSPORT:
                        # Timer instigates this step, transport failed so set up for next loop around
                        self.platform_manager.create_ha_fire_event(
                            event_id=PanelCondition.CONNECTION,
                            datadictionary={
                                "state": "failedattempt",
                                "attempt": attempt_counter + 1,
                            },
                        )
                        attempt_counter += 1
                        self._max_connection_attempts = int(
                            self.entry.options.get(CONF_RETRY_CONNECTION_COUNT, 1)
                        )
                        if attempt_counter >= self._max_connection_attempts:
                            self._kick_off_next_step(Connection_Status.STOP)
                        elif await self._async_create_transport():
                            self.logger.logstate_debug(
                                f"........... connection attempt {attempt_counter + 1} of {1 if force else self._max_connection_attempts}{'     (with no future reconnections)' if force else ''}"
                            )
                            create_timer()
                        else:
                            # Exception so go back to the beginning, and stop and start protocol and transport
                            self._kick_off_next_step(Connection_Status.READY_TO_START)

                    case Connection_Status.BAUD_CHANGE | Connection_Status.BAUD_CHANGE_RESET_PROTOCOL:
                        # cycle the baud for the next reconnection.....

                        self.logger.logstate_debug("Setting Baud %s", self._serial_baud_rate)
                        if self._baud_index >= len(self._connection_baud_list):
                            self.logger.logstate_debug("    Reset Baud Selection List")
                            self.reset_baud_list()

                        self._last_baud_rate_change_success = False

                        if device_type == DeviceType.ETHERNET:
                            # For ethernet we do not terminate the connection, we
                            #   - change the baud in the panel itself
                            #   - use the select entity to change the baud of the serial connection in the ESPHome device
                            if self.is_select_entity_valid(str(self._serial_baud_rate)):
                                self._last_baud_rate_change_success = await self._send_baud_change_to_panel(self._serial_baud_rate)
                                if self._last_baud_rate_change_success:
                                    self.set_select_entity(str(self._serial_baud_rate))
                            else:
                                self.logger.logstate_debug("    NOT changed Baud %s, entity invalid", self._serial_baud_rate)
                            self._kick_off_next_step(Connection_Status.NO_OPERATION)

                        elif device_type == DeviceType.SERIAL:
                            # For serial we terminate the connection, we
                            #   - change the baud in the panel itself
                            #   - terinate the connection
                            #   - create the connection with the new baud
                            #       Note: We could modify the baud of an ESPHome serial connection but we don't know if it is ESPHome
                            self._last_baud_rate_change_success = await self._send_baud_change_to_panel(self._serial_baud_rate)
                            # Recreate the protocol/transport with the old or new baud
                            await self._async_stop_transport()
                            if current_state == Connection_Status.BAUD_CHANGE_RESET_PROTOCOL:
                                self._kick_off_next_step(Connection_Status.INITIAL_CREATE_PROTOCOL)
                            else:
                                self._kick_off_next_step(Connection_Status.INITIAL_CREATE_TRANSPORT)
                        else:
                            self.logger.logstate_debug("    Incorrect device type %s, baud not updated!", device_type)
                            self._kick_off_next_step(Connection_Status.NO_OPERATION)

                    case Connection_Status.STOP:
                        #if self.hasStarted():
                        # If there's an ongoing restart then terminate it
                        await self._async_stop()
                        #self.logger.create_ha_notification(
                        #    AvailableNotifications.CONNECTION,
                        #    f"Failed to connect into Visonic Alarm Panel {self.panel_ident}. Check Your Network and the Configuration Settings.",
                        #)

                    case Connection_Status.CLOSE_CONNECTION:
                        await self._async_stop()
                        self._kick_off_next_step(Connection_Status.READY_TO_START)

            # Do not cause a full Home Assistant Exception, keep it local here
            except (ConnectTimeout, HTTPError) as ex:
                self.logger.create_ha_notification(
                    AvailableNotifications.CONNECTION,
                    f"Visonic Panel Connection Error: {ex}<br />You will need to restart hass after fixing.",
                )
            except asyncio.CancelledError:
                # Re-raise so Home Assistant can properly shut down the task
                self.logger.logstate_debug("........... connection_manager, connection cancelled by system")
                raise
            except OSError as err:
                # Catch network/system issues that might occur during sleep
                self.logger.logstate_debug(f"........... connection_manager, delay interrupted: {err}")
            except TimeoutError as ex:
                self.logger.logstate_warning(f"........... connection_manager, network error: {ex}")
            except (AttributeError, TypeError, ValueError) as ex:
                self.logger.logstate_error(f"........... connection_manager, coding error: {ex}")
            except PyVisonicException as ex:
                self.logger.logstate_debug(f"PyVisonic Library Exception: {ex}")
            except (ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError) as ex:
                self.logger.logstate_warning(".. connection_manager, caused connection exception %s", ex)
            except Exception as ex:  # noqa: BLE001
                tb_str = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
                self.logger.logstate_error(f"General Exception: \n\n{tb_str}")

    # Manage the devices: Sensors, Switches and Devices

    def on_new_sensor(self, create: bool, py_sensor: AlSensorDeviceHelper):
        """Redirect onsensor to add additional parameters. If create is False then delete."""
        if py_sensor is None:
            self.logger.logstate_warning("Sensor callback but Sensor Device is None")
            return
        # Use SensorStateExt instead of SensorState as it adds sensor_type.
        sensor = SensorStateExt.from_dict(self._visonic_protocol.is_power_master(), py_sensor.as_dict())
        if sensor is None or sensor.sensor_type.type == AlarmSensorType.IGNORED:
            return
        if create and not sensor.enabled:
            return
        if create:
            # create
            if self.platform_manager.sensor_update_or_create(sensor):
                py_sensor.add_callback(self.onSensorChange)
        else:
            # delete
            py_sensor.clear_callbacks() # Prevent all callback handlers
            self.platform_manager.delete_sensor(sensor.id)
            if self._visonic_protocol.get_panel_mode() not in [
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ]:
                self.platform_manager.rationalise_ha_devices(True)
        if self.state_changed_callback:
            self.state_changed_callback()

    def onSensorChange(self, py_sensor: AlSensorDeviceHelper, c: AlSensorCondition):
        """Sensor change callback."""
        if py_sensor is None:
            self.logger.logstate_warning("Sensor callback but Sensor Device is None")
            return
        # Use SensorStateExt instead of SensorState as it adds sensor_type.
        sensor = SensorStateExt.from_dict(self._visonic_protocol.is_power_master(), py_sensor.as_dict())
        self.platform_manager.sensor_update_or_create(sensor)
        self.logger.logstate_debug(f"onSensorChange {c.name} {sensor.id}")
        if self.state_changed_callback:
            self.state_changed_callback()


    def on_new_switch(self, create: bool, py_switch: AlSwitchDeviceHelper):
        """Redirect onswitch to add additional parameters."""
        if py_switch is None:
            self.logger.logstate_warning("Switch callback but Switch Device is None")
            return
        dev = SwitchState.from_dict(py_switch.as_dict())
        if create and not dev.enabled:
            return
        if create:
            if self.platform_manager.switch_update_or_create(dev):
                py_switch.add_callback(self.onSwitchChange)
        else:
            py_switch.clear_callbacks() # Prevent all callback handlers
            self.platform_manager.delete_switch(dev.id)
            if self._visonic_protocol.get_panel_mode() not in [
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ]:
                self.platform_manager.rationalise_ha_devices(True)
        if self.state_changed_callback:
            self.state_changed_callback()

    def onSwitchChange(self, py_switch: AlSwitchDeviceHelper):
        """Switch change callback."""
        # self.logger.logstate_debug(f"onSwitchChange {switch}")
        if py_switch is None:
            self.logger.logstate_warning("Sensor callback but Sensor Device is None")
            return
        switch = SwitchState.from_dict(py_switch.as_dict())
        self.platform_manager.switch_update_or_create(switch)
        self.logger.logstate_debug(f"onSwitchChange {switch.id}")
        if self.state_changed_callback:
            self.state_changed_callback()


    def on_new_device(self, create: bool, py_dev: AlGenericDevice):
        """On new device."""
        if py_dev is None:
            self.logger.logstate_warning("Device callback but Device is None")
            return
        device = DeviceState.from_dict(py_dev.as_dict())
        if create and not device.enabled:
            return
        if create:
            if self.platform_manager.device_update_or_create(device):
                py_dev.add_callback(self.onDeviceChange)
        else:
            py_dev.clear_callbacks() # Prevent all callback handlers
            self.platform_manager.delete_sensor(device.id)
            if self._visonic_protocol.get_panel_mode() not in [
                AlPanelMode.POWERLINK,
                AlPanelMode.POWERLINK_BRIDGED,
                AlPanelMode.STANDARD_PLUS,
            ]:
                self.platform_manager.rationalise_ha_devices(True)
        if self.state_changed_callback:
            self.state_changed_callback()

    def onDeviceChange(self, py_dev: AlGenericDevice):
        """Device change callback."""
        # self.logger.logstate_debug(f"onSwitchChange {switch}")
        if py_dev is None:
            self.logger.logstate_warning("Device callback, device is None")
            return
        device = DeviceState.from_dict(py_dev.as_dict())
        self.platform_manager.device_update_or_create(device)
        self.logger.logstate_debug(f"onDeviceChange {device.id}")
        if self.state_changed_callback:
            self.state_changed_callback()


    # This can be called from this module but it is also the callback handler for the connection
    def on_panel_change_handler(
        self, event_id: AlCondition | PanelCondition, data: dict[str, Any] | None
    ):
        """Generate HA Bus Event and Send Notification to Frontend."""
        try:
            event_id = PanelCondition(event_id)
        except ValueError:
            # handle unknown values safely
            return
        if event_id == PanelCondition.PANEL_UPDATE:
            if data is not None and PE_NAME in data and data[PE_NAME] >= 0:
                self.event_coordinator(data)
            else:
                self.logger.logstate_warning(
                    "[on_panel_change_handler] Cannot translate panel event log data %s",
                    data,
                )
        else:
            self.send_event(event_id, data)
        if self.state_changed_callback:
            self.state_changed_callback()

    def on_panel_event_log_handler(
        self,
        total: int,
        current: int,
        partition: set,
        dateandtime: datetime,
        zone: int,
        event: int
    ):
        """Callback handler for panel log events."""
        # Resolve event and zone strings
        # Translate the event
        zone_str: str = self.language_decoder.get_zone_entry(
            self.is_power_master(), zone
        )
        event_str: str = self.language_decoder.get_event_entry(event)
        partition_val = partition if self.get_partitions_in_use() else 0
        self.panel_event_log.process_panel_event_log(
            total, current, partition_val, dateandtime,
            zone_str, event_str
        )

    def on_panel_problem(self, termination: AlTerminationType):
        """Problem Callback for connection disruption to the panel."""
        # Visonic library has responded to a disconnection

        self._max_connection_attempts = int(
            self.entry.options.get(CONF_RETRY_CONNECTION_COUNT, 1)
        )
        self.action_panel_termination(termination)
        # push through a panel update to the HA Frontend of any changes
        self.on_panel_change_handler(event_id=PanelCondition.PUSH_CHANGE, data={})

        # This must be set so default is an invalid setting
        device_type = self.entry.data.get(CONF_TYPE, "")
        device_valid = device_type in (DeviceType.SERIAL) or (device_type == DeviceType.ETHERNET and self.is_select_entity_valid())
        if (
            device_valid
            and self._visonic_protocol is not None
            and self._baud_index < len(self._connection_baud_list)
            and termination
            in [
                AlTerminationType.NO_DATA_FROM_PANEL_NEVER_CONNECTED,
                AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED,
            ]
        ):
            # Try the sequence of baud value
            baud = self._connection_baud_list[self._baud_index]
            self._baud_index += 1  # for next time
            self._serial_baud_rate = baud
            self._kick_off_next_step(Connection_Status.BAUD_CHANGE_RESET_PROTOCOL)

            reason = "disconnected" if termination == AlTerminationType.NO_DATA_FROM_PANEL_DISCONNECTED else "never connected"
            self.logger.logstate_debug("No data from panel (%s) so try a different baud rate %s", reason, baud)

        elif self._max_connection_attempts == 0:
            # If the user says 0 restart attempts then do not restart at all
            self.logger.logstate_debug("  User config explicitly prevents any reconnection attempts, stopping the connection")
            # stop, do not restart
            self._kick_off_next_step(Connection_Status.STOP)
        else:
            # termination:
            #    CRC_ERROR = 3
            #    SAME_PACKET_ERROR = 4
            #    EXTERNAL_TERMINATION = 5
            #    NO_POWERLINK_FOR_PERIOD = 6
            self._kick_off_next_step(Connection_Status.RESTART)


    # The following 2 functions are used by the TCP Server/Discovery configuration, this is not currently used

    def update_t_p(self, transport: asyncio.Transport, protocol: ServerProtocol):
        """Update the transport and protocol. Tie everything back together with the new transport and protocol."""
        if protocol is not None:
            protocol.set_vp(self._visonic_protocol)
        #################################################################
        # TODO:
        # This isn't going to work any more so do it a different way
        #################################################################
        #if transport is not None:
        #    transport.update_transport(transport)

    async def async_server_connect(self, transport: asyncio.Transport, protocol: ServerProtocol) -> bool:
        """Connect to the alarm panel using the pyvisonic library."""

        if self.hasStarted():
            self.logger.logstate_warning(
                "Request to Start and the integraion is already running and connected"
            )
        else:
            self._visonic_protocol = None
            try:
                # self.logger.logstate_debug(f"[async_server_connect]       async_forward_entry_setups")
                self.logger.logstate_debug(
                    "[async_server_connect] Client connecting.....      async_forward_entry_setups"
                )
                await self.hass.config_entries.async_forward_entry_setups(
                    self.entry, PLATFORMS
                )
                self.logger.logstate_debug(
                    "[async_server_connect] Client connecting.....      async_forward_entry_setups done"
                )

                self._visonic_protocol = VisonicProtocol(
                    force_standard_mode=False,
                    disable_all_commands=False,
                    download_code=None,
                    user_code_slot=1,
                    loop=self.hass.loop,
                )
                self._visonic_protocol.set_log_events(
                    self.language_decoder.getLogEventList()
                )
                self.platform_manager.set_alarm_device_information(self.get_panel_model())

                await self.platform_manager.setup_visonic_entity(
                    Platform.ALARM_CONTROL_PANEL, AlarmPanelData(
                        getAlarmPanelUniqueIdent(self.panel_ident),
                        self.get_partitions_in_use()
                    )
                )

                self.platform_manager.create_ha_fire_event(
                    event_id=PanelCondition.CONNECTION,
                    datadictionary={
                        "state": "connected",
                        "attempt": 1,
                    },
                )

                if transport is not None and protocol is not None:
                    self.update_t_p(transport, protocol)

                self._visonic_protocol.on_panel_change(self.on_panel_change_handler)
                self._visonic_protocol.on_panel_event_log(self.on_panel_event_log_handler)
                self._visonic_protocol.on_problem(self.on_panel_problem)
                self._visonic_protocol.on_new_sensor(self.on_new_sensor)
                self._visonic_protocol.on_new_switch(self.on_new_switch)

                # Record that we have started the system
                self._system_started = True

            except (ConnectTimeout, HTTPError) as ex:
                self.logger.create_ha_notification(
                    AvailableNotifications.CONNECTION,
                    f"Visonic Panel Connection Error: {ex}<br />You will need to restart hass after fixing.",
                )

        return self.hasStarted()
