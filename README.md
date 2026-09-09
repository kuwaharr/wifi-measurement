# Wi-Fi Measurement

Windows-based Python application for collecting Wi-Fi and network-quality measurement at physical locations.

Each measurement records network performance, Wi-Fi information, location, and time.
Raw measurements are stored in SQLite and can later be used to compare tables, locations, and access points.

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

## Environment

- Windows
- Python
- SQLite
- Ookla Speedtest CLI
