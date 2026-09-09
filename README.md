# Wi-Fi Measurement

Windows-based Python application for collecting Wi-Fi and network-quality measurements at physical locations.

Each measurement records network performance, Wi-Fi information, location, and time.
Raw measurements are stored in SQLite so that useful metrics and calculations can be selected later without discarding the original measurement data.

## Measurement Method

One measurement is one complete Speedtest-style test.

Only successfully completed tests count as measurements.
Failed attempts are excluded from measurement calculations, but the number of attempts required to obtain each successful measurement is recorded.

Measurements are grouped by physical area using an `area_name` value.

Timestamps are stored in UTC.

## Raw Measurement Data

A completed measurement should preserve the useful raw values available from the Speedtest and Wi-Fi measurements rather than storing only derived or aggregated values.

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

Optional Speedtest metadata such as ISP information, result URLs or IDs, and interface or IP information is not collected.

## Environment

- Windows
- Python
- SQLite
- Ookla Speedtest CLI
