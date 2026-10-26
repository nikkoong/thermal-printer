from __future__ import annotations

from ..config import Config
from .base import Device, PrinterTransport, ProtocolMismatch, TransportError
from .luck import LuckBleTransport
from .mock import MockTransport
from .mxw01 import Mxw01BleTransport
from .serial import SerialEscPosTransport, list_serial_ports

__all__ = [
    "Device",
    "PrinterTransport",
    "ProtocolMismatch",
    "TransportError",
    "LuckBleTransport",
    "Mxw01BleTransport",
    "MockTransport",
    "SerialEscPosTransport",
    "list_serial_ports",
    "make_ble_transport",
    "make_transport",
]


def make_ble_transport(config: Config) -> PrinterTransport:
    """Pick the BLE protocol implementation for the configured device."""
    protocol = (config.protocol or "auto").lower()
    name = (config.device_name or "").lower()
    if protocol == "mxw01" or (protocol == "auto" and "mxw" in name):
        return Mxw01BleTransport(config)
    return LuckBleTransport(config)


def make_transport(config: Config, *, force_mock: bool = False) -> PrinterTransport:
    transport = "mock" if force_mock else (config.transport or "auto").lower()
    if transport in {"mock", "virtual"}:
        return MockTransport(config)
    if transport == "serial":
        return SerialEscPosTransport(config)
    if transport == "ble":
        return make_ble_transport(config)
    # auto
    if config.serial_port and not config.device_name and not config.device_address:
        return SerialEscPosTransport(config)
    return make_ble_transport(config)
