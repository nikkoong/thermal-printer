from __future__ import annotations

from pathlib import Path

from thermal.config import Config
from thermal.history import HistoryStore
from thermal.printer import PrinterManager
from thermal.transports import Mxw01BleTransport, ProtocolMismatch, make_ble_transport
from thermal.transports.luck import _looks_like_printer
from thermal.transports.mock import MockTransport


def test_printer_name_hints() -> None:
    assert _looks_like_printer("DP-L1S", [])
    assert _looks_like_printer("L1-1234", [])
    assert _looks_like_printer("L1", [])
    assert _looks_like_printer("C&Co 3128_BLE", [])
    assert _looks_like_printer("Luck Jingle L1", [])
    assert _looks_like_printer("MXW01", [])
    assert _looks_like_printer("(unnamed)", ["0000ff00-0000-1000-8000-00805f9b34fb"])
    assert _looks_like_printer("(unnamed)", ["0000ae30-0000-1000-8000-00805f9b34fb"])


def test_ble_transport_selection() -> None:
    assert isinstance(make_ble_transport(Config(protocol="mxw01")), Mxw01BleTransport)
    assert isinstance(
        make_ble_transport(Config(protocol="auto", device_name="MXW01")),
        Mxw01BleTransport,
    )
    assert not isinstance(
        make_ble_transport(Config(protocol="auto", device_name="C&Co 3128_BLE")),
        Mxw01BleTransport,
    )
    assert not isinstance(make_ble_transport(Config(protocol="luck")), Mxw01BleTransport)


def test_non_printer_names_are_not_flagged() -> None:
    assert not _looks_like_printer("L10 Speaker", [])
    assert not _looks_like_printer("(unnamed)", [])
    assert not _looks_like_printer("Kitchen Display", ["0000fea0-0000-1000-8000-00805f9b34fb"])


class _FakeBleTransport:
    name = "ble"
    connected = True
    label = "fake BLE printer"

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def status(self) -> None:
        return None

    async def battery(self) -> None:
        return None

    async def info(self) -> dict[str, object]:
        return {"model": "fake"}


class _MismatchTransport(_FakeBleTransport):
    connected = False

    async def connect(self) -> None:
        raise ProtocolMismatch("wrong protocol", "mxw01")


async def test_auto_mode_retries_with_detected_protocol(tmp_path: Path, monkeypatch) -> None:
    config = Config(transport="ble", protocol="auto")
    manager = PrinterManager(config, history=HistoryStore(tmp_path))
    seen: list[str] = []

    def fake_make_transport(name: str | None = None):
        seen.append(config.protocol)
        if len(seen) == 1:
            return _MismatchTransport()
        return _FakeBleTransport()

    monkeypatch.setattr(manager, "_make_transport", fake_make_transport)

    result = await manager.connect()

    assert result["connected"] is True
    assert config.protocol == "mxw01"
    assert seen == ["auto", "mxw01"]


async def test_connect_to_scanned_device_leaves_mock_mode(tmp_path: Path, monkeypatch) -> None:
    config = Config(transport="mock")
    manager = PrinterManager(config, history=HistoryStore(tmp_path))

    def fake_make_transport(name: str | None = None):
        if (name or config.transport) == "mock":
            return MockTransport(config)
        return _FakeBleTransport()

    monkeypatch.setattr(manager, "_make_transport", fake_make_transport)

    await manager.connect(transport="mock")
    assert manager.transport is not None
    assert manager.transport.name == "mock"

    await manager.connect(address="AA:BB:CC", name="C&Co 3128_BLE")
    assert manager.transport is not None
    assert manager.transport.name == "ble"
    assert config.transport == "ble"
    assert config.device_address == "AA:BB:CC"
