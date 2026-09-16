"""Acquire selected raw measurements using the official Ookla Speedtest CLI."""

import json
import subprocess


def _select_fields(value, fields):
    """Keep requested fields, representing unavailable values as None."""
    if not isinstance(value, dict):
        value = {}
    return {field: value.get(field) for field in fields}


def run_speedtest() -> dict:
    """Return one completed measurement; raise RuntimeError on failure.

    Missing fields are None. Raw Ookla values and units are preserved.
    This function does not print or store measurements.
    """
    try:
        completed = subprocess.run(
            ["speedtest", "--format=json", "--progress=no"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=180,
            check=True,
        )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ValueError("Ookla did not return a completed measurement.")
    except FileNotFoundError:
        error = "Official Ookla Speedtest CLI was not found on PATH."
    except subprocess.TimeoutExpired:
        error = "Speedtest did not complete within 180 seconds."
    except subprocess.CalledProcessError as exc:
        error = (
            f"Speedtest failed with exit code {exc.returncode}. "
            "Run speedtest interactively to check setup, license prompts, "
            "and connectivity."
        )
    except (ValueError, UnicodeError):
        error = (
            "Speedtest did not return valid completed-result JSON. "
            "Run speedtest interactively to check setup and license prompts."
        )
    else:
        measurement = {
            "timestamp": result.get("timestamp"),
            "ping": _select_fields(result.get("ping"), ("latency", "jitter", "low", "high")),
        }
        for direction in ("download", "upload"):
            data = result.get(direction)
            measurement[direction] = _select_fields(data, ("bandwidth", "bytes", "elapsed"))
            measurement[direction]["latency"] = _select_fields(
                data.get("latency") if isinstance(data, dict) else None,
                ("iqm", "jitter", "low", "high"),
            )
        measurement["packetLoss"] = result.get("packetLoss")
        measurement["server"] = _select_fields(result.get("server"), ("id", "name", "location"))
        return measurement

    raise RuntimeError(error)
