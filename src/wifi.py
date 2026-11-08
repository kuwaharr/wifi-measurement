"""Read a connected Windows Native Wi-Fi snapshot without scanning or changing settings."""

import ctypes as c
import sys
from datetime import datetime, timezone

DWORD = c.c_uint32
LONG = c.c_int32
USHORT = c.c_uint16
BYTE = c.c_uint8
HANDLE = c.c_void_p
PVOID = c.c_void_p


class GUID(c.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", USHORT), ("Data3", USHORT), ("Data4", BYTE * 8)]


class DOT11_SSID(c.Structure):
    _fields_ = [("uSSIDLength", DWORD), ("ucSSID", BYTE * 32)]


class WLAN_INTERFACE_INFO(c.Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", USHORT * 256), ("isState", LONG)]


class WLAN_ASSOCIATION_ATTRIBUTES(c.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", LONG),
        ("dot11Bssid", BYTE * 6),
        ("dot11PhyType", LONG),
        ("uDot11PhyIndex", DWORD),
        ("wlanSignalQuality", DWORD),
        ("ulRxRate", DWORD),
        ("ulTxRate", DWORD),
    ]


class WLAN_SECURITY_ATTRIBUTES(c.Structure):
    _fields_ = [
        ("bSecurityEnabled", LONG),
        ("bOneXEnabled", LONG),
        ("dot11AuthAlgorithm", LONG),
        ("dot11CipherAlgorithm", LONG),
    ]


class WLAN_CONNECTION_ATTRIBUTES(c.Structure):
    _fields_ = [
        ("isState", LONG),
        ("wlanConnectionMode", LONG),
        ("strProfileName", USHORT * 256),
        ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
        ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES),
    ]


class WLAN_RATE_SET(c.Structure):
    _fields_ = [("uRateSetLength", DWORD), ("usRateSet", USHORT * 126)]


class WLAN_BSS_ENTRY(c.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", DWORD),
        ("dot11Bssid", BYTE * 6),
        ("dot11BssType", LONG),
        ("dot11BssPhyType", LONG),
        ("lRssi", LONG),
        ("uLinkQuality", DWORD),
        ("bInRegDomain", BYTE),
        ("usBeaconPeriod", USHORT),
        ("ullTimestamp", c.c_uint64),
        ("ullHostTimestamp", c.c_uint64),
        ("usCapabilityInformation", USHORT),
        ("ulChCenterFrequency", DWORD),
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", DWORD),
        ("ulIeSize", DWORD),
    ]


class WLAN_INTERFACE_INFO_LIST(c.Structure):
    _fields_ = [("dwNumberOfItems", DWORD), ("dwIndex", DWORD), ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class WLAN_BSS_LIST(c.Structure):
    _fields_ = [("dwTotalSize", DWORD), ("dwNumberOfItems", DWORD), ("wlanBssEntries", WLAN_BSS_ENTRY * 1)]


def _load_api():
    try:
        api = c.WinDLL("wlanapi.dll")
    except (OSError, AttributeError) as exc:
        raise RuntimeError("Could not load Windows Native Wi-Fi API.") from exc
    signatures = {
        "WlanOpenHandle": [DWORD, PVOID, c.POINTER(DWORD), c.POINTER(HANDLE)],
        "WlanCloseHandle": [HANDLE, PVOID],
        "WlanEnumInterfaces": [HANDLE, PVOID, c.POINTER(PVOID)],
        "WlanQueryInterface": [
            HANDLE,
            c.POINTER(GUID),
            LONG,
            PVOID,
            c.POINTER(DWORD),
            c.POINTER(PVOID),
            c.POINTER(LONG),
        ],
        "WlanGetNetworkBssList": [HANDLE, c.POINTER(GUID), c.POINTER(DOT11_SSID), LONG, LONG, PVOID, c.POINTER(PVOID)],
        "WlanFreeMemory": [PVOID],
    }
    for name, arguments in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = None if name == "WlanFreeMemory" else DWORD
    return api


def _check(name, code):
    if code:
        hint = ""
        if code == 5:
            hint = " Check Settings > Privacy & security > Location permissions."
        elif code == 1062:
            hint = " The WLAN service is not running."
        raise RuntimeError(f"{name} failed (error {code}).{hint}")


def _copy(pointer, structure):
    if not pointer.value:
        raise RuntimeError("Wi-Fi API returned a NULL buffer.")
    return structure.from_buffer_copy(c.string_at(pointer, c.sizeof(structure)))


def _ssid(ssid):
    if ssid.uSSIDLength > 32:
        raise RuntimeError("Wi-Fi API returned an invalid SSID length.")
    return bytes(ssid.ucSSID[: ssid.uSSIDLength])


def _connection_key(connection):
    association = connection.wlanAssociationAttributes
    if connection.isState != 1:
        raise RuntimeError("Wi-Fi disconnected during acquisition.")
    if association.dot11BssType != 1:
        raise RuntimeError("Only infrastructure Wi-Fi connections are supported.")
    mac = bytes(association.dot11Bssid)
    if mac == bytes(6) or mac[0] & 1:
        raise RuntimeError("Wi-Fi API returned an invalid BSSID.")
    if association.wlanSignalQuality > 100:
        raise RuntimeError("Wi-Fi API returned an invalid signal quality.")
    return _ssid(association.dot11Ssid), mac, association.dot11BssType


def get_wifi_info() -> dict:
    """Return selected raw connection/BSS values; never print or persist them."""
    if sys.platform != "win32":
        raise RuntimeError("Wi-Fi acquisition requires Windows.")
    api = _load_api()
    handle, version = HANDLE(), DWORD()
    _check("WlanOpenHandle", api.WlanOpenHandle(2, None, c.byref(version), c.byref(handle)))
    if not handle.value:
        raise RuntimeError("WlanOpenHandle returned a NULL handle.")
    try:
        pointer = PVOID()
        try:
            _check("WlanEnumInterfaces", api.WlanEnumInterfaces(handle, None, c.byref(pointer)))
            header = _copy(pointer, DWORD * 2)
            count = header[0]
            if count > 1024 or (count and header[1] >= count):
                raise RuntimeError("Invalid Wi-Fi interface list.")
            interfaces = []
            for index in range(count):
                address = (
                    pointer.value
                    + WLAN_INTERFACE_INFO_LIST.InterfaceInfo.offset
                    + index * c.sizeof(WLAN_INTERFACE_INFO)
                )
                item = WLAN_INTERFACE_INFO.from_buffer_copy(c.string_at(address, c.sizeof(WLAN_INTERFACE_INFO)))
                if item.isState == 1:
                    interfaces.append(item)
            if not count:
                raise RuntimeError("No Wi-Fi adapters found.")
            if len(interfaces) != 1:
                raise RuntimeError(
                    "No connected Wi-Fi adapter." if not interfaces else "Multiple connected Wi-Fi adapters."
                )
            guid = interfaces[0].InterfaceGuid
        finally:
            if pointer.value:
                api.WlanFreeMemory(pointer)

        def query(opcode, structure):
            data, size, kind = PVOID(), DWORD(), LONG()
            try:
                _check(
                    "WlanQueryInterface",
                    api.WlanQueryInterface(
                        handle, c.byref(guid), opcode, None, c.byref(size), c.byref(data), c.byref(kind)
                    ),
                )
                if size.value < c.sizeof(structure):
                    raise RuntimeError("WlanQueryInterface returned an undersized buffer.")
                return _copy(data, structure)
            finally:
                if data.value:
                    api.WlanFreeMemory(data)

        before = query(7, WLAN_CONNECTION_ATTRIBUTES)
        key = _connection_key(before)
        channel = query(8, DWORD).value
        if channel <= 0:
            raise RuntimeError("Wi-Fi API returned an invalid channel.")
        pointer = PVOID()
        try:
            _check(
                "WlanGetNetworkBssList",
                api.WlanGetNetworkBssList(handle, c.byref(guid), None, 3, 0, None, c.byref(pointer)),
            )
            header = _copy(pointer, DWORD * 2)
            total, count = header
            offset = WLAN_BSS_LIST.wlanBssEntries.offset
            if total < offset or count > (total - offset) // c.sizeof(WLAN_BSS_ENTRY):
                raise RuntimeError("Invalid Wi-Fi BSS list size or count.")
            matches = []
            for index in range(count):
                entry = WLAN_BSS_ENTRY.from_buffer_copy(
                    c.string_at(pointer.value + offset + index * c.sizeof(WLAN_BSS_ENTRY), c.sizeof(WLAN_BSS_ENTRY))
                )
                if (_ssid(entry.dot11Ssid), bytes(entry.dot11Bssid), entry.dot11BssType) == key:
                    matches.append(entry)
            if not matches:
                raise RuntimeError("No BSS entry matches the connected Wi-Fi.")
            latest = max(entry.ullHostTimestamp for entry in matches)
            values = {(entry.lRssi, entry.ulChCenterFrequency) for entry in matches if entry.ullHostTimestamp == latest}
            if len(values) != 1:
                raise RuntimeError("Ambiguous Wi-Fi BSS observations at the same timestamp.")
            rssi, frequency = values.pop()
            if frequency <= 0:
                raise RuntimeError("Wi-Fi API returned an invalid center frequency.")
        finally:
            if pointer.value:
                api.WlanFreeMemory(pointer)
        if _connection_key(query(7, WLAN_CONNECTION_ATTRIBUTES)) != key or query(8, DWORD).value != channel:
            raise RuntimeError("Wi-Fi connection or channel changed during acquisition.")
        raw_ssid, mac, _ = key
        try:
            ssid = raw_ssid.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            ssid = None
        result = {
            "ssid": ssid,
            "ssid_hex": raw_ssid.hex(),
            "bssid": ":".join(f"{part:02x}" for part in mac),
            "signal_quality_percent": before.wlanAssociationAttributes.wlanSignalQuality,
            "rssi_dbm": rssi,
            "channel": channel,
            "frequency_khz": frequency,
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    finally:
        _check("WlanCloseHandle", api.WlanCloseHandle(handle, None))
    return result
