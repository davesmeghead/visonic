# Visonic Alarm Panel for Home Assistant
Custom Component for integration with Home Assistant

# Introduction
Visonic produce the Powermax alarm panel series (PowerMax, PowerMax+, PowerMaxExpress, PowerMaxPro and PowerMaxComplete) and the Powermaster alarm series (PowerMaster 10 and PowerMaster 30). This binding allows you to control the alarm panel (arm/disarm) and allows you to use the Visonic sensors (movement, door contact, ...) within Home Assistant.


# What hardware will you need?
You have a choice, you either connect using RS232/USB or using Ethernet (Wired or Wireless)


If you choose USB then the PowerMax provides direct support for an RS232 serial interface that can be connected to the machine running Home Assistant. The serial interface is not installed by default but can be ordered from any PowerMax vendor (called the Visonic RS-232 Adaptor Kit). The RS232 internal panel interface uses TTL signal levels, this adaptor kit makes it "proper" signal levels.  You may not need the Adaptor Kit if you use a USB interface with TTL RS232 logic levels.
You can also connect the USB cable to a raspberry pi, or any other linux capable device, and use the ser2net program to turn it into a network connected device.
install ser2net on the linux device. edit the ser2net config file like this:

10628:raw:600:/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A700eRHw-if00-port0:9600 8DATABITS NONE 1STOPBIT

be sure to change it to the proper serial port (/dev/ttyUSB0 in most cases). After editing restart the ser2net service.

Now setup the component like the ethernet setup from below, using the raspberry pi IP as IP address and 10628 as the port.
Of course you can use any other port you like, but be sure to also change it in the ser2net config file also and to restart ser2net.

If you choose Ethernet then I can help a bit more as that is what I have. I have a device that connects to the RS232 TTL interface inside the panel (without using the RS-232 Adapter Kit) and creates an Ethernet TCP connection with a web server to set it up. I bought [this](https://www.aliexpress.com/item/USR-TCP232-E-Serial-Server-RS232-RS485-To-Ethernet-TTL-Level-DHCP-Web-Module/32687581169.html)

There is a newer version out called a USR-TCP232-E2

There is a wifi version available like [this](https://www.amazon.co.uk/USR-WIFI232-D2-Module-Ethernet-802-11/dp/B00R2J3O1Y) that's a bit more expensive but essentially it's the same. Although you will need to also buy an aerial for it remember!

You do not need to buy anything else apart from 4 wires. You connect it in to your Visonic alarm panel like [this](https://www.domoticaforum.eu/viewtopic.php?f=68&t=7152)

I connected the 3.75v pin on the panel to the Vcc (3.3v) on this device, gnd to gnd and Tx to Rx, Rx to Tx. 4 wires and that's it.

This allows you to connect your alarm panel to your ethernet home network. There is a webserver running on this and you need to set it up for TCP in STA (station) mode as transparent. The RS232 side is 9600 baud, disable control flow i.e CTS/RTS, no parity and 1 stop bit. In the HA configuration file you set the ip address and port for this device.

Visonic does not provide a specification of the RS232 protocol and, thus, the binding uses the available protocol specification given at the ​domoticaforum. The binding implementation of this protocol is largely inspired by the Vera and OpenHab plugins.


## Release
This is release 0.1.5 : *** 0.1.0 represents a potential breaking change ***

If you have used this Component before, please delete the following files before you copy across version 0.1.0
```
    custom_components/visonic.py
    custom_components/pyvisonic.py
    custom_components/test.py
    custom_components/switch/visonic.py
    custom_components/binary_sensor/visonic.py
    custom_components/alarm_control_panel/visonic.py
    custom_components/alarm_control_panel/__init__.py
```

I have upped the release to what I would call a first Beta version as it is much more robust now. I have converted the Component to the new HA Component file structure

|  Release   |    Description   |
|------------|------------------|
| 0.0.2      | Made some bug fixes |
| 0.0.3      | Removed some test code that would prevent arming. Commented out phone number decoding as creating exceptions. |
| 0.0.4      | Added arm_without_usercode. Include phone number decode with an exception handler just in case. |
| 0.0.5      | Updated the state return values for the generic alarm panel for entry delay (as pending). |
| 0.0.6      | Extracts much more info from EPROM in powerlink. Added more Dutch translations. Handle Access Denied (when entering wrong pin code). A few bug fixes. |
| 0.0.6.2    | Added code to indicate when alarm is triggered (and sounding). This works best in powerlink mode. In Standard mode I make a guess that when the panel is armed and a device is triggered then the alarm must be sounding. |
| 0.0.6.3    | Fix bug in SetConfig, prevent "None" values being accepted. |
| 0.0.6.4    | Removed push change in A3 message as causing exception in HA. |
| 0.0.6.5    | Sensors are based on the HA Entity. Moved "from serial_asyncio import create_serial_connection" to create_usb_visonic_connection so only used for USB connections. |
| 0.0.6.6    | Bug fix to sensor device_class, part of HA Entity update. |
| 0.0.7.0    | *** Breaking change *** Conversion of all sensors from a sensor Entity to a binary_sensor Entity type. This means that a sensor can only have 2 states, off or on. The interpretation in the frontend depends on what is provided by device_class. I have done what I can to get the device class correct but you can change this in your customize configuration section, see below. Remember that the state is now off or on and not "-", "T" or "O". |
| 0.0.7.1    | Updated device attributes for battery level, tripped state, armed state (bypassed or not) and last tripped time to all conform with similar settings within HA itself. |
| 0.0.7.2    | HA 0.86 made a breaking change, alarm control panel entities must return "number" and not "Number". |
| 0.0.8.0    | *** Breaking change ***  X10 devices added, they should be created as a switch. I've removed the old switch entity from previous versions and merged its attributes in to "alarm_control_panel.visonic_alarm". Within a sensor, zone tamper and device tamper are now different. Tamper no longer triggers the alarm sounding in HA, only the siren sounding does this now. |
| 0.0.8.1    | Updated for bugfix to A7 data decode using Powermaster 10 |
| 0.0.8.2    | Added Powermaster Sensor MC-302V PG2 and additional zone status decoding |
| 0.0.8.3    | Bug fixes for X10 Devices when in Standard Mode |
| 0.0.8.4    | Bug fix to the Bug fix for X10 Devices when in Standard Mode |
| 0.0.8.5    | Bug fix for the number format to be displayed on standard mode |
| 0.1.0      | *** Breaking change ***  I have converted the Component to the new HA Component file structure. This Component has a different file structure, please delete all previous files. |
| 0.1.1      | Not officially released |
| 0.1.2      | Assume Powermaster as default panel and allow CRC to be 1 less than calculated value (I don't know why). Update operation when panel connection is lost. Move when Time is Synchronised to be more accurate (Powermax only). Update A7 decode as Powermaster 10 series sends unknown A7 data. Update powerlink timing to hopefully improve reliability. |
| 0.1.3      | Quick fix to exit Download mode quicker when trying Powerlink |
| 0.1.4      | Quick fix to Powerlink, would sometimes take 10 to 15 minutes so put some code back to how it was!! I have also added "exclude" lists for sensors and x10 devices, added additional config parameters, see below |
| 0.1.5      | A few debug and bug fix additions to hopefully help work out control flow of some of the wider communities panels |


## Instructions and what works so far
It currently connects to the panel in Powerlink and it creates an HA:
- Sensor for each alarm sensor - As of release 0.0.7.0 this is a binary sensor with state off or on.
- Alarm Panel integration "alarm_control_panel.visonic_alarm" so you can look at the internal state values
- Switch for each X10 device
- "alarm_control_panel" badge so you can arm and disarm the alarm.

You do not need to use the Master Installer Code. To connect in Powerlink mode, the plugin uses a special Powerlink Code.


### The configuration.yaml file
This is an example from my configuration.yaml file (with values such as host and port changed):
```
visonic:
  device:
    type: ethernet
    host: '192.168.1.128'
    port: 10628
  motion_off: 120
  language: 'EN'
  force_standard: 'no'
  sync_time: 'yes'
  allow_remote_arm: 'yes'
  allow_remote_disarm: 'yes'
  exclude_sensor: [2,3]
  exclude_x10: [1]
#  override_code: '1234'
#  arm_without_usercode: 'yes'
```

You can also have a USB (for RS232) connection:
```
  device:
    type: usb
    path: '/dev/ttyUSB1'
```

The default settings if you miss it out of the configuration.yaml file:

| Name                    | Default | Description | List of values |
|-------------------------|  :---:  |-------------|----------------|
| motion_off              |  120    | The time to keep the zone trigger True after it is triggered. There will not be another trigger for that sensor within this time period. | Integer Seconds |
| language                | 'EN'    | Set the Langauge. 'EN' for English or 'NL' for Dutch. | 'EN' or 'NL' |
| force_standard          | 'no'    | Determine whether it tries to connect in Powerlink mode or just goes to Standard. | 'no' or 'yes' |
| sync_time               | 'yes'   | Attempt to synchronise the time between the device you run HA on and the alarm panel. Powermax only, not Powermaster. | 'no' or 'yes' |
| allow_remote_arm        | 'no'    | Determines whether the panel can be armed from within HA. | 'no' or 'yes' |
| allow_remote_disarm     | 'no'    | Determines whether the panel can be disarmed from within HA. | 'no' or 'yes' |
| override_code           | ''      | If in Standard mode, then this is the 4 digit code used to arm and disarm. See note 1 below | 4 digit string |
| arm_without_usercode    | 'no'    | If in Standard mode, Arm without the usercode (not all panels support this). See note 2 below. | 'no' or 'yes' |
| exclude_sensor          | []      | A list of Zone sensors to exclude e.g to exclude zones Z02 and Z03 then use [2,3] | [1,2 etc] |
| exclude_x10             | []      | A list of X10 devices to exclude e.g to exclude devices X02 and X03 then use [2,3]. For PGM use 0 in the list. | [0,1,2 etc] |

Note 1: If in Powerlink mode then this is not used. If in Standard mode and the override_code is not set then you will have to enter your 4 digit code every time you arm and disarm. It depends on how secure you make your system and how much you trust it.

Note 2: This is only used when in Standard mode. Some panels will arm without entering the 4 digit user code but some will not. So if your panel does not need a user code to arm then set this to 'yes' in your HA configuration files.

### Running it in Home Assistant
Put the files in your custom_components directory that is within your HA config directory. 
I have included the python library REQUIREMENTS in visonic.py but in case that doesn't work you would need to install some python libraries yourself:
```
sudo pip3 install pyserial
sudo pip3 install python-datetime
sudo pip3 install pyserial_asyncio
```

### You can force it in to Standard mode.
It tries to connect in Powerlink mode by default (unless you set force_standard to 'yes').

If the plugin connects in Powerlink mode then it automatically gets the user codes from the panel to arm and disarm.
If the plugin connects in Standard mode then you must provide the user code to arm and disarm. You can either use 'override_code' in the HA configuration or manually enter it each time. Some panels allow arming without the user code. If the mode stays at Download for more than 5 minutes then something has gone wrong.

### How to change the device class
I try to set the device class correctly by default however I don't know if a particular perimeter sensor "magnet" is on a door or window for example.
By default I set all:
- "PIRs" to device_class "motion"
- "magnet" to device_class "window"
- "wired" to device_class "door"

You can change this in your customize configuration like this for example

```
    "binary_sensor.visonic_z04":
      friendly_name: 'Kitchen Door'
      device_class: door
```

### How to use it in Home Assistant Automations
```
- alias: Alarm Armed So Turn Lights Off
  initial_state: 'on'
  trigger:
  - platform: state
    entity_id: alarm_control_panel.visonic_alarm
    to: armed_away
  action:
  - service: script.alarm_armed

- alias: Alarm Disarmed So Email Me
  initial_state: 'on'
  trigger:
  - platform: state
    entity_id: alarm_control_panel.visonic_alarm
    to: disarmed
  action:
  - service: script.alarm_disarmed_email
```
Of course you'll have to write your own scripts!


## Notes
- You need to specify either a USB(RS232) connection or an Ethernet(TCP) connection as the device type, this setting is mandatory. 
- For Powerlink mode to work the enrollment procedure has to be followed. If you don't enroll the Powerlink on the PowerMax the binding will operate in Standard mode. On the newer software versions of the PowerMax the Powerlink enrollment is automatic, and the binding should only operate in 'Powerlink' mode (if enrollment is successful). It will attempt to connect in Powerlink mode 3 times before giving up and going to Standard mode. The 2 advantages of Powerlink mode are that it shows you detailed information about the panel and you do not have to enter your user code to arm and disarm the panel.
- You can force the binding to use the Standard mode. In this mode, the binding will not download the alarm panel setup and so the binding will not know your user code.
- An HA Event 'alarm_panel_state_update' (that you will probably never use) is sent through HA for:
    - The data associated with the event is { 'condition': X }   where X is 1, 2, 3, 4 or 5
        - 1 is a zone update
        - 2 is a panel update
        - 3 is a panel update AND the alarm is active!!!!!
        - 4 is a panel reset
        - 5 is a pin rejected
        - 6 is a tamper alarm
    - I may add more information to the event later if required
- You should be able to stop and start your HA. Once you manage to get it in to Powerlink mode then it should keep working through restarting HA, this is because the powerlink code has successfully registered with the panel.


## What it doesn't do in Home Assistant
- Partitions, it assumes a single partition
- What happens when the alarm is actually triggered (apart from sending an event in HA and setting the Alarm Status state)
- The compatibility of the binding with the Powermaster alarm panel series is probably only partial.
- The USB connection is implemented but was not tested (although other users have got it working).
- You cannot bypass / arm individual sensors using the HA interface.
- The Event Log is not yet implemented. It works but I don't know what to do with it in HA.


## Troubleshooting
OK, so you've got it partially working but it's not quite there.... what can you do.

The first thing to say is that it's a PITA to get it in to Powerlink mode from within HA. I believe the problem is to do with timing issues and the way HA works with asyncio. If I use my python (pyvisonic.py) with a test program from a command line it works every time. When I put it in to HA it works some of the time and I just keep trying until it goes in to Powerlink mode. Is this ideal, No.  Do I have a choice, No.  From experience, if the panel isn't doing what you think it should then leave it alone for a few hours. I believe, although I am not sure, that it has some kind of antitamper in the software for the RS232 interface and it stops allowing Powerlink connectivity.

- I try to get it in to Powerlink mode but it only goes in to Standard mode
  - Check that force_standard is set to 'no'
  - If you have had anything connected to the panel in the past that has been in Powerlink mode then Do a Full Restart (see below what I mean *).
  - The plugin will try 3 times to get in to powerlink mode but then gives up and goes in to Standard mode. If this happens and you want powerlink mode then Do a Full Restart Sequence as defined below. If this doesn't work then leave it for a few hours and try again.
  
- I try to get it in to Powerlink mode but it only goes in to Download mode
  - Has it been like this for less than 5 minutes, then wait as it can take a long time
  - So it's more than 5 minutes, OK. Do a Full Restart Sequence as defined below.
  - If there are still problems then set your logger to output debug data to the log file for the visonic components and send it to me. Something like this:

```
logger:
  default: critical
  logs:
    custom_components.visonic: debug
    custom_components.visonic.pyvisonic: debug
```

(*) Full Restart Sequence for Powerlink:
- Stop HA
- Restart the panel: Restart your Visonic panel by going in to and out of installer mode. Do not do any panel resets, the act of exiting installer mode is enough. 
- Wait for a couple of minutes for the panel to restart
- Start HA.
