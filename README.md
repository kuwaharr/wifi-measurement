# Wi-Fi Measurement

Windows-based Python application for collecting Wi-Fi and network-quality measurements at physical locations.

Each measurement records network performance, Wi-Fi information, location, and time.
Raw measurements are stored in SQLite and can later be used to compare tables, areas, and access points.

## Measurement Method

One measurement is one complete Speedtest-style test.

Mesurements may include:

- download speed
- upload speed
- latency
- jitter
- packet loss
- SSID
- BSSID
- signal strength
- latitude and longitude
- area name
- number of attempts required for a successfull measurement

Timestamps are stored in UTC.

A failed attempt does not count as a completed measurement and is not included in measurement calculations.
The number of attempts required to obtain each successfull measurement is recorded.

## Environment

- Windows
- Python
- SQLite
- Ookla Speedtest CLI
