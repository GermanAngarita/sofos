"""Conexión por cable y por Wi-Fi del adaptador de iPhone (sin iPhone real)."""

import asyncio
from types import SimpleNamespace

import pytest

from sofos.devices import ios
from sofos.devices.base import DeviceError


def mux(udid, ctype):
    return SimpleNamespace(serial=udid, connection_type=ctype, matches_udid=lambda u, x=udid: u == x)


class FakeLockdown:
    def __init__(self, wifi=False):
        self.short_info = {"DeviceName": "iPhone de prueba", "ProductVersion": "26.6", "ProductType": "iPhone18,1"}
        self.display_name = "iPhone 17 Pro"
        self.wifi = wifi
        self.set_calls = []

    async def get_enable_wifi_connections(self):
        return self.wifi

    async def set_enable_wifi_connections(self, on):
        self.set_calls.append(on)

    async def close(self):
        pass


@pytest.fixture
def adapter(monkeypatch):
    a = ios.IOSAdapter()
    a.opened = []
    a.lockdown = FakeLockdown()

    async def fake_lockdown(udid, connection_type=None):
        connection_type = connection_type or await a._connection_type(udid)
        a.opened.append(connection_type)
        return a.lockdown

    monkeypatch.setattr(a, "_lockdown", fake_lockdown)
    return a


def with_mux(monkeypatch, devices):
    async def fake_list():
        return devices
    monkeypatch.setattr(ios, "usbmux_list", fake_list)


def test_cable_and_wifi_are_one_device_preferring_cable(adapter, monkeypatch):
    with_mux(monkeypatch, [mux("A", "Network"), mux("A", "USB")])
    [d] = asyncio.run(adapter.list_devices())
    assert d.connection == "usb" and adapter.opened == ["USB"]
    assert "screen" in d.capabilities and d.wifi_enabled is False


def test_wifi_only_has_no_screen(adapter, monkeypatch):
    with_mux(monkeypatch, [mux("A", "Network")])
    [d] = asyncio.run(adapter.list_devices())
    assert d.connection == "wifi" and adapter.opened == ["Network"]
    assert "screen" not in d.capabilities and d.wifi_enabled is True
    assert {"browse", "files", "messages"} <= set(d.capabilities)


def test_enable_wifi_needs_cable(adapter, monkeypatch):
    with_mux(monkeypatch, [mux("A", "Network")])
    with pytest.raises(DeviceError, match="cable"):
        asyncio.run(adapter.set_wifi("A", True))
    with_mux(monkeypatch, [mux("A", "USB")])
    asyncio.run(adapter.set_wifi("A", True))
    assert adapter.lockdown.set_calls == [True]


@pytest.mark.parametrize("root,path,expected", [
    ("media", "/DCIM/100APPLE", "/DCIM/100APPLE"),
    ("media", "../../etc", "/etc"),  # AFC ya está encerrado en la zona de medios
    ("app:com.x", "/", "/Documents"),
    ("app:com.x", "/Documents/sub", "/Documents/sub"),
    ("app:com.x", "sub/file.txt", "/Documents/sub/file.txt"),
])
def test_fs_path_normalizes(root, path, expected):
    assert ios.IOSAdapter._fs_path(root, path) == expected


@pytest.mark.parametrize("path", ["/Documents/../Library", "../Library/Preferences", "/Documents/../../x"])
def test_fs_path_cannot_leave_app_documents(path):
    with pytest.raises(FileNotFoundError):
        ios.IOSAdapter._fs_path("app:com.x", path)
