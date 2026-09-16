# Wi-Fi Measurement

Windows-based Python application for collecting Wi-Fi and network-quality measurements at physical locations.

Each measurement records network performance, Wi-Fi information, location, and time. Raw measurements are stored in SQLite so that useful metrics can be selected and analyzed later without discarding the original measurement data.

## Measurement Method

One measurement is one complete Speedtest-style test.

Only successfully completed tests count as measurements.

A failed attempt does not count as a completed measurement and is not included in calculations based on completed measurements. The number of attempts required to obtain each successful measurement is recorded.

Measurements are grouped by physical area using an `area_name`.

Timestamps are stored in UTC.

## Raw Measurement Data

A completed measurement preserves useful raw values from the Speedtest and Wi-Fi measurements.

### Network Performance

- download speed
- upload speed
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
- area name
- UTC timestamp
- number of attempts required for the successful measurement

## Environment

- Windows
- Python
- SQLite
- Ookla Speedtest CLI
