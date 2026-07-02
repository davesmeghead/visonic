"""Configuration flow for connecting to Visonic PowerMax and PowerMaster alarm systems.

Home Assistant config and options flow handling to test Visonic PowerMax/PowerMaster alarm panel connections.
"""
import asyncio
import logging
from typing import Any

import serial
import serialx

from homeassistant.const import CONF_EXTERNAL_URL, CONF_HOST, CONF_PATH, CONF_PORT

from .const import (
    TRANSLATE_ERROR_CONNECTION_REFUSED,
    TRANSLATE_ERROR_CONNECTION_TIMEOUT,
    TRANSLATE_ERROR_SETTINGS_MISSING,
)
from .visonic_types import DeviceType

_LOGGER = logging.getLogger(__name__)

###################################################################################
#################  A class to test the different connection types #################
###################################################################################

TESTING=False

class ConnectionTest:
    """Common serial and ethernet connection test."""

    # Make an initial connection to the device and then close it to see it we can.

    async def try_tcp_connection(self, host: str, port: int):
        """Connect to an IP host address and port to check validity."""
        # This can also be used to try the cloud connection
        if TESTING:
            return None
        if not host:
            return TRANSLATE_ERROR_SETTINGS_MISSING
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=5,
            )
            writer.close()
            await writer.wait_closed()
        except TimeoutError:
            return TRANSLATE_ERROR_CONNECTION_TIMEOUT
        except asyncio.TimeoutError:  # noqa: UP041
            return TRANSLATE_ERROR_CONNECTION_TIMEOUT
        except OSError:
            return TRANSLATE_ERROR_CONNECTION_REFUSED
        return None

    async def try_serial_port(self, device: str, baudrate=9600, timeout=1):
        """Connect to serial port to check validity."""
        if TESTING:
            return None
        try:
            asu = serialx.async_serial_for_url(
                url=device,
                baudrate=baudrate,
            )
            await asu.open()
            _ = asu.is_open
            await asu.close()
        except serialx.SerialException:
            return TRANSLATE_ERROR_CONNECTION_REFUSED
        except serial.SerialTimeoutException, TimeoutError:
            return TRANSLATE_ERROR_CONNECTION_TIMEOUT
        except ValueError:
            return TRANSLATE_ERROR_CONNECTION_TIMEOUT
        except OSError:
            return TRANSLATE_ERROR_CONNECTION_REFUSED
        finally:
            if asu is not None and asu.is_open:
                await asu.close()
        return None

    async def test_connection(self, device_type: DeviceType, user_input: dict[str, Any] ) -> str | None:
        """Test the connection."""
        error = None
        match device_type:
            case DeviceType.TCP_SERVER:
                error = None
            case DeviceType.CLOUD:
                host = user_input.get(CONF_EXTERNAL_URL, "")
                error = await self.try_tcp_connection(host, 5001)
            case DeviceType.ETHERNET | DeviceType.TCP_DISCOVERED:
                host = user_input.get(CONF_HOST, "")
                port = user_input.get(CONF_PORT, "0")
                error = await self.try_tcp_connection(host, int(port))
            case DeviceType.SERIAL:
                dev = user_input.get(CONF_PATH, "")
                error = await self.try_serial_port(dev)
            case _:
                error=TRANSLATE_ERROR_SETTINGS_MISSING
        return error
