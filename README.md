# Visonic Alarm Panel for Home Assistant

A Custom Integration for Home Assistant

## Introduction
Visonic produce the Powermax and Powermaster alarm panel series including (PowerMax+, PowerMaxExpress, PowerMaxPro and PowerMaxComplete, PowerMaster 10 and PowerMaster 30).

This Home Assistant Integration allows you to control the alarm panel (arm/disarm) and allows you to use the Visonic sensors and events (movement, door contact, ...) and X10 devices within Home Assistant.

Please note that after extensive work, the original PowerMax Panel is not able to be used as it does not support the Powerlink protocol that this component relies on. 

Also the Visonic 360R Alarm Panel is not fitted with an RS232 connection and cannot have a Powerlink3 fitted, it is therefore not compatible with this HA Component.

## Wiki
Remember to check out our Wiki-section, this contains all the documentation.

- [Wiki Home](https://github.com/davesmeghead/visonic/wiki)

_If you have notes related to a specific solution where this component is used, you're mostly welcome to provide both details and full guides to the Wiki-section!_


## Release
This Component is compliant with the new Component format within the Home Assistant structure.

|  Release   |    Description   |
|------------|------------------|
| 0.5.0.1    | HACS Release and minor bug fix |
| 0.5.0.0    | Used the preferred home assistant code formatter, so all code files modified. No functional changes, a few bug fixes |
| 0.4.4.4    | Bug fix for the combination of Arm Without Code and panel Disarmed when in Powerlink or Standard Plus. |
| 0.4.4.3    | Specific update for misreporting of PIR sensors on PowerMax Pro model 62. |
| 0.4.4.2    | Updated test.py to reflect changes to the callback. |
| 0.4.4.1    | Fixed a bug in the reporting of PanelReady in the HA Event. |
| 0.4.4.0    | Circumvent an HA bug that prevents a config parameter being set back to an empty string. Release for HACS. |
| 0.4.3.0    | A few bug fixes and a change for the new HA version 0.110. Also added conversion for config setting boolean imports. Release for HACS. |
| 0.4.2.0    | Restructure the panel data decoding in to PDUs. Release for HACS. Fix B0 Message decode bug. |
| 0.4.1.0    | Restructure the panel data decoding in to PDUs. Release for HACS. |
| 0.4.0.9    | A bit more tidying up for variable message decoding. |
| 0.4.0.8    | Modified decoding of B0 message data and then made the other variable message decoding the same. |
| 0.4.0.7    | Bug fix for exclude_x10 and exclude_sensor when the lists are empty |
| 0.4.0.6    | Bug fix for ability to create the integration from within Home Assistant |
| 0.4.0.5    | Bug fix for USB connections |
| 0.4.0.4    | Bug fix for ability to create/edit the integration from within Home Assistant. Breaking change if you use exclude_x10 and/or exclude_sensor  |
| 0.4.0.3    | Bug fix for USB connections |
| 0.4.0.2    | Bug fix for USB connections |
| 0.4.0.1    | Bug fix for USB connections |
| 0.4.0.0    | Added the ability to create/edit the integration from within Home Assistant. Major change but hopefully not a breaking change. |
| 0.3.7.0    | Several changes to HA event generation, I have updated the wiki so please read it. There are 2 new config settings arm_away_instant and arm_home_instant, 2 new HA Services alarm_panel_command and alarm_panel_download. |
| 0.3.6.3    | Changed Ready to PanelReady  |
| 0.3.6.2    | Updated the new HA Event as condition 11 and added new with condition 12. "Ready" has been added which copies the received "Panel Ready" attribute. A "message" is also added. Either an 11 or 12 HA event is generated.  |
| 0.3.6.1    | Added new config entry called "siren_sounding" as a list of strings. Added new HA Event as condition 11, triggered when the user tries to arm/diasrm the panel (but before the command is sent to the panel) |
| 0.3.6.0    | Added an additional attribute called "Panel Last Event Data" to the panel entity, this is a complex record of the A7 message (the panel state) |
| 0.3.5.8    | Panel type Powermax+ (panel model 1) and Powermax Pro (panel model 2) need to manually enroll.  Code updated to reflect this and added new readme section about panel types. Moved most of the info to the wiki. |
| 0.3.5.7    | Experiment for Panel Model 2 (Powermax Pro) to miss out the Enroll command. |
| 0.3.5.6    | Alarm Panel Siren handling updated.  Detect when no data received from panel from the start up to 30 seconds then something wrong.  No change to B0 Experimental message processing. |
| 0.3.5.5    | Alarm Panel Siren handling updated.  Bug fix to remove wait_for warnings.  No change to B0 Experimental message processing. |
| 0.3.5.4    | Added new config parameter "force_autoenroll". This is a breaking change for Powermax+ users, they need to set this to 'No' in their configuration file. "force_autoenroll" is only used when the panel rejects an EPROM download request and we do not know the panel type. No change to B0 Experimental message processing. |
| 0.3.5.3    | As per 0.3.5.2. Updated Release of Panel Event Log Processing. Minor Functional Change (battery status). No change to B0 Experimental message processing. |
| 0.3.5.2    | Updated Release of Panel Event Log Processing. Minor Functional Change. No change to B0 Experimental message processing. |
| 0.3.5.1    | Updated Release of Panel Event Log Processing. No change to B0 Experimental message processing. |
| 0.3.5      | First Release of Panel Event Log Processing. Several configuration.yaml parameters added, a new service to call to start it and several new HA events can be generated. No change to B0 Experimental message processing. |
| 0.3.4.15   | Updated Panel Event Log Processing. No change to B0 Experimental message processing. |
| 0.3.4.14   | Changed the Im alive and status message exchange when in standard, standard plus and powerlink modes. No change to B0 Experimental message processing. |
| 0.3.4.13   | Updated event and zone information in the panel attributes (Panel Last Event) for PowerMaster panels (a lot more zones and event types). No change to B0 Experimental message processing. |
| 0.3.4.12   | Bug fix in 0.3.4.11 B0 Experimental message processing. |
| 0.3.4.11   | No change to B0 Experimental message processing. Updated timeout sequence. If in download then go to Standard Mode and retry Download in 90 seconds. Shortened download timeouts as they are generally much faster now. |
| 0.3.4.10   | Tidied up the new service call operation. Updated B0 Experimental message processing. |
| 0.3.4.9    | Added more test code to better connect Powermax+ and understand whats going on. Altered the new service call operation to be compatible with the disconnect procedure. |
| 0.3.4.8    | Added more test code to better connect Powermax+ and understand whats going on. Realised that 0.3.4.7 had a problem so it's fixed |
| 0.3.4.7    | Added more test code to better connect Powermax+ and understand whats going on. Added a send of MSG_RESTORE to the panel when achieved Standard Plus. |
| 0.3.4.6    | When using Ethernet to connect to the panel, I have added the socket options to keep the connection alive with a long timeout. Added code to flush the receive buffer. |
| 0.3.4.5    | When using Ethernet to connect to the panel, I have added the socket options to keep the connection alive with a long timeout. |
| 0.3.4.4    | As per 0.3.4. Added more and more test code to better connect Powermax+ and understand whats going on. |
| 0.3.4.3    | As per 0.3.4. Added more test code to better connect Powermax+ and understand whats going on. Updated new service call code to keep alarm panel in frontend. |
| 0.3.4.2    | As per 0.3.4. Added further test code to better connect Powermax+ and understand whats going on. |
| 0.3.4.1    | As per 0.3.4. Added service to close and reconnect to the panel, it is visonic.alarm_panel_reconnect with no parameters. Added test code to better connect Powermax+ and understand whats going on. |
| 0.3.4      | Added PowerMaster shock sensor (as device type vibration). PowerMaster Experimental function retained. Note that the original Powermax is not supported as it doesn not provide the powerlink protocol. |
| 0.3.3.10   | Updated to include decode of message 0x22 in the same way as an 0x3C. This is for the older powermax users. More tries! |
| 0.3.3.9    | Updated to include decode of message 0x22 in the same way as an 0x3C. This is for the older powermax users. Next Next Next try! |
| 0.3.3.8    | Updated to include decode of message 0x22 in the same way as an 0x3C. This is for the older powermax users. Next Next try! |
| 0.3.3.7    | Updated to include decode of message 0x22 in the same way as an 0x3C. This is for the older powermax users. Next try! |
| 0.3.3.6    | Updated to include decode of message 0x22 in the same way as an 0x3C. This is for the older powermax users. |
| 0.3.3.5    | Updated to include flushing the input buffer prior to sending data and updated bridge to include COM to COM transfers. |
| 0.3.3.4    | Updated for bug fix in A5 system status message decode. Experimental release from 0.3.3.1 kept in, feedback would be appreciated. |
| 0.3.3.3    | Updated Powerlink operation: removed I'm Alive message and now sending periodic Restore. Experimental release from 0.3.3.1 kept in. Feedback on both of these would be appreciated. |
| 0.3.3.2    | A minor feature added to stop doing powerlink attempts and to allow sending on panel timeouts. Experimental release from 0.3.3.1 kept in. Feedback on both of these would be appreciated. |
| 0.3.3.1    | Experimental release to attempt to decode the A7 FF message for PowerMaster Series alarms. It still also includes the B0 experimental decode for sensors too. Feedback on both of these would be appreciated. |
| 0.3.3      | Added a config parameter to almost always display the numeric keypad, including when the User code has been obtained from the EEPROM. Note that the B0 Experimental function is still in there too. |
| 0.3.2.2    | Experimental release looking at the B0 PowerMaster messages, included some code to detect motion from the "B0 03 04" data. |
| 0.3.2.1    | Experimental release looking at the B0 PowerMaster messages and whether we can use them to determine PIR motion without the alarm being armed. |
| 0.3.2      | Reworked HA service to bypass/rearm individual sensors in the panel. There is no Frontend for this, just a service for you to call. |
| 0.3.1      | Added HA service to bypass/rearm individual sensors in the panel. There is no Frontend for this, just a service for you to call. |
| 0.3.0      | New Control Flow working for PowerMaster 10 and 30 and achieves powerlink much quicker. Also, courtesy of olijouve, a French language translation. Tidied up log entries. Trigger restore if expected not received in 10 seconds. |
| 0.2.9      | Fixed a bug from when there are 20 or more of the same message, I caused a reset of the Component. I should only do this for A5 messages. |
| 0.2.8      | Lots and lots of debug for Adding Sensors and X10. Removed debug logs for EPROM Download. |
| 0.2.7      | Changes Download EPROM Technique, its much faster. Lots and lots of debug for Adding Sensors and EPROM download (but does not show pin codes) |
| 0.2.6      | Some PowerMaster fixes for Downloading EPROM. Lots and lots of debug for EPROM download (but does not show pin codes) |
| 0.2.5      | Process access denied messages from panel better. Lots and lots of debug for EPROM download (but does not show pin codes) |
| 0.2.4      | Added HA access to watchdog and download timout counters. Process access denied messages from panel better. |
| 0.2.3      | Minor updates for PowerMaster Panels, more debug logs for PowerMaster panels, no other changes. |
| 0.2.2      | Minor updates for PowerMaster Panels, no other changes. |
| 0.2.1      | X10 Devices available in Standard Plus and Powerlink. Added "zone tripped" attribute to the sensors. Assume siren is not active when panel disarmed. |
| 0.2.0      | A change of control flow for startup and the addition of the "Standard Plus" mode. Also added the ability to set the Download Code from the configuration file |
| 0.1.5      | A few debug and bug fix additions to hopefully help work out control flow of some of the wider communities panels |
| 0.1.4      | Quick fix to Powerlink, would sometimes take 10 to 15 minutes so put some code back to how it was!! I have also added "exclude" lists for sensors and x10 devices, added additional config parameters, see below |
| 0.1.3      | Quick fix to exit Download mode quicker when trying Powerlink |
| 0.1.2      | Assume Powermaster as default panel and allow CRC to be 1 less than calculated value (I don't know why). Update operation when panel connection is lost. Move when Time is Synchronised to be more accurate (Powermax only). Update A7 decode as Powermaster 10 series sends unknown A7 data. Update powerlink timing to hopefully improve reliability. |
| 0.1.1      | Not officially released |
| 0.1.0      | *** Breaking change ***  I have converted the Component to the new HA Component file structure. This Component has a different file structure, please delete all previous files. |
| 0.0.8.5    | Bug fix for the number format to be displayed on standard mode |
| 0.0.8.4    | Bug fix to the Bug fix for X10 Devices when in Standard Mode |
| 0.0.8.3    | Bug fixes for X10 Devices when in Standard Mode |
| 0.0.8.2    | Added Powermaster Sensor MC-302V PG2 and additional zone status decoding |
| 0.0.8.1    | Updated for bugfix to A7 data decode using Powermaster 10 |
| 0.0.8.0    | *** Breaking change ***  X10 devices added, they should be created as a switch. I've removed the old switch entity from previous versions and merged its attributes in to "alarm_control_panel.visonic_alarm". Within a sensor, zone tamper and device tamper are now different. Tamper no longer triggers the alarm sounding in HA, only the siren sounding does this now. |
| 0.0.7.2    | HA 0.86 made a breaking change, alarm control panel entities must return "number" and not "Number". |
| 0.0.7.1    | Updated device attributes for battery level, tripped state, armed state (bypassed or not) and last tripped time to all conform with similar settings within HA itself. |
| 0.0.7.0    | *** Breaking change *** Conversion of all sensors from a sensor Entity to a binary_sensor Entity type. This means that a sensor can only have 2 states, off or on. The interpretation in the frontend depends on what is provided by device_class. I have done what I can to get the device class correct but you can change this in your customize configuration section, see below. Remember that the state is now off or on and not "-", "T" or "O". |
| 0.0.6.6    | Bug fix to sensor device_class, part of HA Entity update. |
| 0.0.6.5    | Sensors are based on the HA Entity. Moved "from serial_asyncio import create_serial_connection" to create_usb_visonic_connection so only used for USB connections. |
| 0.0.6.4    | Removed push change in A3 message as causing exception in HA. |
| 0.0.6.3    | Fix bug in SetConfig, prevent "None" values being accepted. |
| 0.0.6.2    | Added code to indicate when alarm is triggered (and sounding). This works best in powerlink mode. In Standard mode I make a guess that when the panel is armed and a device is triggered then the alarm must be sounding. |
| 0.0.6      | Extracts much more info from EPROM in powerlink. Added more Dutch translations. Handle Access Denied (when entering wrong pin code). A few bug fixes. |
| 0.0.5      | Updated the state return values for the generic alarm panel for entry delay (as pending). |
| 0.0.4      | Added arm_without_usercode. Include phone number decode with an exception handler just in case. |
| 0.0.3      | Removed some test code that would prevent arming. Commented out phone number decoding as creating exceptions. |
| 0.0.2      | Made some bug fixes |