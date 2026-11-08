import ctypes as c
import unittest
from unittest.mock import Mock, patch

from src import wifi as w


class FakeApi:
    def __init__(self, ssid=b"test"):
        self.buffers = []
        self.freed = []
        self.closed = 0
        self.errors = {}
        self.count = 1
        self.query_count = 0
        self.channel = 36
        self.change = False
        self.connection = w.WLAN_CONNECTION_ATTRIBUTES()
        self.connection.isState = 1
        a = self.connection.wlanAssociationAttributes
        a.dot11BssType = 1
        a.dot11Ssid.uSSIDLength = len(ssid)
        a.dot11Ssid.ucSSID[: len(ssid)] = ssid
        a.dot11Bssid[:] = b"\x02\x11\x22\x33\x44\x55"
        a.wlanSignalQuality = 71
        entry = w.WLAN_BSS_ENTRY()
        entry.dot11Ssid = a.dot11Ssid
        entry.dot11Bssid = a.dot11Bssid
        entry.dot11BssType = 1
        entry.lRssi = -61
        entry.ulChCenterFrequency = 5180000
        entry.ullHostTimestamp = 100
        self.entries = [entry]

    def allocate(self, data, output):
        buffer = c.create_string_buffer(data)
        self.buffers.append(buffer)
        c.cast(output, c.POINTER(w.PVOID))[0] = c.addressof(buffer)

    def WlanOpenHandle(self, version, reserved, negotiated, handle):
        assert version == 2 and reserved is None
        if self.errors.get("open"):
            return self.errors["open"]
        c.cast(handle, c.POINTER(w.HANDLE))[0] = 0x123456789
        return 0

    def WlanCloseHandle(self, handle, reserved):
        assert handle.value == 0x123456789 and reserved is None
        self.closed += 1
        return self.errors.get("close", 0)

    def WlanFreeMemory(self, pointer):
        # Fake allocations are owned by Python; this mock never frees them with the OS API.
        assert pointer.value not in self.freed
        self.freed.append(pointer.value)

    def WlanEnumInterfaces(self, handle, reserved, output):
        if self.errors.get("enum"):
            return self.errors["enum"]
        info = w.WLAN_INTERFACE_INFO()
        info.isState = 1
        info.InterfaceGuid.Data1 = 42
        self.allocate(bytes((w.DWORD * 2)(self.count, 0)) + bytes(info) * self.count, output)
        return 0

    def WlanQueryInterface(self, handle, guid, opcode, reserved, size, output, kind):
        assert c.cast(guid, c.POINTER(w.GUID)).contents.Data1 == 42
        assert reserved is None
        if self.errors.get("query"):
            return self.errors["query"]
        if opcode == 7:
            self.query_count += 1
            data = bytes(self.connection)
            if self.change and self.query_count == 2:
                connection = w.WLAN_CONNECTION_ATTRIBUTES.from_buffer_copy(data)
                connection.wlanAssociationAttributes.dot11Bssid[5] = 99
                data = bytes(connection)
        else:
            assert opcode == 8
            data = bytes(w.DWORD(self.channel))
        c.cast(size, c.POINTER(w.DWORD))[0] = 0 if self.errors.get("size") else len(data)
        self.allocate(data, output)
        return 0

    def WlanGetNetworkBssList(self, handle, guid, ssid, bss_type, security, reserved, output):
        assert ssid is None and bss_type == 3 and security == 0 and reserved is None
        if self.errors.get("bss"):
            return self.errors["bss"]
        entries = b"".join(bytes(entry) for entry in self.entries)
        total = 8 + len(entries)
        if self.errors.get("total"):
            total = 7
        self.allocate(bytes((w.DWORD * 2)(total, len(self.entries))) + entries, output)
        return 0


class WifiTests(unittest.TestCase):
    def read(self, api):
        with patch.object(w.sys, "platform", "win32"), patch.object(w, "_load_api", return_value=api):
            try:
                return w.get_wifi_info()
            finally:
                self.assertEqual(len(api.buffers), len(api.freed))
                self.assertEqual(api.closed, 0 if api.errors.get("open") else 1)

    def test_windows_abi(self):
        self.assertEqual(c.sizeof(w.GUID), 16)
        self.assertEqual(c.sizeof(w.DOT11_SSID), 36)
        self.assertEqual(c.sizeof(w.WLAN_INTERFACE_INFO), 532)
        self.assertEqual(c.sizeof(w.WLAN_ASSOCIATION_ATTRIBUTES), 68)
        self.assertEqual(c.sizeof(w.WLAN_CONNECTION_ATTRIBUTES), 604)
        self.assertEqual(c.sizeof(w.WLAN_BSS_ENTRY), 360)
        self.assertEqual(w.WLAN_BSS_ENTRY.ullTimestamp.offset, 72)
        self.assertEqual(w.WLAN_BSS_ENTRY.ulChCenterFrequency.offset, 92)
        self.assertEqual(w.WLAN_BSS_LIST.wlanBssEntries.offset, 8)

    def test_success_and_ssid_bytes(self):
        for raw in (b"test", "日本語".encode(), b"x" * 32, b"", b" a\x00b ", b"\xff"):
            with self.subTest(raw=raw):
                result = self.read(FakeApi(raw))
                self.assertEqual(result["ssid_hex"], raw.hex())
                self.assertEqual(result["ssid"], None if raw == b"\xff" else raw.decode())
                self.assertEqual(result["rssi_dbm"], -61)
                self.assertEqual(result["frequency_khz"], 5180000)
                self.assertEqual(result["channel"], 36)
                self.assertEqual(result["bssid"], "02:11:22:33:44:55")
                self.assertTrue(result["captured_at"].endswith("Z"))
                self.assertEqual(len(result), 8)

    def test_latest_and_ambiguous_bss(self):
        api = FakeApi()
        newer = w.WLAN_BSS_ENTRY.from_buffer_copy(bytes(api.entries[0]))
        newer.ullHostTimestamp = 101
        newer.lRssi = -44
        api.entries.append(newer)
        self.assertEqual(self.read(api)["rssi_dbm"], -44)
        api = FakeApi()
        other = w.WLAN_BSS_ENTRY.from_buffer_copy(bytes(api.entries[0]))
        other.lRssi = -44
        api.entries.append(other)
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.read(api)

    def test_api_errors_and_release(self):
        for stage in ("open", "enum", "query", "bss", "close"):
            for code in (5, 1062, 50):
                with self.subTest(stage=stage, code=code):
                    api = FakeApi()
                    api.errors[stage] = code
                    with self.assertRaisesRegex(RuntimeError, f"error {code}"):
                        self.read(api)

    def test_invalid_values_and_changes(self):
        for case in (
            "zero",
            "multiple",
            "quality",
            "channel",
            "frequency",
            "length",
            "change",
            "size",
            "total",
            "no_bss",
        ):
            with self.subTest(case=case):
                api = FakeApi()
                if case == "zero":
                    api.count = 0
                if case == "multiple":
                    api.count = 2
                if case == "quality":
                    api.connection.wlanAssociationAttributes.wlanSignalQuality = 101
                if case == "channel":
                    api.channel = 0
                if case == "frequency":
                    api.entries[0].ulChCenterFrequency = 0
                if case == "length":
                    api.connection.wlanAssociationAttributes.dot11Ssid.uSSIDLength = 33
                if case == "change":
                    api.change = True
                if case in ("size", "total"):
                    api.errors[case] = True
                if case == "no_bss":
                    api.entries = []
                with self.assertRaises(RuntimeError):
                    self.read(api)

    def test_non_windows_and_dll_signatures(self):
        with patch.object(w.sys, "platform", "other"), patch.object(w, "_load_api") as load:
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                w.get_wifi_info()
            load.assert_not_called()
        dll = Mock()
        with patch.object(w.c, "WinDLL", return_value=dll, create=True):
            self.assertIs(w._load_api(), dll)
        self.assertEqual(dll.WlanOpenHandle.restype, w.DWORD)
        self.assertIsNone(dll.WlanFreeMemory.restype)
        self.assertEqual(dll.WlanQueryInterface.argtypes[0], w.HANDLE)

    def test_other_access_points_are_not_substituted(self):
        for field in ("bssid", "ssid", "type"):
            with self.subTest(field=field):
                api = FakeApi()
                entry = api.entries[0]
                if field == "bssid":
                    entry.dot11Bssid[5] = 99
                elif field == "ssid":
                    entry.dot11Ssid.ucSSID[0] = 99
                else:
                    entry.dot11BssType = 2
                with self.assertRaisesRegex(RuntimeError, "No BSS entry"):
                    self.read(api)

    def test_quality_boundaries_and_unsupported_connection(self):
        for quality in (0, 100):
            api = FakeApi()
            api.connection.wlanAssociationAttributes.wlanSignalQuality = quality
            self.assertEqual(self.read(api)["signal_quality_percent"], quality)
        api = FakeApi()
        api.connection.wlanAssociationAttributes.dot11BssType = 2
        with self.assertRaisesRegex(RuntimeError, "infrastructure"):
            self.read(api)
        api = FakeApi()
        api.connection.isState = 4
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            self.read(api)

    def test_allocated_buffers_are_freed_on_interrupt(self):
        for method in ("WlanEnumInterfaces", "WlanQueryInterface", "WlanGetNetworkBssList"):
            with self.subTest(method=method):
                api = FakeApi()
                original = getattr(api, method)

                def interrupted(*args):
                    original(*args)
                    raise KeyboardInterrupt

                setattr(api, method, interrupted)
                with self.assertRaises(KeyboardInterrupt):
                    self.read(api)

    def test_close_error_retains_original_exception(self):
        api = FakeApi()
        api.errors.update(query=5, close=50)
        with self.assertRaises(RuntimeError) as caught:
            self.read(api)
        self.assertIn("WlanCloseHandle", str(caught.exception))
        self.assertIn("WlanQueryInterface", str(caught.exception.__context__))

    def test_null_query_buffer_is_rejected(self):
        api = FakeApi()

        def query(handle, guid, opcode, reserved, size, output, kind):
            c.cast(size, c.POINTER(w.DWORD))[0] = c.sizeof(w.WLAN_CONNECTION_ATTRIBUTES)
            return 0

        api.WlanQueryInterface = query
        with self.assertRaisesRegex(RuntimeError, "NULL"):
            self.read(api)


if __name__ == "__main__":
    unittest.main()
