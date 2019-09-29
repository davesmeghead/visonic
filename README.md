# Visonic Alarm Panel for Home Assistant
Custom Component for integration with Home Assistant

## Introduction
Visonic produce the Powermax alarm panel series (PowerMax, PowerMax+, PowerMaxExpress, PowerMaxPro and PowerMaxComplete) and the Powermaster alarm series (PowerMaster 10 and PowerMaster 30). This Home Assistant Component allows you to control the alarm panel (arm/disarm) and allows you to use the Visonic sensors (movement, door contact, ...) and X10 devices within Home Assistant.

## What hardware will you need?
You have a choice, you can connect to your Visonic Alarm Panel using RS232, USB or Ethernet (Wired or Wireless)

The PowerMax/Master Alarm Panels provide an internal panel connector that is labelled as the Control interface, the PC interface or the RS232 interface, this interface directly supports a TTL logic level based RS232 protocol. Visonic do not provide a specification of the RS232 protocol and, thus, the Component uses the available protocol specification given at the ​domoticaforum. The binding implementation of this protocol is largely inspired by the Vera and OpenHab plugins.

#### RS232 "Direct" Option
The panels internal RS232 interface can be connected to an RS232 9 pin "DB9" Type connection on the machine running Home Assistant. However, note that the panel interface is an RS232 connector that uses TTL logic signal levels and not "proper" RS232 voltage levels. The Visonic PowerMax serial interface is not installed by default but can be ordered from any PowerMax vendor (called the Visonic RS-232 Adaptor Kit). This adaptor kit makes the TTL logic levels on the panel connector in to "proper" RS232 signal levels. I have never tried this and I don't know of anyone that has. If you do it like this you're pretty much on your own.

#### USB Option
You do not need the Visonic RS-232 Adaptor Kit if you use a USB interface with TTL RS232 logic levels. You can then connect the USB cable to a Windows PC, a raspberry pi, or any other linux capable device such as some NAS devices. If you run Home Assistant on any of these devices it will likely appear to the operating system as a USB device but many of the RS232 to USB devices come with drivers to install that make it appear like a serial device in some way (e.g. on Windows it appears as a COM port)

#### Ethernet Option
##### Ethernet Option 1
If you want to connect your panel to your home ethernet network then you have 2 further options, this is option 1. This option uses a small Linux based device such as the raspberry pi to run the ser2net program to turn it into a network connected device.

Install ser2net on the linux device. edit the ser2net config file like this:

```
10628:raw:600:/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A700eRHw-if00-port0:9600 8DATABITS NONE 1STOPBIT
```

Be sure to change it to the proper serial port (/dev/ttyUSB0 in most cases). After editing, restart the ser2net service.

Now setup the component in Home Assistant like the ethernet setup, using the raspberry pi IP as IP address and 10628 as the port. Of course you can use any other port you like, but be sure to also change it in the ser2net config file also and to restart ser2net. There are users that have this setup and can help in the forums, one main advantage is that you can use the WiFi capability of the raspberry pi.

##### Ethernet Option 2
If you want to connect your panel to your home ethernet network then you have 2 further options, this is option 2. I can help a bit more with this set up as that is what I have. I have a device that connects to the RS232 TTL interface inside the panel (without using the Visonic RS-232 Adapter Kit) and creates an Ethernet TCP connection with a web server to set it up. I bought [this](https://www.aliexpress.com/item/USR-TCP232-E-Serial-Server-RS232-RS485-To-Ethernet-TTL-Level-DHCP-Web-Module/32687581169.html)

There is a newer version out called a USR-TCP232-E2. There is also a wifi version available like [this](https://www.amazon.co.uk/USR-WIFI232-D2-Module-Ethernet-802-11/dp/B00R2J3O1Y) that's a bit more expensive but essentially it's the same. Although you will need to also buy an aerial for it remember!

This allows you to connect your alarm panel to your Ethernet home network. There is a webserver running on this and you need to set it up for TCP in STA (station) mode as transparent. The RS232 side is 9600 baud, disable control flow i.e CTS/RTS, no parity and 1 stop bit. In the HA configuration file you set the IP address and port for this device.

#### Wiring it all up
You do not need to buy anything else apart from 4 wires. For some panels you can buy a connector with a cable that you can use to make it easier. I bought an "IDC 10-pin ribbon cable" for my panel but remember that the panels and connectors can be different. I then split the wires to use the 4 that I needed.

You connect it in to your Visonic alarm panel like [this](https://www.domoticaforum.eu/viewtopic.php?f=68&t=7152)

I connected the 3.75v pin on the panel to the Vcc (3.3v) on my device, gnd to gnd and Tx to Rx, Rx to Tx. 4 wires and that's it. It just worked!
Some users have found that they only have 5 volts available and they need 3.3 volts and so need to use a DC supply regulator (with 2 10uF Capacitors) to generate the 3.3 volts and then they also need a logic level shifter between the 5 and 3.3 volt TTL levels.

## Release
This Component is compliant with the new Component format within the Home Assistant structure.

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
| 0.2.0      | A change of control flow for startup and the addition of the "Standard Plus" mode. Also added the ability to set the Download Code from the configuration file |
| 0.2.1      | X10 Devices available in Standard Plus and Powerlink. Added "zone tripped" attribute to the sensors. Assume siren is not active when panel disarmed. |
| 0.2.2      | Minor updates for PowerMaster Panels, no other changes. |
| 0.2.3      | Minor updates for PowerMaster Panels, more debug logs for PowerMaster panels, no other changes. |
| 0.2.4      | Added HA access to watchdog and download timout counters. Process access denied messages from panel better. |
| 0.2.5      | Process access denied messages from panel better. Lots and lots of debug for EPROM download (but does not show pin codes) |
| 0.2.6      | Some PowerMaster fixes for Downloading EPROM. Lots and lots of debug for EPROM download (but does not show pin codes) |
| 0.2.7      | Changes Download EPROM Technique, its much faster. Lots and lots of debug for Adding Sensors and EPROM download (but does not show pin codes) |
| 0.2.8      | Lots and lots of debug for Adding Sensors and X10. Removed debug logs for EPROM Download. |
| 0.2.9      | Fixed a bug from when there are 20 or more of the same message, I caused a reset of the Component. I should only do this for A5 messages. |
| 0.3.0      | New Control Flow working for PowerMaster 10 and 30 and achieves powerlink much quicker. Also, courtesy of olijouve, a French language translation. Tidied up log entries. Trigger restore if expected not received in 10 seconds. |
| 0.3.1      | Added HA service to bypass/rearm individual sensors in the panel. There is no Frontend for this, just a service for you to call. |
| 0.3.2      | Reworked HA service to bypass/rearm individual sensors in the panel. There is no Frontend for this, just a service for you to call. |
| 0.3.2.1    | Experimental release looking at the B0 PowerMaster messages and whether we can use them to determine PIR motion without the alarm being armed. |
| 0.3.2.2    | Experimental release looking at the B0 PowerMaster messages, included some code to detect motion from the "B0 03 04" data. |
| 0.3.3      | Added a config parameter to almost always display the numeric keypad, including when the User code has been obtained from the EEPROM. Note that the B0 Experimental function is still in there too. |
| 0.3.3.1    | Experimental release to attempt to decode the A7 FF message for PowerMaster Series alarms. It still also includes the B0 experimental decode for sensors too. Feedback on both of these would be appreciated. |
| 0.3.3.2    | A minor feature added to stop doing powerlink attempts and to allow sending on panel timeouts. Experimental release from 0.3.3.1 kept in. Feedback on both of these would be appreciated. |


## Instructions and what works so far
This Component currently connects to the panel and it creates an:
- HA Sensor for each alarm sensor
- Alarm Panel integration Entity "alarm_control_panel.visonic_alarm" so you can look at the internal state values
- HA Switch for each X10 device
- "alarm_control_panel" badge so you can arm and disarm the alarm.
- An HA service to bypass/rearm individual sensors

As of version 0.2.0 I have changed the control flow and introduced a new mode "Standard Plus".
You know which mode you are in by looking at the Entity "alarm_control_panel.visonic_alarm" and the attribute "Mode".

You do not need to use the Master Installer Code from your panel. To connect in Standard Plus and Powerlink mode, the Component uses a special Download Code. This defaults to "5650" (for those with ASCII knowledge this is hex for the characters VP for Visonic Panel I believe), but you can set it in the configuration file.

##### Standard Mode
This is the basic mode where the alarm panel provides an indication of its state and the information about the sensors and X10 devices. However, not all information is available for the Sensors, critically the sensor type is missing. You can arm and disarm the panel by either setting the override_code in the HA configuration file or by entering the code each time manually. The EPROM data is not downloaded from the alarm panel.

##### Standard Plus Mode
As per Standard mode, but in addition the EPROM data has been obtained from the panel. This provides more detailed information about the sensors and X10 devices and it also provides the user code for the panel. You can arm and disarm the panel without entering any user code as the HA Component already knows it. You do not need to use the override_code in the HA configuration file.

##### Powerlink Mode
As per Standard Plus mode, in addition the interaction with the panel is more robust with continual "Powerlink Alive" messages from the Panel. From a functionality view point, there isn't much difference between Standard Plus and Powerlink. There is a more detailed status message from the panel that provides siren status.

##### You can force it in to Standard mode.
It tries to connect in Powerlink mode by default (unless you set force_standard to 'yes').

If the Component connects in Standard Plus or Powerlink mode then it automatically gets the user codes from the panel to arm and disarm.
If the Component connects in Standard mode then you must provide the user code to arm and disarm. You can either use 'override_code' in the HA configuration or manually enter it each time. Some panels allow arming without the user code.

##### The Startup Control Flow
I have worked on the control flow for release 0.2.0 (instead of copying the control flow from other plugins used in other devices) and have altered it so it is more reliable for my panel, I believe that it removes some (or all?) of the timing dependencies. For those interested in the startup control flow.....
- At component start, it checks the "Force Standard" user setting. If set it goes directly to Standard Mode and stays there.
- If "Force Standard" is not set then it tries to Download the EPROM data from the panel.
    - If this fails immediatelly then it keeps retrying every 4 minutes.
    - If it fails part way through the download there is something more seriously wrong and it goes to Standard Mode and stays there.
    - The Download step itself takes about 40 seconds on my panel, 
- When EPROM Download succeeds is goes to Standard Plus mode.
    - I get Standard Plus Mode within a minute or so of starting HA.
    - I expect that most people will be happy to get it to Standard Plus mode.
- It then starts trying to Enroll as a Powerlink device, 
    - Trying every 4 minutes.
    - It can't try too often as the panel just blocks the communication, thinking that it's an attack I think, so 4 minutes seems OK
    - I get Powerlink Mode within 2 minutes of starting HA
    - Sometimes it fails the first time (fails to Enroll) and it works the next time around, so about 5 minutes after starting HA

In other words, the Component continues to try and do the best it can and doesn't give up like previous versions. Previous versions attemped Powerlink enrollment (3 times) and if that failed did not then attempt to Download the EPROM. It now does it the other way around and keeps trying. If I get problems with it trying too often then I may need to limit it.

### The configuration.yaml file
This is an example from my configuration.yaml file (with values such as host and port changed):
```
visonic:
  device:
    type: ethernet
    host: !secret visonic_ip
    port: !secret visonic_port
  motion_off: 120
  language: 'EN'
  force_standard: 'no'
  sync_time: 'yes'
  allow_remote_arm: 'yes'
  allow_remote_disarm: 'yes'
  exclude_sensor: [2,3]
  exclude_x10: [1]
#  force_numeric_keypad: 'yes'
#  override_code: '1234'
#  download_code: '9876'
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
| language                | 'EN'    | Set the Langauge. 'EN' for English, 'NL' for Dutch or 'FR' for French. | 'EN', 'NL' or 'FR' |
| force_standard          | 'no'    | Determine whether it tries to connect in Standard Plus & Powerlink mode or just goes to Standard. | 'no' or 'yes' |
| sync_time               | 'yes'   | Attempt to synchronise the time between the device you run HA on and the alarm panel. Powermax only, not Powermaster. | 'no' or 'yes' |
| allow_remote_arm        | 'no'    | Determines whether the panel can be armed from within HA. | 'no' or 'yes' |
| allow_remote_disarm     | 'no'    | Determines whether the panel can be disarmed from within HA. | 'no' or 'yes' |
| override_code           | ''      | If in Standard mode, then this is the 4 digit code used to arm and disarm. See note 1 below | 4 digit string |
| download_code           | '5650'  | This is the 4 digit code used to download the EPROM and to Enroll for Powerlink. | 4 digit hex string |
| arm_without_usercode    | 'no'    | If the Panel is Disarmed, then Arm without the usercode (not all panels support this). See note 2 below. | 'no' or 'yes' |
| force_numeric_keypad    | 'no'    | Display the numeric keypad to force the user to enter the correct code (in any Mode). The only exception is the use of arm_without_usercode. | 'no' or 'yes' |
| exclude_sensor          | []      | A list of Zone sensors to exclude e.g to exclude zones Z02 and Z03 then use [2,3] | [1,2 etc] |
| exclude_x10             | []      | A list of X10 devices to exclude e.g to exclude devices X02 and X03 then use [2,3]. For PGM use 0 in the list. | [0,1,2 etc] |

Note 1: If in Standard Plus or Powerlink mode then this is not used. If in Standard mode and the override_code is not set then you will have to enter your 4 digit code every time you arm and disarm. It depends on how secure you make your system and how much you trust it.

Note 2: This is only used when in Standard mode. Some panels will arm without entering the 4 digit user code but some will not. So if your panel does not need a user code to arm then set this to 'yes' in your HA configuration files.

### Running it in Home Assistant
Put the files in your custom_components directory that is within your HA config directory. 
I have included the python library REQUIREMENTS in visonic.py but in case that doesn't work you would need to install some python libraries yourself:
```
sudo pip3 install pyserial
sudo pip3 install python-datetime
sudo pip3 install pyserial_asyncio
```

### How to change the device class
In Standard Plus & Powerlink Modes, I try to set the device class correctly by default however I don't know if a particular perimeter sensor "magnet" is on a door or window for example.
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

### Home Assistant Visonic Panel Services
The Component responds to some of the built in HA Alarm Panel Services. The first 3 are HA built in, the 4th I have added.

As an aside, I couldn't get the built in service "alarm_arm_custom_bypass" to work and then realised this is for the panel as a whole and not individual sensors. It is not yet implemented.

In all 4 services the "code" service data is optional, depending on the mode that we're connected as i.e. in Standard mode then you will need to set the code.

| Name                    | Description | Example Service Data |
|-------------------------|-------------|----------------------|
| alarm_control_panel.alarm_arm_away | Arm the panel away | "entity_id":"alarm_control_panel.visonic_alarm" |
| alarm_control_panel.alarm_arm_home | Arm the panel home | "entity_id":"alarm_control_panel.visonic_alarm" |
| alarm_control_panel.alarm_disarm   | Disarm the panel   | "entity_id":"alarm_control_panel.visonic_alarm" |
| visonic.alarm_sensor_bypass        | Bypass/Arm individual sensors (must be done when panel is disarmed).  | "entity_id":"binary_sensor.visonic_z01", "bypass":"True" |


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
  - service: script.alarm_disarmed
```
Of course you'll have to write your own scripts!


## Notes
- You need to specify either a USB(RS232) connection or an Ethernet(TCP) connection as the device type, this setting is mandatory. 
- For Powerlink mode to work the enrollment procedure has to be followed. If you don't enroll the Powerlink on the Alarm Panel the binding will operate in Standard or Standard Plus mode. On the newer software versions of the PowerMax panels the Powerlink enrollment is automatic, and the binding should only operate in 'Powerlink' mode (if enrollment is successful).
- You can force the binding to use the Standard mode. In this mode, the binding will not download the alarm panel EPROM and so the binding will not know your user code.
- An HA Event 'alarm_panel_state_update' (that you will probably never use) is sent through HA for:
    - The data associated with the event is { 'condition': X }   where X is 1, 2, 3, 4, 5, 6, 7 or 8
        - 1 is a zone update
        - 2 is a panel update
        - 3 is a panel update AND the alarm is active!!!!!
        - 4 is a panel reset
        - 5 is a pin rejected
        - 6 is a tamper alarm
        - 7 is an EPROM download timeout, go to Standard Mode
        - 8 is a watchdog timeout, give up trying to achieve a better mode
        - 9 is a watchdog timeout, going to try again to get a better mode
    - I may add more information to the event later if required

## What it doesn't do in Home Assistant
- Partitions, it assumes a single partition
- What happens when the alarm is actually triggered (apart from sending an event in HA and setting the Alarm Status state)
- The compatibility of the binding with the Powermaster alarm panel series is probably only partial.
- The USB connection is implemented but was not tested (although other users have got it working).
- You cannot bypass / arm individual sensors using the HA interface. I can do it, I just don't know how to interface to it from within HA.
- The Event Log is not yet implemented. It works but I don't know what to do with it in HA.

## Extra Hidden Functionality
There are 2 extras that I include in the release

#### For Testing without HA
Disable the Component in HA (or disable HA altogether) and use the test.py script from a command line like this
```
python3 test.py -address 192.168.X.Y -port YourPort
On Linux:   python3 test.py -usb /dev/ttyUSB1
On Windows: python3 test.py -usb COM1
```
It will perform like it does in HA but from the command line. Note that the other settings from the configuration can be changed by editing test.py

#### "Powermaster Remote Programmer" Bridging
This is a recent addition, you can use the "Powermaster Remote Programmer" (PRP) from a Windows PC to connect to your panel if using the Ethernet option
- Download and setup com0com on your PC with a Virtual RS232 connection using COM1 and COM2 (assuming these aren't existing real devices on your Windows PC)
- Run bridge.py from a command prompt and connect to COM1 like this
```
python3 bridge.py -address 192.168.X.Y -port YourPort -usb COM1
```
- Run PRP and connect to COM2

You can then use PRP with your panel, the bridge command prompt displays the messages going to/from the panel

## Troubleshooting
OK, so you've got it partially working but it's not quite there.... what can you do.

The first thing to say is that it's a PITA to get it in to Powerlink mode from within HA. I believe the problem is to do with timing issues and the way HA works with asyncio. Is this ideal, No.  Do I have a choice, No.  From experience, if the panel isn't doing what you think it should then leave it alone for a few hours. I believe, although I am not sure, that it has some kind of antitamper in the software for the RS232 interface and it stops allowing Powerlink connectivity.

- I try to get it in to Standard Plus or Powerlink mode but it only goes in to Standard mode
  - Check that force_standard is set to 'no'
  - If you have had anything connected to the panel in the past that has been in Powerlink mode then Do a Full Restart (see below what I mean *).
  
- I try to get it in to Powerlink mode but it only goes in to Download mode
  - Has it been like this for less than 4 minutes, then wait as it can take a long time with some devices and panels
  - So it's more than 4 minutes, OK. Do a Full Restart Sequence as defined below.
  - If there are still problems then set your logger to output debug data to the log file for the visonic components and send it to me. In your configuration.yaml file do it exactly like this so I only get logged data from my Component:

```
logger:
  default: critical
  logs:
    custom_components.visonic: debug
    custom_components.visonic.pyvisonic: debug
    custom_components.visonic.alarm_control_panel: debug
    custom_components.visonic.binary_sensor: debug
    custom_components.visonic.switch: debug
    custom_components.visonic.__init__: debug
```

(*) Full Restart Sequence for Powerlink:
- Stop HA
- Restart the panel: Restart your Visonic panel by going in to and out of installer mode. Do not do any panel resets, the act of exiting installer mode is enough. 
- Wait for a couple of minutes for the panel to restart
- Start HA.
