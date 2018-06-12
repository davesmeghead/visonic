# Visonic Alarm Panel for Home Assistant
Custom Component for integration with Home Assistant

# Introduction
Visonic produce the Powermax alarm panel series (PowerMax, PowerMax+, PowerMaxExpress, PowerMaxPro and PowerMaxComplete) and the Powermaster alarm series (PowerMaster 10 and PowerMaster 30). This binding allows you to control the alarm panel (arm/disarm) and allows you to use the Visonic sensors (movement, door contact, ...) within Home Assistant.

The PowerMax provides support for a serial interface that can be connected to the machine running Home Assistant. The serial interface is not installed by default but can be ordered from any PowerMax vendor (called the Visonic RS-232 Adaptor Kit).

I have a device that connects to the RS232 interface inside the panel and creates an Ethernet TCP connection with a web server to set it up.

Visonic does not provide a specification of the RS232 protocol and, thus, the binding uses the available protocol specification given at the ​domoticaforum. The binding implementation of this protocol is largely inspired by the Vera and OpenHab plugins.


## Release
This is the first Alpha release 0.0.1

Please be gentle with me, this is my first HA adventure


## Instructions and what works so far
It currently connects to the panel in powerlink and it creates an HA:
- Sensor for each alarm sensor.
- Switch so you can look at the internal state values, the switch itself doesn't do anything. I would like to do a frontend card but I don't currently know how.
- "alarm_control_panel" badge so you can arm and disarm the alarm (only in powerlink mode)

### The configuration.yaml file
This is an example from my configuration.yaml files

```
visonic:
  device:
    type: ethernet
    host: '192.168.1.128'
    port: 10628
  motion_off: 120
  language: 'EN'
  debug: 'yes'
  force_standard: 'no'
  sync_time: 'yes'
  allow_remote_arm: 'yes'
  allow_remote_disarm: 'yes'
  allow_sensor_bypass: 'yes'
```

You can also have a USB (for RS232) connection:

```
  device:
    type: usb
    path: '/dev/ttyUSB1'
    baud: 9600
```


It tries to connect in Powerlink mode by default (unless you set force_standard to 'yes').

Set debug to 'yes' or 'no' to output more or less in the log file

### Running it in Home Assistant
Put the files in your custom_components directory that is within your HA config directory.

## Notes
- You need to specify either a USB connection or a TCP connection. 
- For Powerlink mode to work the enrollment procedure has to be followed. If you don't enroll the Powerlink on the PowerMax the binding will operate in Standard mode. On the newer software versions of the PowerMax the Powerlink enrollment is automatic, and the binding should only operate in 'Powerlink' mode (if enrollment is successful).
- You can force the binding to use the Standard mode. In this mode, the binding will not download the alarm panel setup and so the binding will not know what your PIN code is for example.
- An HA Event 'alarm_panel_state_update' is sent through HA for:
    - 1 is a zone update, 2 is a panel update, 3 is a panel update AND the alarm is active!!!!!
    - The data associated with the event is { 'condition': X }   where X is 1, 2 or 3
    - I may add more information to the event later if required


## What it doesn't do in Home Assistant
- Partitions, it assumes a single partition
- What happens when the alarm is actually triggered
- The compatibility of the binding with the Powermaster alarm panel series is probably only partial.
- The USB connection is implemented but was not tested.
- You cannot bypass / arm individual sensors using the HA interface
- The Event Log is not yet implemented. It works but I don't know what to do with it in HA.
