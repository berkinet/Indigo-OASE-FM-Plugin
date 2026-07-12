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
        self.assertEqual(info["PluginVersion"], "0.1.1")
        self.assertEqual(
            info["CFBundleIdentifier"],
            "com.berkinet.indigoplugin.oase-fm",
        )

    def test_three_native_device_types(self):
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
            },
        )

    def test_switched_socket_choices_are_physical_1_2_4(self):
        devices = ET.parse(SERVER / "Devices.xml").getroot()
        switched = devices.find("Device[@id='switchedSocket']")
        options = switched.findall("./ConfigUI/Field/List/Option")
        self.assertEqual([option.attrib["value"] for option in options], ["1", "2", "4"])

    def test_plugin_config_contains_required_connection_fields(self):
        config = ET.parse(SERVER / "PluginConfig.xml").getroot()
        fields = {field.attrib["id"]: field.attrib for field in config.findall("Field")}
        self.assertIn("deviceIp", fields)
        self.assertIn("localIp", fields)
        self.assertEqual(fields["password"]["secure"], "true")


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


if __name__ == "__main__":
    unittest.main()
