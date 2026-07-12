import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "OASE FM.indigoPlugin" / "Contents" / "Server Plugin"
sys.path.insert(0, str(SERVER))


class PluginBase:
    class StopThread(Exception):
        pass

    def __init__(self, plugin_id, display_name, version, prefs):
        self.pluginId = plugin_id
        self.pluginPrefs = prefs
        self.logger = logging.getLogger("test")

    def deviceStartComm(self, dev):
        pass

    def deviceStopComm(self, dev):
        pass


indigo = ModuleType("indigo")
indigo.PluginBase = PluginBase
indigo.Dict = dict
indigo.devices = SimpleNamespace(iter=lambda plugin_id: [])
indigo.kDeviceAction = SimpleNamespace(
    TurnOn=1,
    TurnOff=2,
    Toggle=3,
    SetBrightness=4,
    BrightenBy=5,
    DimBy=6,
)
indigo.kUniversalAction = SimpleNamespace(RequestStatus=1)
sys.modules["indigo"] = indigo

oase_fm = ModuleType("oase_fm")
oase_fm.OaseController = Mock
oase_fm.OaseError = type("OaseError", (RuntimeError,), {})
sys.modules["oase_fm"] = oase_fm

spec = importlib.util.spec_from_file_location("indigo_oase_plugin", SERVER / "plugin.py")
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)


class PluginLogicTests(unittest.TestCase):
    def setUp(self):
        self.plugin = plugin_module.Plugin("plugin.id", "OASE FM", "0.1.0", {})
        self.controller = Mock()
        self.plugin._controller_call = lambda callback: callback(self.controller)

    def test_physical_socket_four_uses_third_protocol_outlet(self):
        device = SimpleNamespace(
            deviceTypeId=plugin_module.DEVICE_SWITCHED,
            pluginProps={"socketNumber": "4"},
        )

        self.plugin._set_on_off(device, True)

        self.controller.set_outlet.assert_called_once_with(3, True)

    def test_dimmer_is_protocol_outlet_four(self):
        device = SimpleNamespace(deviceTypeId=plugin_module.DEVICE_DIMMER)

        self.plugin._set_on_off(device, False)

        self.controller.set_outlet.assert_called_once_with(4, False)

    def test_assignments_prevent_duplicate_physical_devices(self):
        self.assertEqual(
            self.plugin._assignment(
                plugin_module.DEVICE_SWITCHED,
                {"socketNumber": "4"},
            ),
            ("socket", 4),
        )
        self.assertEqual(
            self.plugin._assignment(plugin_module.DEVICE_DIMMER, {}),
            ("socket", 3),
        )
        self.assertEqual(
            self.plugin._assignment(plugin_module.DEVICE_EGC, {}),
            ("egc", 0),
        )


if __name__ == "__main__":
    unittest.main()
