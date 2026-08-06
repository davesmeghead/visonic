"""Constants and abstract base classes for the alarm panel integration."""

from datetime import timedelta
from enum import IntEnum
import os

LIBRARY_VERSION = "2.0.0.3"

NOBYPASSSTR = "No Bypass"
DISABLE_TEXT = "Disable"
MAX_PARTITIONS = 3             # The maximum number of partitions that any of the visonic panels can support

# Whether to download all the EPROM from the panel or to just download the parts that we get usable data from
EPROM_DOWNLOAD_ALL = False
NO_DELAY_SET = 0xFFFF

TEXT_PANEL_MODEL = "Panel Model"
TEXT_WATCHDOG_TIMEOUT_TOTAL = "Watchdog Timeout (Total)"
TEXT_WATCHDOG_TIMEOUT_DAY = "Watchdog Timeout (Past 24 Hours)"
TEXT_DOWNLOAD_TIMEOUT = "Download Timeout"
TEXT_DL_MESSAGE_RETRIES = "Download Message Retries"
TEXT_PROTOCOL_VERSION = "Protocol Version"
TEXT_POWER_MASTER = "Power Master"

# These problem values are in the language json file file for zone_trouble
TEXT_NONE       = "none"
TEXT_TAMPER     = "tamper"
TEXT_JAMMING    = "jamming"
TEXT_COMM_FAIL  = "comm_failure"
TEXT_LINE_FAIL  = "line_failure"
TEXT_FUSE       = "fuse"
TEXT_NOT_ACTIVE = "not_active"
TEXT_AC_FAIL    = "ac_failure"

###################################################################################
###### Global variables used to determine what is included in the log file ########
###################################################################################

# Obfuscate sensitive data, regardless of the other Debug settings.
#     Setting this to True limits the logging of messages sent to the panel to CMD or NONE
#                     It also limits logging of received data

OBFUS = os.getenv("HA_DEBUG_NO_OBFUSCATION") != "1"
#OBFUS = True

# Whether to include B0 35 and B0 42 panel data decode in the log file.  Note that this is also combined with OBFUS.
B0_35_PANEL_DATA_LOG = True  # True or False
B0_42_PANEL_DATA_LOG = B0_35_PANEL_DATA_LOG

class DebugLevel(IntEnum):
    """Debug level."""
    NONE = 0   # 0 = do not log this message
    CMD  = 1   # 1 = Show only the msg string in the log file, not the message content
    FULL = 2   # 2 = Show the full data in the log file, including the message content

###################################################################################
### Global variables used to configure specific timeouts and maximum settings. ####
### These also help readability of the code.                                   ####
###################################################################################

# Maximum number of CRC errors on receiving data from the alarm panel before performing a restart
#    This means a maximum of 5 CRC errors in 10 minutes before resetting the connection
MAX_CRC_ERROR = 5
CRC_ERROR_PERIOD = 600  # seconds, 10 minutes

# Maximum number of received messages that are exactly the same from the alarm panel before performing a restart
SAME_PACKET_ERROR = 10000

# If we are waiting on a message back from the panel or we are explicitly waiting for an acknowledge,
#    then wait this time before resending the message.
#  Note that not all messages will get a resend, only ones waiting for a specific response and/or are blocking on an ack
RESEND_MESSAGE_TIMEOUT = timedelta(seconds=30000) # Not currently used

# We must get specific messages from the panel, if we do not in this time period (seconds) then trigger a restore/status request
WATCHDOG_TIMEOUT = 120

# If there has been a watchdog timeout this many times per 24 hours then go to standard (plus) mode
WATCHDOG_MAXIMUM_EVENTS = 10

# Response timeout, when we send a PDU this is the time we wait for a response (defined in replytype in VisonicCommand)
RESPONSE_TIMEOUT = timedelta(seconds=5)

# If a message has not been sent to the panel in this time (seconds) then send an I'm alive message
KEEP_ALIVE_PERIOD = 25  # Seconds

# When we send a download command wait for DownloadMode to become false.
#   If this timesout then I'm not sure what to do, maybe we really need to just start again
#   In Vera, if we timeout we just assume we're in Standard mode by default
DOWNLOAD_TIMEOUT = 90

# Default Download Code
DEFAULT_DL_CODE = "5650"

# Number of seconds delay between trying to achieve EPROM download
DOWNLOAD_RETRY_DELAY = 60

# Number of times to retry the download, this is a total
DOWNLOAD_RETRY_COUNT = 10

# Whether to download the EPROM or to use default to get the panel data, this is or'd with CFG.EPROM_DOWNLOAD in pmPanelConfig and used for debug
FORCE_DOWNLOAD_TO_USE_EPROM = True

# Number of times to retry the retrieval of a block to download, this is a total across all blocks to download and not each block
DOWNLOAD_PDU_RETRY_COUNT = 30

# Number of seconds delay between not getting I'm alive messages from the panel in Powerlink Mode
POWERLINK_IMALIVE_RETRY_DELAY = 100

STANDARD_STATUS_RETRY_DELAY = 88   # must be divisible by 4

# Maximum number of seconds between the panel sending I'm alive messages
MAX_TIME_BETWEEN_POWERLINK_ALIVE = 60

# This is the minimum time interval (in milli seconds) between sending subsequent messages to the panel so the panel has time to process them.
#    This value is based on the slowest supported panel
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMAX = 190
MINIMUM_PDU_TIME_INTERVAL_MILLISECS_POWERMASTER = 150

# The number of seconds that if we have not received any data packets from the panel at all (from the start) then suspend this plugin and report to HA
#    This is only used when no data at all has been received from the panel ... ever
NO_RECEIVE_DATA_TIMEOUT = 30

# The number of seconds between receiving data from the panel and then no communication (the panel has stopped sending data for this period of time) then suspend this plugin and report to HA
#    This is used when this integration has received data and then stopped receiving data
LAST_RECEIVE_DATA_TIMEOUT = 240  # 4 minutes

# Interval (in seconds) to get the time and for most panels to try and set it if it's out by more than TIME_INTERVAL_ERROR seconds
#     PowerMaster uses time interval for checking motion triggers so more critical to keep it updated
POWERMASTER_CHECK_TIME_INTERVAL =   180  # 3 minutes  (this uses B0 messages and not DOWNLOAD panel state)  Divisible by 4
IMAGE_TRANSFER_TIMEOUT =            40  # seconds of silence before a part built camera image is abandoned
POWERMAX_CHECK_TIME_INTERVAL    = 14400  # 4 hours    (this uses the DOWNLOAD panel state)
TIME_INTERVAL_ERROR = 3

THREE_SECONDS = 3

PACKET_MAX_SIZE = 0xF0
#ACK_MESSAGE = 0x02

# This string is used in the log file to indicate that I have no idea what it means, and that I'm investigating it.  If has to be unique so I can search all log files for it.
notknown = ":NotKnown:"

#from .pyhelper import vloggerclass
#log = vloggerclass(mylog, 0, False)

# Part or the F4 Image Transfer panel event dictionary
FAILED="failed"
DEGRADED="degraded"
SUCCESS="success"
DELAYED="delayed"
ABORTED="aborted"
