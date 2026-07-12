# OASE FM Plugin for Indigo

An Indigo plugin for local control of the OASE InScenio FM-Master EGC and
attached EGC devices.

This repository will build on the reusable
[`oase-fm`](https://github.com/berkinet/oase-fm) Python protocol and controller
implementation. The initial plugin structure and Indigo integration are the
next development step.

## Planned capabilities

- Discover and connect to an FM-Master EGC on the local network
- Represent FM-Master outlets and the dimmer as Indigo devices
- Represent an attached EGC device and its power level
- Read current state and apply changes through the shared `oase_fm` module
- Keep controller passwords in Indigo configuration rather than source code

## Status

Repository initialized. Plugin implementation has not started yet.

