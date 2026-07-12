"""Indigo plugin for the OASE InScenio FM-Master EGC."""

from __future__ import annotations

import ipaddress
import threading

import indigo
from oase_fm import OaseController, OaseError

from oase_plugin import (
    SWITCHED_SOCKET_TO_PROTOCOL_OUTLET,
    dimmer_percent_to_raw,
    egc_percent_to_raw,
    map_fm_state,
)


DEVICE_SWITCHED = "switchedSocket"
DEVICE_DIMMER = "dimmableSocket"
DEVICE_EGC = "egcDevice"


class Plugin(indigo.PluginBase):
    def __init__(
        self,
        plugin_id,
        plugin_display_name,
        plugin_version,
        plugin_prefs,
    ):
        super().__init__(
            plugin_id,
            plugin_display_name,
            plugin_version,
            plugin_prefs,
        )
        self._lock = threading.RLock()
        self._controller = None
        self._egc_device = None

    def startup(self):
        self.logger.info("OASE FM plugin started")

    def shutdown(self):
        self._disconnect()
        self.logger.info("OASE FM plugin stopped")

    def runConcurrentThread(self):
        try:
            while True:
                self._refresh_all()
                self.sleep(self._poll_interval())
        except self.StopThread:
            pass
        finally:
            self._disconnect()

    def deviceStartComm(self, dev):
        super().deviceStartComm(dev)
        self._refresh_all()

    def deviceStopComm(self, dev):
        super().deviceStopComm(dev)

    def validatePrefsConfigUi(self, values_dict):
        errors = indigo.Dict()
        for field, label in (
            ("deviceIp", "OASE IP Address"),
            ("localIp", "Local IP Address"),
        ):
            value = str(values_dict.get(field, "")).strip()
            try:
                ipaddress.ip_address(value)
            except ValueError:
                errors[field] = f"{label} must be a valid IP address"
            values_dict[field] = value

        if not str(values_dict.get("password", "")):
            errors["password"] = "OASE Password is required"

        try:
            interval = int(str(values_dict.get("pollInterval", "10")))
            if interval not in range(5, 3601):
                raise ValueError
            values_dict["pollInterval"] = str(interval)
        except ValueError:
            errors["pollInterval"] = "Polling interval must be 5-3600 seconds"

        if errors:
            errors["showAlertText"] = "Please correct the highlighted settings."
            return False, values_dict, errors
        return True, values_dict

    def closedPrefsConfigUi(self, values_dict, user_cancelled):
        if not user_cancelled:
            self._disconnect()

    def validateDeviceConfigUi(self, values_dict, type_id, dev_id):
        errors = indigo.Dict()
        assignment = self._assignment(type_id, values_dict)
        if assignment is None:
            errors["socketNumber"] = "Select socket 1, 2, or 4"
        else:
            for existing in indigo.devices.iter("self"):
                if existing.id == dev_id:
                    continue
                if self._assignment(existing.deviceTypeId, existing.pluginProps) == assignment:
                    errors["showAlertText"] = (
                        f"{existing.name!r} already uses this OASE device assignment."
                    )
                    break

        if errors:
            return False, values_dict, errors
        return True, values_dict

    def actionControlDevice(self, action, dev):
        try:
            if action.deviceAction == indigo.kDeviceAction.TurnOn:
                self._set_on_off(dev, True)
            elif action.deviceAction == indigo.kDeviceAction.TurnOff:
                self._set_on_off(dev, False)
            elif action.deviceAction == indigo.kDeviceAction.Toggle:
                self._set_on_off(dev, not dev.onState)
            elif action.deviceAction == indigo.kDeviceAction.SetBrightness:
                self._set_brightness(dev, int(action.actionValue))
            elif action.deviceAction == indigo.kDeviceAction.BrightenBy:
                self._set_brightness(
                    dev,
                    min(100, int(dev.brightness) + int(action.actionValue)),
                )
            elif action.deviceAction == indigo.kDeviceAction.DimBy:
                self._set_brightness(
                    dev,
                    max(0, int(dev.brightness) - int(action.actionValue)),
                )
            else:
                self.logger.warning("Unsupported action for %s", dev.name)
                return
            self._refresh_all(raise_errors=True)
        except (OaseError, OSError, ValueError) as exc:
            self.logger.error("OASE command for %s failed: %s", dev.name, exc)
            dev.setErrorStateOnServer(str(exc))

    def actionControlUniversal(self, action, dev):
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self._refresh_all(raise_errors=False)

    def _poll_interval(self):
        try:
            return max(5, min(3600, int(self.pluginPrefs.get("pollInterval", 10))))
        except (TypeError, ValueError):
            return 10

    def _configuration(self):
        return (
            str(self.pluginPrefs.get("deviceIp", "")).strip(),
            str(self.pluginPrefs.get("localIp", "")).strip(),
            str(self.pluginPrefs.get("password", "")),
        )

    def _configured(self):
        device_ip, local_ip, password = self._configuration()
        if not device_ip or not local_ip or not password:
            return False
        try:
            ipaddress.ip_address(device_ip)
            ipaddress.ip_address(local_ip)
        except ValueError:
            return False
        return True

    def _get_controller(self):
        if self._controller is None:
            device_ip, local_ip, password = self._configuration()
            controller = OaseController(
                device_ip=device_ip,
                local_ip=local_ip,
                password=password,
            )
            try:
                controller.connect()
            except BaseException:
                # The shared module also cleans up failed connections. Keep
                # this guard so the plugin never leaks the TLS callback port
                # if initialization fails before the controller is retained.
                controller.close()
                raise
            self._controller = controller
            self._egc_device = None
        return self._controller

    def _disconnect(self):
        with self._lock:
            if self._controller is not None:
                self._controller.close()
            self._controller = None
            self._egc_device = None

    def _controller_call(self, callback):
        with self._lock:
            try:
                return callback(self._get_controller())
            except (OaseError, OSError):
                self._disconnect()
                raise

    def _get_egc_device(self, controller):
        if self._egc_device is None:
            self._egc_device = controller.get_single_egc_device()
        return self._egc_device

    def _set_on_off(self, dev, on):
        if dev.deviceTypeId == DEVICE_SWITCHED:
            physical = int(dev.pluginProps["socketNumber"])
            protocol_outlet = SWITCHED_SOCKET_TO_PROTOCOL_OUTLET[physical]
            self._controller_call(
                lambda controller: controller.set_outlet(protocol_outlet, on)
            )
        elif dev.deviceTypeId == DEVICE_DIMMER:
            self._controller_call(lambda controller: controller.set_outlet(4, on))
        elif dev.deviceTypeId == DEVICE_EGC:
            self._controller_call(
                lambda controller: controller.rdm_set(
                    self._get_egc_device(controller).uid,
                    0x1010,
                    bytes((0xFF if on else 0x00,)),
                )
            )
        else:
            raise ValueError(f"unknown device type {dev.deviceTypeId!r}")

    def _set_brightness(self, dev, brightness):
        brightness = max(0, min(100, int(brightness)))
        if dev.deviceTypeId == DEVICE_DIMMER:
            def set_dimmer(controller):
                controller.set_dimmer4(dimmer_percent_to_raw(brightness))
                controller.set_outlet(4, brightness > 0)

            self._controller_call(set_dimmer)
        elif dev.deviceTypeId == DEVICE_EGC:
            def set_egc(controller):
                egc = self._get_egc_device(controller)
                if brightness > 0:
                    controller.rdm_set(
                        egc.uid,
                        0x8039,
                        bytes((egc_percent_to_raw(brightness),)),
                    )
                controller.rdm_set(
                    egc.uid,
                    0x1010,
                    bytes((0xFF if brightness > 0 else 0x00,)),
                )

            self._controller_call(set_egc)
        else:
            raise ValueError("brightness is supported only for dimmable and EGC devices")

    def _refresh_all(self, raise_errors=False):
        if not self._configured():
            return False
        devices = list(indigo.devices.iter("self"))
        if not devices:
            return True
        try:
            fm_state = self._controller_call(
                lambda controller: map_fm_state(controller.get_state())
            )
            for dev in devices:
                if not dev.enabled:
                    continue
                if dev.deviceTypeId == DEVICE_SWITCHED:
                    physical = int(dev.pluginProps["socketNumber"])
                    updates = [
                        {"key": "onOffState", "value": fm_state.switched[physical]}
                    ]
                elif dev.deviceTypeId == DEVICE_DIMMER:
                    updates = [
                        {"key": "onOffState", "value": fm_state.dimmer_on},
                        {
                            "key": "brightnessLevel",
                            "value": fm_state.dimmer_brightness,
                        },
                    ]
                else:
                    continue
                dev.updateStatesOnServer(updates)
                dev.setErrorStateOnServer(None)
        except (OaseError, OSError, ValueError, KeyError) as exc:
            self.logger.warning("Unable to refresh FM-Master status: %s", exc)
            for dev in devices:
                if dev.enabled:
                    dev.setErrorStateOnServer(str(exc))
            if raise_errors:
                raise
            return False

        egc_devices = [
            dev for dev in devices if dev.enabled and dev.deviceTypeId == DEVICE_EGC
        ]
        if not egc_devices:
            return True
        try:
            egc_state = self._controller_call(
                lambda controller: controller.get_egc_state(
                    self._get_egc_device(controller)
                )
            )
            updates = [
                {"key": "onOffState", "value": egc_state.on},
                {"key": "brightnessLevel", "value": egc_state.power},
            ]
            for dev in egc_devices:
                dev.updateStatesOnServer(updates)
                dev.setErrorStateOnServer(None)
            return True
        except (OaseError, OSError, ValueError, KeyError) as exc:
            self.logger.warning("Unable to refresh EGC status: %s", exc)
            for dev in egc_devices:
                dev.setErrorStateOnServer(str(exc))
            if raise_errors:
                raise
            return False

    @staticmethod
    def _assignment(type_id, props):
        if type_id == DEVICE_SWITCHED:
            try:
                socket_number = int(props.get("socketNumber", ""))
            except (TypeError, ValueError):
                return None
            if socket_number not in SWITCHED_SOCKET_TO_PROTOCOL_OUTLET:
                return None
            return "socket", socket_number
        if type_id == DEVICE_DIMMER:
            return "socket", 3
        if type_id == DEVICE_EGC:
            return "egc", 0
        return None
