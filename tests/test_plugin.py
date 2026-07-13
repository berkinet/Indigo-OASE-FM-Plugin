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

    def test_failed_initial_connection_is_closed(self):
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        controller = Mock()
        controller.connect.side_effect = TimeoutError("timed out")
        original = plugin_module.OaseController
        plugin_module.OaseController = Mock(return_value=controller)
        try:
            with self.assertRaises(TimeoutError):
                self.plugin._get_controller()
        finally:
            plugin_module.OaseController = original

        controller.close.assert_called_once_with()
        self.assertIsNone(self.plugin._controller)

    def test_controller_call_retries_one_timeout(self):
        controller = Mock()
        self.plugin._get_controller = Mock(
            side_effect=[TimeoutError("timed out"), controller]
        )
        original_sleep = plugin_module.time.sleep
        plugin_module.time.sleep = Mock()
        try:
            result = plugin_module.Plugin._controller_call(
                self.plugin,
                lambda active: active,
            )
        finally:
            plugin_module.time.sleep = original_sleep

        self.assertIs(result, controller)
        self.assertEqual(self.plugin._get_controller.call_count, 2)

    def test_controller_call_does_not_retry_non_timeout(self):
        self.plugin._get_controller = Mock(
            side_effect=plugin_module.OaseError("authentication failed")
        )

        with self.assertRaises(plugin_module.OaseError):
            plugin_module.Plugin._controller_call(
                self.plugin,
                lambda active: active,
            )

        self.plugin._get_controller.assert_called_once_with()

    def test_refresh_publishes_egc_rpm_and_watts(self):
        egc_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_EGC,
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: [egc_device]
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        self.controller.get_state.return_value = SimpleNamespace(
            outlet1=False,
            outlet2=False,
            outlet3=False,
            outlet4=True,
            dimmer4=128,
        )
        self.controller.get_single_egc_device.return_value = SimpleNamespace(
            uid=b"device"
        )
        self.controller.get_egc_state.return_value = SimpleNamespace(
            on=True,
            power=50,
            rpm=2345,
            watts=78,
        )
        try:
            refreshed = self.plugin._refresh_all()
        finally:
            plugin_module.indigo.devices.iter = original_iter

        self.assertTrue(refreshed)
        egc_device.updateStatesOnServer.assert_called_once_with(
            [
                {"key": "brightnessLevel", "value": 50},
                {"key": "onOffState", "value": True},
                {"key": "rpm", "value": 2345, "uiValue": "2345 RPM"},
                {"key": "watts", "value": 78, "uiValue": "78 W"},
            ]
        )


if __name__ == "__main__":
    unittest.main()
