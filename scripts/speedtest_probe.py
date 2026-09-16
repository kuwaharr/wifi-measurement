"""Print selected raw measurements from one official Ookla Speedtest as JSON."""

import json
import sys
from pathlib import Path

# Resolve project imports relative to this script, regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.speedtest import run_speedtest


def main():
    try:
        measurement = run_speedtest()
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(measurement, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
