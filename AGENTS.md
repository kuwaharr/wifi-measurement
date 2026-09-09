# Agent Instructions

## Environment

- Target Windows as the primary runtime.
- Use Python.
- Use SQLite for persistent storage.
- Keep Windows-specific code isolated where practcal.

## Measurement Rules

- One datebase row represents one raw measurement.
- Do not replace raw measurements with precomputed averages.
- Use the official Ookla Speedtest CLI for network performance measurements.
- Invoke external commands through Python rahter than reimplementing the Speedtest algorithm.

## Development Guidelines

- Implement one feature rather than implementing many at the same time.
- Prefer simple implementations over premature abstraction.
- Prefer the Python standard library when sufficient.
- Avoid unnecessary dependencies.
- Keep measurement acquisition, storage, and analysis separate.
- Do not introduce PostgreSQL or a web backend unless requirements change.
- Preserve existing project conventions unless there is a concrete reason to change them.
