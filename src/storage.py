"""Validate and persist one complete raw measurement using SQLite."""

import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "measurements.db"

# Column, source path, SQL type, minimum, maximum. This is also the validation schema.
FIELDS = [
    ("latitude", "location.latitude", "REAL", -90, 90),
    ("longitude", "location.longitude", "REAL", -180, 180),
    ("location_sent_at", "location.sent_at", "TEXT", None, None),
    ("location_received_at", "location.received_at", "TEXT", None, None),
    ("location_type", "location.location_type", "TEXT", None, None),
    ("location_name", "location.location_name", "TEXT", None, None),
    ("location_detail", "location.location_detail", "TEXT", None, None),
    ("attempt_count", None, "INTEGER", 1, 1),
]
for name, kind, low, high in (
    ("ssid", "TEXT", None, None),
    ("ssid_hex", "TEXT", None, None),
    ("bssid", "TEXT", None, None),
    ("signal_quality_percent", "INTEGER", 0, 100),
    ("rssi_dbm", "INTEGER", None, None),
    ("channel", "INTEGER", 1, None),
    ("frequency_khz", "INTEGER", 1, None),
    ("captured_at", "TEXT", None, None),
):
    FIELDS.append((f"wifi_{name}", f"wifi.{name}", kind, low, high))
FIELDS.append(("speedtest_timestamp", "speedtest.timestamp", "TEXT", None, None))
for name in ("latency", "jitter", "low", "high"):
    column = {"latency": "latency", "jitter": "jitter", "low": "latency_low", "high": "latency_high"}[name]
    FIELDS.append((f"idle_{column}", f"speedtest.ping.{name}", "REAL", 0, None))
for direction in ("download", "upload"):
    for name in ("bandwidth", "bytes", "elapsed"):
        FIELDS.append((f"{direction}_{name}", f"speedtest.{direction}.{name}", "INTEGER", 0, None))
    for name, column in (
        ("iqm", "loaded_latency"),
        ("jitter", "loaded_jitter"),
        ("low", "latency_low"),
        ("high", "latency_high"),
    ):
        FIELDS.append((f"{direction}_{column}", f"speedtest.{direction}.latency.{name}", "REAL", 0, None))
FIELDS.extend(
    [
        ("packet_loss", "speedtest.packetLoss", "REAL", 0, 100),
        ("server_id", "speedtest.server.id", "INTEGER", 1, None),
        ("server_name", "speedtest.server.name", "TEXT", None, None),
        ("server_location", "speedtest.server.location", "TEXT", None, None),
    ]
)
TIME_COLUMNS = {"location_sent_at", "location_received_at", "wifi_captured_at", "speedtest_timestamp"}
CHECKS = (
    "CHECK (location_type IN ('inside', 'outside'))",
    "CHECK (latitude BETWEEN -90 AND 90)",
    "CHECK (longitude BETWEEN -180 AND 180)",
    "CHECK (length(trim(location_name)) BETWEEN 1 AND 200)",
    "CHECK (length(trim(location_detail)) BETWEEN 1 AND 500)",
    "CHECK (attempt_count >= 1)",
)


class StorageError(RuntimeError):
    """A storage failure with a known or uncertain persistence outcome."""

    def __init__(self, message, outcome="not saved", row_id=None):
        super().__init__(message)
        self.outcome = outcome
        self.row_id = row_id


def _validate_record(record):
    if not isinstance(record, dict) or set(record) != {field[0] for field in FIELDS}:
        raise ValueError("Measurement record has an invalid column set.")
    result = {}
    for column, path, kind, low, high in FIELDS:
        value = record[column]
        label = path or column
        try:
            if kind == "INTEGER":
                valid = type(value) is int and -(2**63) <= value < 2**63
            elif kind == "REAL":
                valid = type(value) in (int, float) and math.isfinite(value)
            else:
                valid = isinstance(value, str)
            if not valid or (low is not None and value < low) or (high is not None and value > high):
                raise ValueError("invalid type or range")
            if column in TIME_COLUMNS:
                # Require a date AND a time, rather than accepting date-only ISO variants.
                if not re.match(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d", value):
                    raise ValueError("expected ISO date and time")
                timestamp = datetime.fromisoformat(value)
                if timestamp.tzinfo is None:
                    raise ValueError("timezone required")
                value = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if column == "location_type" and value not in ("inside", "outside"):
                raise ValueError("expected inside or outside")
            if column in ("location_name", "location_detail"):
                limit = 200 if column == "location_name" else 500
                if not 1 <= len(value.strip()) <= limit or "\0" in value:
                    raise ValueError("invalid length or NUL")
            if column in ("server_name", "server_location") and not value.strip():
                raise ValueError("nonblank text required")
            if column == "wifi_bssid" and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", value):
                raise ValueError("expected six-byte BSSID")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{label}: {exc}") from exc
        result[column] = value
    try:
        ssid = result["wifi_ssid"].encode("utf-8")
        hex_value = result["wifi_ssid_hex"]
        if len(ssid) > 32 or not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", hex_value) or bytes.fromhex(hex_value) != ssid:
            raise ValueError("SSID bytes and hex must match (maximum 32 bytes)")
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"wifi.ssid / wifi.ssid_hex: {exc}") from exc
    return result


def prepare_measurement(measurement) -> dict:
    """Return validated columns without modifying the acquired dictionaries."""
    record = {}
    for column, path, *_ in FIELDS:
        value = measurement
        if path is None:
            value = 1
        else:
            for part in path.split("."):
                if not isinstance(value, dict) or part not in value:
                    raise ValueError(f"{path}: missing value or invalid parent dictionary")
                value = value[part]
        record[column] = value
    return _validate_record(record)


def _check_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    columns = connection.execute("PRAGMA table_info(measurements)").fetchall()
    expected = [("id", "INTEGER", 1, 1)] + [(c, t, 1, 0) for c, _, t, *_ in FIELDS]
    actual = [(row[1], row[2].upper(), row[3], row[5]) for row in columns]
    if version != 1 or actual != expected:
        raise ValueError("Incompatible measurement database schema or user_version.")


def _close(connection, original_error=False):
    try:
        connection.close()
    except BaseException:
        if not original_error:
            raise


def initialize_database(path=DEFAULT_DB_PATH) -> None:
    """Prepare a compatible database and release its write lock before acquisition."""
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if version == 0 and not tables:
            columns = ["id INTEGER PRIMARY KEY NOT NULL"] + [f"{c} {t} NOT NULL" for c, _, t, *_ in FIELDS]
            connection.execute("CREATE TABLE measurements (" + ", ".join(columns + list(CHECKS)) + ")")
            connection.execute("PRAGMA user_version=1")
        _check_schema(connection)
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        _close(connection, original_error=True)
        raise
    _close(connection)


def save_measurement(record, path=DEFAULT_DB_PATH) -> int:
    """Insert once; report persistence outcome even when commit or close fails."""
    record = _validate_record(record)
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=5.0)
    committing = False
    committed = False
    row_id = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        _check_schema(connection)
        columns = list(record)
        cursor = connection.execute(
            "INSERT INTO measurements (" + ", ".join(columns) + ") VALUES (" + ", ".join("?" for _ in columns) + ")",
            [record[column] for column in columns],
        )
        row_id = cursor.lastrowid
        committing = True
        connection.commit()
        committed = True
    except BaseException as exc:
        outcome = "unknown" if committing else "not saved"
        try:
            connection.rollback()
            if isinstance(exc, sqlite3.Error) and getattr(exc, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
                sqlite3.SQLITE_CONSTRAINT,
            ):
                outcome = "not saved"
        except BaseException:
            outcome = "unknown"
        _close(connection, original_error=True)
        if isinstance(exc, KeyboardInterrupt):
            # Keep interruption semantics while carrying the commit outcome to the caller.
            exc.outcome = outcome
            raise
        raise StorageError(str(exc), outcome) from exc
    try:
        _close(connection)
    except KeyboardInterrupt as exc:
        exc.outcome = "saved"
        raise
    except Exception as exc:
        raise StorageError(str(exc), "saved" if committed else "unknown", row_id) from exc
    return row_id
