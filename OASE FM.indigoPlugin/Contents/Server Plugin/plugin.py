"""Indigo plugin for the OASE InScenio FM-Master EGC."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time

import indigo
from oase_fm import OaseController, OaseError

from oase_plugin import (
    SWITCHED_SOCKET_TO_PROTOCOL_OUTLET,
    dimmer_state_updates,
    dimmer_percent_to_raw,
    egc_percent_to_raw,
    map_fm_state,
    rssi_quality,
)


DEVICE_SWITCHED = "switchedSocket"
DEVICE_DIMMER = "dimmableSocket"
DEVICE_EGC = "egcDevice"
DEVICE_CONTROLLER = "controllerDevice"


class IndigoProtocolLogHandler(logging.Handler):
    """Forward oase-fm records to Indigo's Event Log."""

    def __init__(self, indigo_logger):
        super().__init__()
        self._indigo_logger = indigo_logger

    def emit(self, record):
        try:
            # Indigo commonly filters DEBUG records at its own logger. When
            # protocol debugging is selected, surface them as clearly marked
            # informational Event Log entries instead.
            level = max(record.levelno, logging.INFO)
            prefix = "Protocol DEBUG: " if record.levelno == logging.DEBUG else ""
            self._indigo_logger.log(level, "%s%s", prefix, record.getMessage())
        except Exception:
            self.handleError(record)


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
        self._discovery = None
        self._egc_device = None
        self._failed_refreshes = set()
        self._protocol_logger = logging.getLogger("oase")
        self._protocol_log_handler = IndigoProtocolLogHandler(self.logger)
        self._protocol_logger.addHandler(self._protocol_log_handler)
        self._protocol_logger.propagate = False
        self._configure_protocol_logging()

    def startup(self):
        self._configure_protocol_logging()
        self.logger.info("OASE FM plugin started")

    def shutdown(self):
        self._disconnect()
        self.logger.info("OASE FM plugin stopped")
        self._remove_protocol_logging()

    def _configure_protocol_logging(self, level_name=None):
        if level_name is None:
            level_name = self.pluginPrefs.get("logLevel", "info")
        level = logging.DEBUG if level_name == "debug" else logging.INFO
        self._protocol_logger.setLevel(level)
        self._protocol_log_handler.setLevel(level)

    def _remove_protocol_logging(self):
        if self._protocol_log_handler is not None:
            self._protocol_logger.removeHandler(self._protocol_log_handler)
            self._protocol_log_handler = None

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
        address = self._static_address(dev.deviceTypeId, dev.pluginProps)
        if address is not None:
            self._update_address(dev, address)
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

        if values_dict.get("logLevel", "info") not in ("info", "debug"):
            errors["logLevel"] = "Select Normal or Protocol debugging"

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
            self._configure_protocol_logging(values_dict.get("logLevel", "info"))
            self._disconnect()
            self._failed_refreshes.clear()

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

        address = self._static_address(type_id, values_dict)
        if address is None and type_id == DEVICE_EGC:
            address = "EGC"
        if address is not None:
            values_dict["address"] = address

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
                discovery = controller.connect()
            except BaseException:
                # The shared module also cleans up failed connections. Keep
                # this guard so the plugin never leaks the TLS callback port
                # if initialization fails before the controller is retained.
                controller.close()
                raise
            self._controller = controller
            self._discovery = discovery
            self._egc_device = None
        return self._controller

    def _disconnect(self):
        with self._lock:
            if self._controller is not None:
                self._controller.close()
            self._controller = None
            self._discovery = None
            self._egc_device = None

    def _controller_call(self, callback):
        with self._lock:
            for attempt in range(2):
                try:
                    return callback(self._get_controller())
                except (OaseError, OSError) as exc:
                    self._disconnect()
                    if attempt == 0 and self._is_timeout_error(exc):
                        self._protocol_logger.debug(
                            "OASE connection timed out; retrying once"
                        )
                        time.sleep(1.0)
                        continue
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _is_timeout_error(exc):
        """Recognize socket timeouts and timeout errors wrapped by the protocol."""
        current = exc
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, TimeoutError):
                return True
            if "timed out" in str(current).lower():
                return True
            current = current.__cause__ or current.__context__
        return False

    def _get_egc_device(self, controller):
        if self._egc_device is None:
            self._egc_device = controller.get_single_egc_device()
        return self._egc_device

    def _refresh_failed(self, refresh_type, message, exc):
        """Log only the transition into a failed refresh state."""
        if refresh_type not in self._failed_refreshes:
            self.logger.warning("%s: %s", message, exc)
            self._failed_refreshes.add(refresh_type)

    def _refresh_succeeded(self, refresh_type, recovery_message):
        """Log only the transition back from a failed refresh state."""
        if refresh_type in self._failed_refreshes:
            self._failed_refreshes.remove(refresh_type)
            self.logger.info(recovery_message)

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
                    updates = dimmer_state_updates(
                        fm_state.dimmer_on,
                        fm_state.dimmer_brightness,
                    )
                else:
                    continue
                dev.updateStatesOnServer(updates)
                dev.setErrorStateOnServer(None)
            self._refresh_succeeded("fm-master", "OASE controller connection restored")
        except (OaseError, OSError, ValueError, KeyError) as exc:
            self._refresh_failed(
                "fm-master",
                "Unable to refresh FM-Master status",
                exc,
            )
            for dev in devices:
                if dev.enabled:
                    if dev.deviceTypeId == DEVICE_CONTROLLER:
                        dev.updateStatesOnServer(
                            [
                                {"key": "onOffState", "value": False},
                                {"key": "connected", "value": False},
                                {"key": "authenticated", "value": False},
                            ]
                        )
                    dev.setErrorStateOnServer(str(exc))
            if raise_errors:
                raise
            return False

        egc_devices = [
            dev for dev in devices if dev.enabled and dev.deviceTypeId == DEVICE_EGC
        ]
        controller_devices = [
            dev
            for dev in devices
            if dev.enabled and dev.deviceTypeId == DEVICE_CONTROLLER
        ]
        controller_ok = True
        if controller_devices:
            try:
                controller_state = self._controller_call(
                    lambda controller: controller.get_controller_state()
                )
                if controller_state.rssi is None:
                    raise OaseError("Controller does not report Wi-Fi RSSI")
                quality = rssi_quality(controller_state.rssi)
                discovery = self._discovery
                if discovery is None:
                    raise OaseError("Controller discovery information is unavailable")
                updates = [
                    {"key": "onOffState", "value": True},
                    {
                        "key": "rssi",
                        "value": controller_state.rssi,
                        "uiValue": f"{controller_state.rssi} dBm",
                    },
                    {"key": "signalQuality", "value": quality},
                    {"key": "connected", "value": True},
                    {"key": "authenticated", "value": True},
                    {"key": "hardwareType", "value": discovery.hardware_type},
                    {"key": "deviceIndex", "value": discovery.device_index},
                    {"key": "controllerName", "value": discovery.name},
                    {"key": "serialNumber", "value": discovery.serial_number},
                    {"key": "modelName", "value": discovery.long_name},
                    {"key": "articleNumber", "value": discovery.order_number},
                    {"key": "release", "value": discovery.firmware},
                    {
                        "key": "firmwareVersion",
                        "value": (
                            f"{discovery.firmware_high}."
                            f"{discovery.firmware_low}"
                        ),
                    },
                    {"key": "wifiChannel", "value": discovery.wifi_channel},
                    {"key": "networkType", "value": discovery.network_type},
                    {"key": "statusText", "value": discovery.status},
                ]
                for dev in controller_devices:
                    dev.updateStatesOnServer(updates)
                    dev.setErrorStateOnServer(None)
                self._refresh_succeeded(
                    "controller-meta",
                    "OASE controller information restored",
                )
            except (OaseError, OSError, ValueError, KeyError) as exc:
                controller_ok = False
                self._refresh_failed(
                    "controller-meta",
                    "Unable to refresh OASE controller information",
                    exc,
                )
                for dev in controller_devices:
                    dev.updateStatesOnServer(
                        [
                            {"key": "onOffState", "value": False},
                            {"key": "connected", "value": False},
                            {"key": "authenticated", "value": False},
                        ]
                    )
                    dev.setErrorStateOnServer(str(exc))
                if raise_errors:
                    raise

        if not egc_devices:
            return controller_ok
        try:
            egc_state = self._controller_call(
                lambda controller: controller.get_egc_state(
                    self._get_egc_device(controller)
                )
            )
            updates = dimmer_state_updates(egc_state.on, egc_state.power)
            rpm = getattr(egc_state, "rpm", None)
            watts = getattr(egc_state, "watts", None)
            if rpm is not None:
                updates.append(
                    {
                        "key": "rpm",
                        "value": rpm,
                        "uiValue": f"{rpm:g} RPM",
                    }
                )
            if watts is not None:
                updates.append(
                    {
                        "key": "watts",
                        "value": watts,
                        "uiValue": f"{watts:g} W",
                    }
                )
            for key, value in (
                ("moduleTemperature", getattr(egc_state, "module_temperature", None)),
                ("pcbTemperature", getattr(egc_state, "pcb_temperature", None)),
                ("waterTemperature", getattr(egc_state, "water_temperature", None)),
            ):
                if value is not None:
                    updates.append(
                        {
                            "key": key,
                            "value": value,
                            "uiValue": f"{value:g} °C",
                        }
                    )
            sfc_enabled = getattr(egc_state, "sfc_enabled", None)
            if sfc_enabled is not None:
                updates.append({"key": "sfcEnabled", "value": sfc_enabled})
            sfc_mode = getattr(egc_state, "sfc_mode", None)
            if sfc_mode is not None:
                updates.append({"key": "sfcMode", "value": sfc_mode})
            egc = egc_state.device
            updates.extend(
                [
                    {
                        "key": "manufacturerIdentifier",
                        "value": egc.manufacturer_identifier,
                    },
                    {
                        "key": "deviceIdentifier",
                        "value": egc.device_identifier,
                    },
                    {"key": "uid", "value": egc.uid_text},
                    {"key": "articleNumber", "value": egc.article_number},
                    {"key": "subdeviceCount", "value": egc.subdevice_count},
                ]
            )
            for dev in egc_devices:
                self._update_address(dev, f"EGC {egc.uid_text}")
                dev.updateStatesOnServer(updates)
                dev.setErrorStateOnServer(None)
            self._refresh_succeeded("egc", "OASE EGC status restored")
            return controller_ok
        except (OaseError, OSError, ValueError, KeyError) as exc:
            self._refresh_failed("egc", "Unable to refresh EGC status", exc)
            for dev in egc_devices:
                dev.setErrorStateOnServer(str(exc))
            if raise_errors:
                raise
            return False

    def _static_address(self, type_id, props):
        if type_id == DEVICE_SWITCHED:
            assignment = self._assignment(type_id, props)
            if assignment is not None:
                return f"Socket {assignment[1]}"
        elif type_id == DEVICE_DIMMER:
            return "Socket 3"
        elif type_id == DEVICE_CONTROLLER:
            device_ip, _local_ip, _password = self._configuration()
            if device_ip:
                return device_ip
        return None

    @staticmethod
    def _update_address(dev, address):
        if getattr(dev, "address", None) == address:
            return
        props = dict(dev.pluginProps)
        if props.get("address") == address:
            return
        props["address"] = address
        dev.replacePluginPropsOnServer(props)

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
        if type_id == DEVICE_CONTROLLER:
            return "controller", 0
        return None
