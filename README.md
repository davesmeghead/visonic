[![](https://img.shields.io/badge/MAINTAINER-%40Davesmeghead-green?style=for-the-badge)](https://github.com/Davesmeghead)
[![Buy me a coffee][buymeacoffee-shield]][buymeacoffee]

[buymeacoffee]: https://www.buymeacoffee.com/davesmeghead
[buymeacoffee-shield]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png

# Development Release

# Visonic Alarm Panel for Home Assistant

This is a *custom integration* for [Home Assistant](https://www.home-assistant.io/).

Please know that this develepment/beta release is a change that is likely to have trouble going back to the main "master" release as it changes/updates the configuration to align itself with Home Assistant guidelines. The current master release of this integration does not follow these guidelines. If you start using this development release then the only way to go back to the master is to delete the hubs for all panels, replace the visonic directory software with the current master release and recreate the hubs.

The only way to install this development/beta release (0.13.x.y) is to download the code zip file, delete (or backup) the current "master" release *custom_components/visonic* directory and replace it with the one from the downloaded zip file. You may want to keep a backup of the existing master release directory instead of deleting it.

## Introduction (Development Release)

This is a custom integration to allow the control of a Visonic alarm system. Visonic produce a series of Alarm Systems and this integration is compatible with the following models:

- PowerMax+ (plus), PowerMax Express, PowerMax Pro , PowerMax Pro Plus, PowerMax Complete and PowerMax Complete Plus
- PowerMaster-10, PowerMaster-30 and PowerMaster-33
- PowerMaster-360 and PowerMaster-360R in a limited way

How to connect to your panel, supported by this dev/beta release

Remember to check out the Wiki-section, this contains all the documentation except for the new configuration settings such as for cloud [Wiki Home](https://github.com/davesmeghead/visonic/wiki). 

### Direct Connection
This is an extension of the current integrations capability. For PowerMax and PowerMaster Panels, a __"Direct"__ wired/wifi connection using:
  - Direct Ethernet to a TCP Server device, using IP address and port number (that could be an ESP32 device using ESPHome)
  - Direct USB/Serial using a cable (to a COM port on windows or a /dev/ttyUSBx on linux for example)
  - Using an ESPHome serial_proxy (over Ethernet/Wifi).  Note that you must have the ESPHome integration installed and the ESP device registered. When the serial_proxy is available in Home Assistant it is listed as a serial device.

This allows you to:

- Control the alarm panel to arm and disarm,
- Bypass/Arm individual sensors,
- Use the various sensors as devices and entities,
- Use the X10 devices (on supported panel types).
- On PowerMaster panels, trigger the Siren, Panic Alarm and Fire/Emergency
- On PowerMaster panels, the baud rate is auto negotiated with the panel
    - for PowerMax panels the baud is always 9600

### Cloud Connection

For PowerMaster Panels using a Powerlink 3.1 hardware module connected to your LAN, a connection to a __"Cloud"__ server that you have a login and password for. This integration operates in parallel with your Visonic Go app on your mobile phone.

The Home Assistant Integration, __"Cloud"__ connection is limited in its capabilities:

- PIR/motion detections do not come through to Home Assistant for example
- As it is a polled system (you set the poll time in the configuration) the sensor updates are slower
- Devices and Switches, including the PGM, are not included

Cloud configuration settings:
- Server URL e.g. visonic.tycomonitor.com
- Login email and password (what you use to login to the Visonic Go app)
- Panel user code
- Panel Serial ID - If you only have 1 panel registered then you can leave this empty (the integration will use the first panel in the list of retrieved panels)
- Period of time between polling, minimum 15 seconds is suggested as this is used to poll the cloud server
- Sensors and switches to exclude, note that switches are currently not supported so leave the Switch list empty

## Development / Beta Release Notes

This is a development/beta release, please give it a try and give me feedback in the forum, as a github discussion or as a specific github issue, but if you do them please make sure to tell me it's for the development "dev" release.

There are several new capabilities in the dev release:

1. I think that the main new capability is the "cloud" capability for PowerMaster panels (See note 1 below). I have used Tyco for testing but it should work with others.

   The integration can be used alongside the Visonic Go app.
   There are 2 main limitations:
      - Updates and control is slower than a direct connection. You set the update rate in the configuration but I suggest nothing faster/less than 15 seconds as it bombards the visonic cloud server.
      - PIR/Motion sensors are created in HA but they are never triggered, motion events are not transacted.
2. I create all entities that are relevant to that visonic sensor/device. See lists below.
3. I include the battery state entity for Keyfob and Keypads
4. I have added Reconfigure to the integration menu, you can now change the main parameters without deleting and recreating (though it does reload).
5. I have fully refactored all the code to align to the Home Assistant guidelines. I could, in theory, submit this to HA for approval to get it in the core as a core integration. I have also followed the Home Assistant guidelines and embodied a "coordinator". One "side" of the software creates the data, the data is passed to the other "side" which presents the data in the form of the entities in HA.

Each Visonic Sensor, Switch and Device (e.g. keyfob) is represented in Home Assistant as a __Device__.
The Panel is also represented by a single __Device__, this also includes a single Siren Entity.

Just a quick Home Assistant refresher for you on terminology:

- A __Device__ is a physical thing e.g. A SD-304C PG2 Visonic Shock Sensor
- An __Entity__ represents one aspect of a Device e.g. Motion, Tamper, Battery Level etc

There is a Home Assistant Device for each visonic: Sensor, KeyFob, KeyPad, Switch and Panel (I have included the Siren Entity in with the Panel Device).

#### Visonic Sensors

Each Home Assistant Device has a number of Entities depending on the Visonic Sensor Type and Model:

- All have Trouble and Tamper Entities
- All have a Select Entity to set the Armed Mode (Armed / Bypass). The entity name has "_arm_mode" added.
- All except Wired Contact Sensors have a Battery Entity
- All sensors that are single function have an Entity called "Zone"
  - "state" --> Magnet/Contact Sensors have a State Entity i.e. Open/Closed
  - "trigger/event" --> PIR/Camera/Motion/Gas Sensors have a Motion, Gas etc Entity
  - Note that device_triggered and zone_open attributes have been removed. Each is its own Entity now.
- There is only 1 visonic sensor I know of that is multifunction, it has both a trigger and a state, a SD-304C PG2 Visonic Shock Sensor.
  - A "Zone" Entity is created for the Shock "trigger" part of the sensor and a "Contact" Entity is created for the "state" magnet/contact part of the sensor.
- Please remember that for contact sensors I do not know where you have put them, Doors, Windows, Garage Doors etc so you have to change this manually

#### Visonic KeyFob, KeyPad

- A single Battery Entity

#### Visonic Alarm Panel

- Tamper, Battery and Trouble Entities
- A Main Panel Entity
- Partition Entities, 1 per partition if they are configured in the panel
- A Siren Entity is also associated with this Device rather than separate/standalone.
  - This may change in future but the entity name should remain the same (unless I find a way to determine multiple sirens!)

#### Visonic Switch

- A single Switch Entity
- As far as I can tell, all panels support a PGM switch. All the panels that I have create a PGM switch. For example, on a PowerMaster 30 buttons 3, 6 and 9 control the PGM.
- A __Cloud__ connection does not create any switch devices

### Notes

1. For Cloud:
   - It does not support PowerMax panels, only PowerMaster that you have already got working with a Visonic Server and the Visonic Go phone app.
   - I have used and updated the library by [Mark Parker](https://github.com/msp1974) to be asyncio for the cloud connection
   - The configuration settings should be obvious. If you only have a single panel then you can miss out the 6 digit panel ident and it will use the first panel it finds. (this is usually found on the back of the panel).
2. Sensor Location
   - In the python code I could look up the __area__ in HA and if I got a match then assign a sensor to that area. It worked well, but it also changed the sensor name e.g. binary_sensor.kitchen_visonic_z01_zone. So I have excluded this at the moment
3. I have updated the language translation files with new keys but the values are still in English.
4. I plan to update 2 libraries on PyPi:
   - pyvisonic for the direct connection with everything in the pyvisonic directory and
   - pyvisonicalarm on Mark's Github/PyPi for the cloud connection, when I get it a bit more mature

## Remember the caveat at the top of this page, to go back to the "master" you will likely have to delete the hub, replace the visonic software and recreate your hub

## Configuration

The only way to setup this integration is by using the Integration page within Home Assistant. However, this integration also supports auto discovery through zeroconf and mDNS

The master release of this integration is supported by HACS, first install HACS and then find this integration in the HACS list.
However, this development integration must be manually installed.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=visonic)
