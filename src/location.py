"""Read one Windows location using WinRT through Windows PowerShell."""

import base64
import json
import math
import subprocess
import sys
from datetime import datetime, timezone

_LOCATION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Devices.Geolocation.Geolocator, Windows.Devices.Geolocation, ContentType = WindowsRuntime]
    $null = [Windows.Devices.Geolocation.Geoposition, Windows.Devices.Geolocation, ContentType = WindowsRuntime]
    $locator = [Windows.Devices.Geolocation.Geolocator]::new()
    $locator.DesiredAccuracy = [Windows.Devices.Geolocation.PositionAccuracy]::High
    # Request a 10-meter target; the returned Accuracy reports the actual estimate.
    $locator.DesiredAccuracyInMeters = 10

    # Use existing desktop location permission; errors are reported without changing settings.
    $operation = $locator.GetGeopositionAsync([TimeSpan]::Zero, [TimeSpan]::FromSeconds(45))
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $asTask.MakeGenericMethod([Windows.Devices.Geolocation.Geoposition]).Invoke($null, @($operation))
    $coordinate = $task.GetAwaiter().GetResult().Coordinate
    $position = $coordinate.Point.Position
    [ordered]@{
        latitude = $position.Latitude
        longitude = $position.Longitude
        accuracy_meters = $coordinate.Accuracy
        position_source = $coordinate.PositionSource.ToString()
        timestamp = $coordinate.Timestamp.ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Compress
} catch {
    $cause = $_.Exception.GetBaseException()
    [ordered]@{
        error = $cause.Message
        hresult = $cause.HResult
    } | ConvertTo-Json -Compress
    exit 1
}
"""


def get_location() -> dict:
    """Return coordinates, accuracy in meters, position source, and UTC timestamp.

    Requests 10-meter accuracy, which Windows may not be able to achieve.
    Requires Windows desktop location access. Raises RuntimeError when no valid
    position is available. Does not print or persist location data.
    """
    if sys.platform != "win32":
        raise RuntimeError("Location acquisition requires Windows.")

    encoded_script = base64.b64encode(_LOCATION_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("Could not launch Windows PowerShell for location acquisition.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Windows did not return a location within 60 seconds.") from exc

    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError("Windows PowerShell did not return valid location JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Windows PowerShell returned an invalid location response.")
    if completed.returncode != 0 or "error" in result:
        detail = result.get("error", "Unknown Windows location error.")
        raise RuntimeError(
            f"Could not obtain Windows location: {detail} "
            "Check Settings > Privacy & security > Location, including desktop app access."
        )

    for field, minimum, maximum in (
        ("latitude", -90, 90),
        ("longitude", -180, 180),
        ("accuracy_meters", 0, math.inf),
    ):
        value = result.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise RuntimeError(f"Windows returned an invalid {field}.")
    try:
        timestamp = datetime.fromisoformat(result["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError("Missing timestamp timezone.")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Windows returned an invalid location timestamp.") from exc

    return {
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "accuracy_meters": result["accuracy_meters"],
        "position_source": result.get("position_source"),
        "timestamp": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
