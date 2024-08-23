[![](https://img.shields.io/github/release/davesmeghead/visonic/all.svg?style=for-the-badge)](https://github.com/davesmeghead/visonic/releases) 
[![](https://img.shields.io/badge/MAINTAINER-%40Davesmeghead-green?style=for-the-badge)](https://github.com/Davesmeghead)
[![Buy me a coffee][buymeacoffee-shield]][buymeacoffee]

[buymeacoffee]: https://www.buymeacoffee.com/davesmeghead
[buymeacoffee-shield]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png

![installation_badge](https://img.shields.io/badge/dynamic/json?style=for-the-badge?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.visonic.total)
[![Validated with HACS](https://github.com/davesmeghead/visonic/actions/workflows/validate.yaml/badge.svg)](https://github.com/davesmeghead/visonic/actions/workflows/validate.yaml)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs)

# Visonic Alarm Panel for Home Assistant

This is a *custom integration* for [Home Assistant](https://www.home-assistant.io/).

## Introduction
This is a custom integration to allow the control of a Visonic alarm system. Visonic produce a series of Alarm Systems and this integration is compatible with the following models:
- PowerMax+ (plus), PowerMax Express, PowerMax Pro , PowerMax Pro Plus, PowerMax Complete and PowerMax Complete Plus
- PowerMaster-10, PowerMaster-30 and PowerMaster-33
- PowerMaster-360 and PowerMaster-360R in a limited way

This Home Assistant Integration allows you to:
- Control the alarm panel to arm and disarm,
- Bypass/Arm individual sensors,
- Use the various sensors as devices and entities,
- Use the X10 devices (on supported panel types).
- On PowerMaster panels, trigger the Siren, Panic Alarm and Fire Emergency

Remember to check out the Wiki-section, this contains all the documentation [Wiki Home](https://github.com/davesmeghead/visonic/wiki)

_If you have notes related to a specific solution where this component is used, you're mostly welcome to provide both details and full guides to the Wiki-section!_

## Configuration
The only way to setup this integration is by using the Integration page within Home Assistant. This integration does not support auto discovery.
This integration is supported by HACS, first install HACS and then find this integration in the HACS list.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=visonic)
