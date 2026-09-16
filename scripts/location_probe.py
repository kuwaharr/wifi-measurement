"""Print one Windows location as JSON without storing it."""

import json
import sys
from pathlib import Path

# Resolve project imports independently of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.location import get_location


def main():
    try:
        location = get_location()
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(location, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
