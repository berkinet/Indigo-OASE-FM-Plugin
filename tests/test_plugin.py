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

    def tearDown(self):
        self.plugin._remove_protocol_logging()

    def test_protocol_debugging_is_forwarded_to_indigo_log(self):
        self.plugin.closedPrefsConfigUi({"logLevel": "debug"}, False)

        with self.assertLogs("test", level="INFO") as captured:
            logging.getLogger("oase").debug("TLS receive: AABBCC")

        self.assertIn(
            "Protocol DEBUG: TLS receive: AABBCC",
            "\n".join(captured.output),
        )

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
        self.assertEqual(
            self.plugin._assignment(plugin_module.DEVICE_CONTROLLER, {}),
            ("controller", 0),
        )

    def test_device_config_populates_static_addresses(self):
        self.plugin.pluginPrefs = {"deviceIp": "192.0.2.10"}
        cases = (
            (plugin_module.DEVICE_SWITCHED, {"socketNumber": "4"}, "Socket 4"),
            (plugin_module.DEVICE_DIMMER, {}, "Socket 3"),
            (plugin_module.DEVICE_EGC, {}, "EGC"),
            (plugin_module.DEVICE_CONTROLLER, {}, "192.0.2.10"),
        )

        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: []
        try:
            for type_id, values, expected in cases:
                valid, updated = self.plugin.validateDeviceConfigUi(
                    values, type_id, 0
                )
                self.assertTrue(valid)
                self.assertEqual(updated["address"], expected)
        finally:
            plugin_module.indigo.devices.iter = original_iter

    def test_existing_device_address_is_replaced_only_when_needed(self):
        device = SimpleNamespace(
            address="Old",
            pluginProps={"address": "Old", "socketNumber": "1"},
            replacePluginPropsOnServer=Mock(),
        )

        self.plugin._update_address(device, "Socket 1")

        device.replacePluginPropsOnServer.assert_called_once_with(
            {"address": "Socket 1", "socketNumber": "1"}
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

    def test_timeout_retry_is_visible_only_with_protocol_debugging(self):
        controller = Mock()
        self.plugin._get_controller = Mock(
            side_effect=[TimeoutError("timed out"), controller]
        )
        original_sleep = plugin_module.time.sleep
        plugin_module.time.sleep = Mock()
        try:
            with self.assertNoLogs("test", level="INFO"):
                plugin_module.Plugin._controller_call(
                    self.plugin,
                    lambda active: active,
                )

            protocol_debug = Mock()
            self.plugin._protocol_logger.debug = protocol_debug
            self.plugin._get_controller = Mock(
                side_effect=[TimeoutError("timed out"), controller]
            )
            plugin_module.Plugin._controller_call(
                self.plugin,
                lambda active: active,
            )
        finally:
            plugin_module.time.sleep = original_sleep

        protocol_debug.assert_called_once_with(
            "OASE connection timed out; retrying once"
        )

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
            address="EGC",
            pluginProps={"address": "EGC"},
            replacePluginPropsOnServer=Mock(),
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
        egc_identity = SimpleNamespace(
            uid=b"device",
            uid_text="4F41:000001C8",
            manufacturer_identifier=0x4F41,
            device_identifier=456,
            article_number=123,
            subdevice_count=1,
        )
        self.controller.get_single_egc_device.return_value = egc_identity
        self.controller.get_egc_state.return_value = SimpleNamespace(
            device=egc_identity,
            on=True,
            power=50,
            rpm=2345,
            watts=78,
            module_temperature=24,
            pcb_temperature=31,
            water_temperature=18,
            sfc_enabled=False,
            sfc_mode="Maximum",
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
                {
                    "key": "moduleTemperature",
                    "value": 24,
                    "uiValue": "24 °C",
                },
                {
                    "key": "pcbTemperature",
                    "value": 31,
                    "uiValue": "31 °C",
                },
                {
                    "key": "waterTemperature",
                    "value": 18,
                    "uiValue": "18 °C",
                },
                {"key": "sfcEnabled", "value": False},
                {"key": "sfcMode", "value": "Maximum"},
                {"key": "manufacturerIdentifier", "value": 0x4F41},
                {"key": "deviceIdentifier", "value": 456},
                {"key": "uid", "value": "4F41:000001C8"},
                {"key": "articleNumber", "value": 123},
                {"key": "subdeviceCount", "value": 1},
            ]
        )
        egc_device.replacePluginPropsOnServer.assert_called_once_with(
            {"address": "EGC 4F41:000001C8"}
        )

    def test_refresh_publishes_controller_rssi_and_quality(self):
        controller_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_CONTROLLER,
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: [controller_device]
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        self.controller.get_state.return_value = SimpleNamespace(
            outlet1=False,
            outlet2=False,
            outlet3=False,
            outlet4=False,
            dimmer4=0,
        )
        self.controller.get_controller_state.return_value = SimpleNamespace(rssi=-57)
        self.plugin._discovery = SimpleNamespace(
            hardware_type=4,
            device_index=0,
            name="CM Oase",
            serial_number="217200019384",
            long_name="FM-Master EGC Home",
            order_number=12345,
            firmware=2,
            firmware_low=7,
            firmware_high=3,
            wifi_channel=6,
            network_type=1,
            status="Ready",
        )
        try:
            refreshed = self.plugin._refresh_all()
        finally:
            plugin_module.indigo.devices.iter = original_iter

        self.assertTrue(refreshed)
        controller_device.updateStatesOnServer.assert_called_once_with(
            [
                {"key": "onOffState", "value": True},
                {"key": "rssi", "value": -57, "uiValue": "-57 dBm"},
                {"key": "signalQuality", "value": "Strong"},
                {"key": "connected", "value": True},
                {"key": "authenticated", "value": True},
                {"key": "hardwareType", "value": 4},
                {"key": "deviceIndex", "value": 0},
                {"key": "controllerName", "value": "CM Oase"},
                {"key": "serialNumber", "value": "217200019384"},
                {"key": "modelName", "value": "FM-Master EGC Home"},
                {"key": "articleNumber", "value": 12345},
                {"key": "release", "value": 2},
                {"key": "firmwareVersion", "value": "3.7"},
                {"key": "wifiChannel", "value": 6},
                {"key": "networkType", "value": 1},
                {"key": "statusText", "value": "Ready"},
            ]
        )
        controller_device.setErrorStateOnServer.assert_called_once_with(None)

    def test_rssi_failure_does_not_block_egc_refresh(self):
        controller_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_CONTROLLER,
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        egc_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_EGC,
            address="EGC 4F41:000001C8",
            pluginProps={"address": "EGC 4F41:000001C8"},
            replacePluginPropsOnServer=Mock(),
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: [
            controller_device,
            egc_device,
        ]
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        self.controller.get_state.return_value = SimpleNamespace(
            outlet1=False,
            outlet2=False,
            outlet3=False,
            outlet4=False,
            dimmer4=0,
        )
        self.controller.get_controller_state.side_effect = plugin_module.OaseError(
            "RSSI unavailable"
        )
        egc_identity = SimpleNamespace(
            uid=b"device",
            uid_text="4F41:000001C8",
            manufacturer_identifier=0x4F41,
            device_identifier=456,
            article_number=123,
            subdevice_count=1,
        )
        self.controller.get_single_egc_device.return_value = egc_identity
        self.controller.get_egc_state.return_value = SimpleNamespace(
            device=egc_identity,
            on=True,
            power=50,
            rpm=2345,
            watts=78,
        )
        try:
            with self.assertLogs("test", level="WARNING"):
                refreshed = self.plugin._refresh_all()
        finally:
            plugin_module.indigo.devices.iter = original_iter

        self.assertFalse(refreshed)
        egc_device.updateStatesOnServer.assert_called_once()
        controller_device.updateStatesOnServer.assert_called_once_with(
            [
                {"key": "onOffState", "value": False},
                {"key": "connected", "value": False},
                {"key": "authenticated", "value": False},
            ]
        )
        controller_device.setErrorStateOnServer.assert_called_once_with(
            "RSSI unavailable"
        )

    def test_controller_outage_sets_native_sensor_state_off(self):
        controller_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_CONTROLLER,
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: [controller_device]
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        self.controller.get_state.side_effect = plugin_module.OaseError("offline")
        try:
            with self.assertLogs("test", level="WARNING"):
                refreshed = self.plugin._refresh_all()
        finally:
            plugin_module.indigo.devices.iter = original_iter

        self.assertFalse(refreshed)
        controller_device.updateStatesOnServer.assert_called_once_with(
            [
                {"key": "onOffState", "value": False},
                {"key": "connected", "value": False},
                {"key": "authenticated", "value": False},
            ]
        )

    def test_repeated_refresh_failures_log_once_and_recovery_logs_once(self):
        socket_device = SimpleNamespace(
            enabled=True,
            deviceTypeId=plugin_module.DEVICE_SWITCHED,
            pluginProps={"socketNumber": "1"},
            updateStatesOnServer=Mock(),
            setErrorStateOnServer=Mock(),
        )
        original_iter = plugin_module.indigo.devices.iter
        plugin_module.indigo.devices.iter = lambda _plugin_id: [socket_device]
        self.plugin.pluginPrefs = {
            "deviceIp": "192.0.2.1",
            "localIp": "192.0.2.2",
            "password": "pw",
        }
        self.controller.get_state.side_effect = plugin_module.OaseError("timed out")
        try:
            with self.assertLogs("test", level="WARNING") as failures:
                self.assertFalse(self.plugin._refresh_all())
                self.assertFalse(self.plugin._refresh_all())

            self.controller.get_state.side_effect = None
            self.controller.get_state.return_value = SimpleNamespace(
                outlet1=True,
                outlet2=False,
                outlet3=False,
                outlet4=False,
                dimmer4=0,
            )
            with self.assertLogs("test", level="INFO") as recovery:
                self.assertTrue(self.plugin._refresh_all())
        finally:
            plugin_module.indigo.devices.iter = original_iter

        failure_messages = [
            message
            for message in failures.output
            if "Unable to refresh FM-Master status" in message
        ]
        self.assertEqual(len(failure_messages), 1)
        self.assertIn(
            "OASE controller connection restored",
            "\n".join(recovery.output),
        )
        self.assertEqual(socket_device.setErrorStateOnServer.call_count, 3)


if __name__ == "__main__":
    unittest.main()
