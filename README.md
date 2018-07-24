# Visonic Alarm Panel for Home Assistant
Custom Component for integration with Home Assistant

# Introduction
Visonic produce the Powermax alarm panel series (PowerMax, PowerMax+, PowerMaxExpress, PowerMaxPro and PowerMaxComplete) and the Powermaster alarm series (PowerMaster 10 and PowerMaster 30). This binding allows you to control the alarm panel (arm/disarm) and allows you to use the Visonic sensors (movement, door contact, ...) within Home Assistant.


# What hardware will you need?
You have a choice, you either connect using RS232/USB or using Ethernet (Wired or Wireless)


If you choose USB then the PowerMax provides direct support for an RS232 serial interface that can be connected to the machine running Home Assistant. The serial interface is not installed by default but can be ordered from any PowerMax vendor (called the Visonic RS-232 Adaptor Kit). The RS232 internal panel interface uses TTL signal levels, this adaptor kit makes it "proper" TTL signal levels.  You may not need the Adaptor Kit if you use a USB interface with TTL RS232 logic levels.


If you choose Ethernet then I can help a bit more as that is what I have. I have a device that connects to the RS232 TTL interface inside the panel (without using the RS-232 Adapter Kit) and creates an Ethernet TCP connection with a web server to set it up. I bought [this](https://www.aliexpress.com/item/USR-TCP232-E-Serial-Server-RS232-RS485-To-Ethernet-TTL-Level-DHCP-Web-Module/32687581169.html)

There is a newer version out called a USR-TCP232-E2

There is a wifi version available like [this](https://www.amazon.co.uk/USR-WIFI232-D2-Module-Ethernet-802-11/dp/B00R2J3O1Y) that's a bit more expensive but essentially it's the same. Although you will need to also buy an aerial for it remember!

You do not need to buy anything else apart from 4 wires. You connect it in to your Visonic alarm panel like [this](https://www.domoticaforum.eu/viewtopic.php?f=68&t=7152)

I connected the 3.75v pin on the panel to the Vcc (3.3v) on this device, gnd to gnd and Tx to Rx, Rx to Tx. 4 wires and that's it.

This allows you to connect your alarm panel to your ethernet home network. There is a webserver running on this and you need to set it up for TCP in STA (station) mode as transparent. The RS232 side is 9600 baud, disable control flow i.e CTS/RTS, no parity and 1 stop bit. In the HA configuration file you set the ip address and port for this device.

Visonic does not provide a specification of the RS232 protocol and, thus, the binding uses the available protocol specification given at the ​domoticaforum. The binding implementation of this protocol is largely inspired by the Vera and OpenHab plugins.


## Release
This is Alpha release 0.0.5

Please be gentle with me, this is my first HA adventure

0.0.2: Made some bug fixes

0.0.3: Removed some test code that would prevent arming. Commented out phone number decoding as creating exceptions

0.0.4: Added arm_without_usercode. Include phone number decode with an exception handler just in case

0.0.5: Updated the state return values for the generic alarm panel for entry delay (as pending)

## Instructions and what works so far
It currently connects to the panel in Powerlink and it creates an HA:
- Sensor for each alarm sensor.
- Switch so you can look at the internal state values, the switch itself doesn't do anything.
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
#  override_code: '1234'
#  arm_without_usercode: 'yes'
```

You can also have a USB (for RS232) connection:
```
  device:
    type: usb
    path: '/dev/ttyUSB1'
```


It tries to connect in Powerlink mode by default (unless you set force_standard to 'yes').

- 'motion_off' (default 180) is in seconds, it is the time to keep the zone trigger True after it is triggered. There will not be another trigger for that sensor within this time period.
- 'language' (default 'EN') can be either EN for English or NL for Dutch
- 'force_standard' (default 'no') determine whether it tries to connect in Powerlink mode or just goes to Standard
- 'sync_time' (default 'yes') attempts to synchronise the time between the device you run HA on and the alarm panel
- 'allow_remote_arm' (default 'no') determines whether the panel can be armed from within HA
- 'allow_remote_disarm' (default 'no') determines whether the panel can be disarmed from within HA
- 'override_code' (default '') If in Powerlink mode then this is not used. If in Standard mode, then this is the 4 digit code used to arm and disarm. If in Standard mode and the override_code is not set then you will have to enter your 4 digit code every time you arm and disarm. It depends on how secure you make your system and how much you trust it!
- 'arm_without_usercode' (default 'no') This is only used when in Standard mode. Some panels will arm without entering the 4 digit user code but some will not. So if your panel does not need a user code to arm then set this to 'yes' in your HA configuration files


### Running it in Home Assistant
Put the files in your custom_components directory that is within your HA config directory. 
I have included the python library REQUIREMENTS in visonic.py but in case that doesn't work you would need to install some python libraries yourself:
```
sudo pip3 install pyserial
sudo pip3 install python-datetime
sudo pip3 install pyserial_asyncio
```

You can force it in to Standard mode.
If the plugin connects in Powerlink mode then it automatically gets the user codes from the panel to arm and disarm.
If the plugin connects in Standard mode then you must provide the user code to arm and disarm. You can either use 'override_code' in the HA configuration or manually enter it each time. Some panels allow arming without the user code. If the mode stays at Download for more than 5 minutes then something has gone wrong.


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
    - 1 is a zone update, 2 is a panel update, 3 is a panel update AND the alarm is active!!!!!
    - The data associated with the event is { 'condition': X }   where X is 1, 2 or 3
    - I may add more information to the event later if required
- You should be able to stop and start your HA. Once you manage to get it in to Powerlink mode then it should keep working through restarting HA, this is because the powerlink code has successfully registered with the panel.


## What it doesn't do in Home Assistant
- Partitions, it assumes a single partition
- What happens when the alarm is actually triggered (apart from sending an event in HA and setting the Alarm Status state)
- The compatibility of the binding with the Powermaster alarm panel series is probably only partial.
- The USB connection is implemented but was not tested.
- You cannot bypass / arm individual sensors using the HA interface
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
    custom_components.alarm_control_panel.visonic: critical
    custom_components.sensor.visonic: critical
    custom_components.pyvisonic: debug
```

(*) Full Restart Sequence for Powerlink:
- Stop HA
- Restart the panel: Restart your Visonic panel by going in to and out of installer mode. Do not do any panel resets, the act of exiting installer mode is enough. 
- Wait for a couple of minutes for the panel to restart
- Start HA.
