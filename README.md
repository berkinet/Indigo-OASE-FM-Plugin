# OASE FM Plugin for Indigo

An Indigo 2025.2 plugin for local control of the OASE InScenio FM-Master EGC
and one attached EGC device, including live RPM and wattage telemetry when the
EGC device exposes those standard RDM sensors.

The plugin bundles the reusable
[`oase-fm`](https://github.com/berkinet/oase-fm) Python module for the OASE
UDP/TLS protocol, outlet control, EGC discovery, and RDM communication.

## Requirements

- Indigo 2025.2 or newer
- Indigo Plugin API 3.8 / Python 3.13+
- An OASE FM-Master EGC reachable from the Indigo server
- The OASE app password

The `oase-fm` module is included in the plugin bundle. It uses the
`cryptography` package supplied with Indigo 2025.2, so installation and plugin
upgrades do not download Python modules from GitHub or PyPI.

## Installation

Download or clone this repository, then double-click `OASE FM.indigoPlugin`.
Indigo installs the complete bundle without a separate requirements step.

Configure the plugin with:

- **OASE IP Address** — address of the FM-Master controller
- **Local IP Address** — Indigo server address reachable by the controller for
  its TLS callback
- **OASE Password** — stored by Indigo and concealed in the configuration UI
- **Polling Interval** — complete status refresh interval, default 10 seconds
- **Logging** — leave at **Normal**, or select **Protocol debugging** temporarily
  to record the raw controller exchange in Indigo's Event Log. The password
  authentication payload is always redacted.

## Devices

Create one of three native Indigo device types:

| Plugin device type | Indigo type | Assignment |
| --- | --- | --- |
| Switched socket | Relay | Select physical socket 1, 2, or 4 |
| Dimmable socket | Dimmer | Physical socket 3 |
| EGC device | Dimmer | Single attached EGC, discovered automatically |

Duplicate physical assignments are rejected during device configuration.

The OASE protocol numbers its three ordinary channels before its dimmer
channel. The plugin translates those internal selectors to the FM-Master's
physical socket labels: physical sockets 1, 2, and 4 are switched, while
physical socket 3 is dimmable.

## Behavior

- Relay and dimmer actions are sent immediately to the FM-Master.
- A full status refresh follows each Indigo-issued state change.
- The polling thread requests complete status rather than querying an
  individual Indigo device.
- Polling reflects changes made by the OASE app or other OASE clients in the
  Indigo UI.
- FM-Master outlet updates remain available if a separate EGC query fails.
- EGC pump RPM and current power consumption are exposed as read-only Indigo
  states when reported by the device. OASE's `ActualSpeed` sensor supplies the
  live RPM value; `NominalSpeed` is not used for status.
- Connections are reused and automatically reset after communication errors.

## Protocol diagnostics

If EGC telemetry is missing or incorrect, open the plugin configuration, set
**Logging** to **Protocol debugging**, save, and request a device status update.
Relevant Event Log entries start with `Protocol DEBUG:`; sensor choices start
with `Using EGC`. Return Logging to **Normal** after collecting the diagnostic
entries.

## Development

Run the tests from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Validate the bundle metadata and XML on macOS:

```bash
plutil -lint "OASE FM.indigoPlugin/Contents/Info.plist"
xmllint --noout "OASE FM.indigoPlugin/Contents/Server Plugin/Devices.xml"
xmllint --noout "OASE FM.indigoPlugin/Contents/Server Plugin/PluginConfig.xml"
```

## Current limitations

- One FM-Master is configured per plugin instance.
- One attached EGC device is selected automatically. UID selection can be
  added later if multi-EGC installations need it.
- External state changes are detected by polling; unsolicited OASE broadcast
  support has not yet been identified.

## Acknowledgement

The underlying OASE protocol work acknowledges
[mr-suw/ioBroker.oasecontrol](https://github.com/mr-suw/ioBroker.oasecontrol),
whose MIT-licensed implementation supplied the practical foundation for the
FM-Master connection and socket-control path.
