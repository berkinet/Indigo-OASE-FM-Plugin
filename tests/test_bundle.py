import hashlib
import importlib.util
import plistlib
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "OASE FM.indigoPlugin" / "Contents"
SERVER = BUNDLE / "Server Plugin"

spec = importlib.util.spec_from_file_location(
    "oase_plugin", SERVER / "oase_plugin.py"
)
oase_plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = oase_plugin
spec.loader.exec_module(oase_plugin)


class BundleTests(unittest.TestCase):
    def test_info_targets_current_indigo_api(self):
        with (BUNDLE / "Info.plist").open("rb") as stream:
            info = plistlib.load(stream)

        self.assertEqual(info["ServerApiVersion"], "3.8")
        self.assertEqual(info["PluginVersion"], "0.4.0")
        self.assertEqual(
            info["CFBundleIdentifier"],
            "com.berkinet.indigoplugin.oase-fm",
        )

    def test_protocol_library_is_bundled_without_runtime_downloads(self):
        library = SERVER / "oase_fm.py"

        self.assertTrue(library.is_file())
        self.assertFalse((SERVER / "requirements.txt").exists())
        self.assertEqual(
            hashlib.sha256(library.read_bytes()).hexdigest(),
            "8feb6b635de0d35199b0d82b549aa7f3d747dd2e1c1c87e58968786af06181d2",
        )

    def test_four_native_device_types(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()
        found = {
            element.attrib["id"]: element.attrib["type"]
            for element in devices.findall("Device")
        }
        self.assertEqual(
            found,
            {
                "switchedSocket": "relay",
                "dimmableSocket": "dimmer",
                "egcDevice": "dimmer",
                "controllerDevice": "sensor",
            },
        )

    def test_switched_socket_choices_are_physical_1_2_4(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()
        switched = devices.find("Device[@id='switchedSocket']")
        options = switched.findall("./ConfigUI/Field/List/Option")
        self.assertEqual([option.attrib["value"] for option in options], ["1", "2", "4"])

    def test_every_device_type_declares_read_only_address_field(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()

        for device in devices.findall("Device"):
            address = device.find("./ConfigUI/Field[@id='address']")
            self.assertIsNotNone(address, device.attrib["id"])
            self.assertEqual(address.attrib["readonly"], "true")

    def test_egc_device_exposes_telemetry_and_identity_states(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()
        egc = devices.find("Device[@id='egcDevice']")
        states = {state.attrib["id"] for state in egc.findall("./States/State")}

        self.assertEqual(
            states,
            {
                "rpm",
                "watts",
                "moduleTemperature",
                "pcbTemperature",
                "waterTemperature",
                "manufacturerIdentifier",
                "deviceIdentifier",
                "uid",
                "articleNumber",
                "subdeviceCount",
            },
        )

    def test_controller_device_exposes_rssi_states(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()
        controller = devices.find("Device[@id='controllerDevice']")
        states = {
            state.attrib["id"] for state in controller.findall("./States/State")
        }

        self.assertEqual(
            states,
            {
                "connected",
                "authenticated",
                "rssi",
                "signalQuality",
                "hardwareType",
                "deviceIndex",
                "controllerName",
                "serialNumber",
                "modelName",
                "articleNumber",
                "firmware",
                "firmwareLow",
                "firmwareHigh",
                "wifiChannel",
                "networkType",
                "statusText",
            },
        )
        self.assertIsNone(controller.find("UiDisplayStateId"))

    def test_plugin_config_contains_required_connection_fields(self):
        config = ET.parse(SERVER / "PluginConfig.xml").getroot()
        fields = {field.attrib["id"]: field.attrib for field in config.findall("Field")}
        self.assertIn("deviceIp", fields)
        self.assertIn("localIp", fields)
        self.assertEqual(fields["password"]["secure"], "true")
        self.assertEqual(fields["logLevel"]["defaultValue"], "info")
        log_options = config.findall("./Field[@id='logLevel']/List/Option")
        self.assertEqual(
            [option.attrib["value"] for option in log_options],
            ["info", "debug"],
        )


class MappingTests(unittest.TestCase):
    def test_physical_socket_mapping(self):
        state = SimpleNamespace(
            outlet1=True,
            outlet2=False,
            outlet3=True,
            outlet4=False,
            dimmer4=128,
        )

        mapped = oase_plugin.map_fm_state(state)

        self.assertEqual(mapped.switched, {1: True, 2: False, 4: True})
        self.assertFalse(mapped.dimmer_on)
        self.assertEqual(mapped.dimmer_brightness, 64)

    def test_dimmer_percentage_encoding(self):
        self.assertEqual(oase_plugin.dimmer_percent_to_raw(0), 0)
        self.assertEqual(oase_plugin.dimmer_percent_to_raw(50), 100)
        self.assertEqual(oase_plugin.dimmer_percent_to_raw(100), 255)
        self.assertEqual(oase_plugin.dimmer_raw_to_percent(255), 100)

    def test_egc_percentage_encoding(self):
        self.assertEqual(oase_plugin.egc_percent_to_raw(0), 0)
        self.assertEqual(oase_plugin.egc_percent_to_raw(50), 127)
        self.assertEqual(oase_plugin.egc_percent_to_raw(100), 255)

    def test_rssi_quality_uses_oase_thresholds(self):
        self.assertEqual(oase_plugin.rssi_quality(-81), "Weak")
        self.assertEqual(oase_plugin.rssi_quality(-80), "Weak")
        self.assertEqual(oase_plugin.rssi_quality(-79), "Fair")
        self.assertEqual(oase_plugin.rssi_quality(-68), "Fair")
        self.assertEqual(oase_plugin.rssi_quality(-67), "Good")
        self.assertEqual(oase_plugin.rssi_quality(-60), "Good")
        self.assertEqual(oase_plugin.rssi_quality(-59), "Strong")

    def test_dimmer_on_off_state_is_applied_after_brightness(self):
        self.assertEqual(
            oase_plugin.dimmer_state_updates(False, 64),
            [
                {"key": "brightnessLevel", "value": 64},
                {"key": "onOffState", "value": False},
            ],
        )


if __name__ == "__main__":
    unittest.main()
