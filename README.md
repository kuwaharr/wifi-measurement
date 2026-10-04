# Wi-Fi Measurement

Windows-based Python application for collecting Wi-Fi and network-quality measurements at physical locations.

Each measurement records network performance, Wi-Fi information, location, and time. Raw measurements are stored in SQLite so that useful metrics can be selected and analyzed later without discarding the original measurement data.

## Measurement Method

One measurement is one successfully completed Speedtest-style test.

Failed attempts do not count as completed measurements and are not included in calculations based on completed measurements. The number of attempts required to obtain each successful measurement is recorded.

Speedtest values are stored using the raw values and units returned by the Ookla Speedtest CLI, without conversion.

Timestamps are stored in UTC.

Each measurement includes the following required location metadata:

- `location_type`: `inside` or `outside`
- `location_name`: the building name for an indoor measurement, or a meaningful nearby place or landmark for an outdoor measurement
- `location_detail`: a more precise description of the measurement location, including the floor number when applicable

## Raw Measurement Data

A completed measurement preserves useful raw values from the Speedtest and Wi-Fi measurements.

### Network Performance

- download bandwidth
- upload bandwidth
- idle latency
- idle jitter
- idle latency low and high
- download loaded latency
- download loaded jitter
- download latency low and high
- upload loaded latency
- upload loaded jitter
- upload latency low and high
- packet loss
- downloaded bytes
- download elapsed time
- uploaded bytes
- upload elapsed time

### Speedtest Server

- server ID
- server name
- server location

### Wi-Fi

- SSID
- BSSID
- signal strength / RSSI
- channel
- frequency or band

### Location and Measurement Metadata

- latitude
- longitude
- location type
- location name
- location detail
- UTC timestamp
- number of attempts required for the successful measurement

## Environment

- Windows
- Python
- SQLite
- Ookla Speedtest CLI
