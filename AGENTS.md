# Agent Instructions

## Environment

- Target Windows as the primary runtime.
- Use Python.
- Use SQLite for persistent storage.
- Keep Windows-specific code isolated where practical.

## Measurement Rules

- One database row represents one successfully completed raw measurement.
- Preserve useful raw measurement values instead of storing only derived or aggregated values.
- Do not replace raw measurements with precomputed averages.
- Store measurement timestamps in UTC.
- A failed attempt is not a completed measurement.
- Do not include failed attempts in averages or other calculations based on completed measurements.
- Track how many attempts were required to obtain each successful measurement.
- Each measurement must contain `area_name` so measurements can be grouped by physical area.
- `area_name` should be typed through CLI every time measurement is recorded.
- Use the official Ookla Speedtest CLI for network-performance measurements.
- Invoke external commands through Python rather than reimplementing the Speedtest algorithm.

## Raw Data to Preserve

When available from a completed Speedtest, preserve:

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

### Wi-Fi Information

Preserve:

- SSID
- BSSID
- signal strength / RSSI
- channel
- frequency or band

### Location and Measurement Metadata

Preserve:

- latitude
- longitude
- area name
- UTC timestamp
- number of attempts required for the successful measurement

Do not collect unnecessary optional Speedtest metadata, including:

- ISP information
- Speedtest result URL or result ID
- interface information
- local or external IP information

## Development Guidelines

- Implement one feature rather than implementing many at the same time.
- Prefer simple implementations over premature abstraction.
- Prefer the Python standard library when sufficient.
- Avoid unnecessary dependencies.
- Keep measurement acquisition, storage, and analysis separate.
- Do not introduce PostgreSQL or a web backend unless requirements change.
- Preserve existing project conventions unless there is a concrete reason to change them.
