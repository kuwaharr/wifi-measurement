import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_measurement_probe import LOCATION, SPEEDTEST, WIFI

from src import storage as s


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="measurement space ")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "data #1.db"
        self.measurement = copy.deepcopy({"location": LOCATION, "wifi": WIFI, "speedtest": SPEEDTEST})
        s.initialize_database(self.path)

    def rows(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM measurements")]
        finally:
            connection.close()

    def execute(self, sql, parameters=()):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    def test_all_columns_roundtrip_append_and_reinitialize(self):
        record = s.prepare_measurement(self.measurement)
        self.assertEqual(len(record), 39)
        self.assertEqual(
            record,
            {
                "latitude": 40.42,
                "longitude": -86.91,
                "location_sent_at": "2026-11-04T12:00:00Z",
                "location_received_at": "2026-11-04T12:00:01Z",
                "location_type": "inside",
                "location_name": "Example Hall",
                "location_detail": "2nd floor",
                "attempt_count": 1,
                "wifi_ssid": "test",
                "wifi_ssid_hex": "74657374",
                "wifi_bssid": "01:23:45:67:89:ab",
                "wifi_signal_quality_percent": 80,
                "wifi_rssi_dbm": -51,
                "wifi_channel": 36,
                "wifi_frequency_khz": 5180000,
                "wifi_captured_at": "2026-11-04T12:00:02Z",
                "speedtest_timestamp": "2026-11-04T12:00:05Z",
                "idle_latency": 1.5,
                "idle_jitter": 0.2,
                "idle_latency_low": 1,
                "idle_latency_high": 2,
                "download_bandwidth": 12345,
                "download_bytes": 23456,
                "download_elapsed": 1000,
                "download_loaded_latency": 3.5,
                "download_loaded_jitter": 0.5,
                "download_latency_low": 2,
                "download_latency_high": 4,
                "upload_bandwidth": 5432,
                "upload_bytes": 6543,
                "upload_elapsed": 2000,
                "upload_loaded_latency": 4.5,
                "upload_loaded_jitter": 0.6,
                "upload_latency_low": 3,
                "upload_latency_high": 5,
                "packet_loss": 0,
                "server_id": 123,
                "server_name": "Server",
                "server_location": "City",
            },
        )
        self.assertEqual(s.save_measurement(record, self.path), 1)
        s.initialize_database(self.path)
        self.assertEqual(s.save_measurement(record, self.path), 2)
        self.assertEqual(self.rows(), [{"id": number, **record} for number in (1, 2)])
        self.assertTrue(all(value is not None for row in self.rows() for value in row.values()))
        self.assertEqual(self.execute("PRAGMA user_version"), [(1,)])
        columns = self.execute("PRAGMA table_info(measurements)")
        self.assertEqual(len(columns), 40)
        self.assertTrue(all(row[3] == 1 for row in columns))
        self.assertEqual(
            [(r[1], r[2], r[5]) for r in columns], [("id", "INTEGER", 1)] + [(c, t, 0) for c, _, t, *_ in s.FIELDS]
        )

    def test_every_source_missing_none_and_parent_types(self):
        for _, path, *_ in s.FIELDS:
            if path is None:
                continue
            parts = path.split(".")
            for missing in (True, False):
                data = copy.deepcopy(self.measurement)
                parent = data
                for part in parts[:-1]:
                    parent = parent[part]
                if missing:
                    del parent[parts[-1]]
                else:
                    parent[parts[-1]] = None
                with self.subTest(path=path, missing=missing), self.assertRaisesRegex(ValueError, path):
                    s.prepare_measurement(data)
            for index in range(len(parts) - 1):
                data = copy.deepcopy(self.measurement)
                parent = data
                for part in parts[:index]:
                    parent = parent[part]
                parent[parts[index]] = []
                with (
                    self.subTest(parent=parts[: index + 1]),
                    self.assertRaisesRegex(ValueError, "\\.".join(parts[: index + 1])),
                ):
                    s.prepare_measurement(data)
        self.assertEqual(self.rows(), [])

    def test_types_ranges_and_direct_record_validation(self):
        original = s.prepare_measurement(self.measurement)
        for column, _, kind, low, high in s.FIELDS:
            invalid = [None, True, [], {}]
            if kind != "TEXT":
                invalid += ["1", float("nan"), float("inf")]
                if low is not None:
                    invalid.append(low - 1)
                if high is not None:
                    invalid.append(high + 1)
            if kind == "INTEGER":
                invalid += [1.0, 2**63, -(2**63) - 1]
            for value in invalid:
                record = original | {column: value}
                with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                    s.save_measurement(record, self.path)
        for record in ({}, original | {"extra": 1}):
            with self.assertRaises(ValueError):
                s.save_measurement(record, self.path)
        self.assertEqual(self.rows(), [])

    def test_zero_raw_text_ssid_timestamps_and_no_mutation(self):
        data = self.measurement
        data["location"].update(latitude=0, longitude=0, location_name="建物 '); DROP TABLE measurements; --")
        data["wifi"].update(ssid="", ssid_hex="", signal_quality_percent=0)
        data["speedtest"]["server"]["name"] = " Server 日本 "
        data["speedtest"]["download"]["bandwidth"] = 0
        data["location"]["sent_at"] = "2026-11-04T07:00:00.123456-05:00"
        data["token"] = "not stored"
        data["attempt_count"] = 999
        before = copy.deepcopy(data)
        record = s.prepare_measurement(data)
        self.assertEqual(data, before)
        self.assertEqual(record["location_sent_at"], "2026-11-04T12:00:00.123456Z")
        self.assertEqual(record["location_received_at"], LOCATION["received_at"])
        self.assertEqual(record["attempt_count"], 1)
        self.assertNotIn("token", record)
        s.save_measurement(record, self.path)
        self.assertEqual(self.rows()[0], {"id": 1, **record})
        for column, value in (
            ("wifi_ssid_hex", "00"),
            ("wifi_ssid", "x" * 33),
            ("wifi_bssid", "bad"),
            ("location_name", "\0name"),
            ("location_detail", " " * 5),
            ("location_type", "unknown"),
            ("server_name", "  "),
            ("server_location", ""),
        ):
            with self.subTest(column=column), self.assertRaises(ValueError):
                s.save_measurement(record | {column: value}, self.path)
        for column in s.TIME_COLUMNS:
            for value in ("2026-11-04", "2026-11-04T12:00:00", "bad", "9999-12-31T23:59:59-12:00"):
                with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                    s.save_measurement(record | {column: value}, self.path)

    def test_database_constraints(self):
        record = s.prepare_measurement(self.measurement)
        for column in record:
            invalid = record | {column: None}
            with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                self.execute(
                    "INSERT INTO measurements ("
                    + ",".join(invalid)
                    + ") VALUES ("
                    + ",".join("?" for _ in invalid)
                    + ")",
                    list(invalid.values()),
                )
        for column, value in (
            ("latitude", 91),
            ("longitude", -181),
            ("location_type", "bad"),
            ("location_name", ""),
            ("location_detail", "x" * 501),
            ("attempt_count", 0),
        ):
            invalid = record | {column: value}
            with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                self.execute(
                    "INSERT INTO measurements ("
                    + ",".join(invalid)
                    + ") VALUES ("
                    + ",".join("?" for _ in invalid)
                    + ")",
                    list(invalid.values()),
                )
        self.assertEqual(self.rows(), [])

    def test_incompatible_versions_columns_empty_and_corrupt(self):
        self.execute("PRAGMA user_version=9")
        with self.assertRaises(ValueError):
            s.initialize_database(self.path)
        self.execute("PRAGMA user_version=1")
        self.execute("ALTER TABLE measurements ADD COLUMN surprise TEXT")
        with self.assertRaises(ValueError):
            s.initialize_database(self.path)
        other = self.path.with_name("other.db")
        sqlite3.connect(other).close()
        s.initialize_database(other)
        foreign = self.path.with_name("foreign.db")
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.close()
        with self.assertRaises(ValueError):
            s.initialize_database(foreign)
        corrupt = self.path.with_name("corrupt.db")
        corrupt.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            s.initialize_database(corrupt)

    def test_deleted_or_changed_database_is_not_recreated(self):
        record = s.prepare_measurement(self.measurement)
        self.path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            s.save_measurement(record, self.path)
        self.assertFalse(self.path.exists())
        s.initialize_database(self.path)
        self.execute("DROP TABLE measurements")
        with self.assertRaises(s.StorageError):
            s.save_measurement(record, self.path)

    def test_lock_failure_preserves_existing_row(self):
        record = s.prepare_measurement(self.measurement)
        s.save_measurement(record, self.path)
        blocker = sqlite3.connect(self.path)
        connect = sqlite3.connect

        def short_connect(*args, **kwargs):
            kwargs["timeout"] = 0.01
            return connect(*args, **kwargs)

        try:
            blocker.execute("BEGIN IMMEDIATE")
            with patch.object(s.sqlite3, "connect", side_effect=short_connect):
                with self.assertRaises(s.StorageError):
                    s.save_measurement(record, self.path)
                with self.assertRaises(sqlite3.OperationalError):
                    s.initialize_database(self.path)
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(len(self.rows()), 1)

    def test_commit_interrupt_failure_and_close_failure(self):
        record = s.prepare_measurement(self.measurement)
        connect = sqlite3.connect
        for failure in (RuntimeError("before commit"), KeyboardInterrupt(), OSError("commit IO")):
            connections = []

            class BrokenCommit(sqlite3.Connection):
                def commit(self):
                    raise failure

                def close(self):
                    super().close()
                    connections.append("closed")

            with patch.object(s.sqlite3, "connect", side_effect=lambda *a, **k: connect(*a, **k, factory=BrokenCommit)):
                with self.assertRaises((s.StorageError, KeyboardInterrupt)):
                    s.save_measurement(record, self.path)
            self.assertEqual(self.rows(), [])
            self.assertEqual(connections, ["closed"])
            s.save_measurement(record, self.path)
            self.execute("DELETE FROM measurements")

        class BrokenClose(sqlite3.Connection):
            def close(self):
                super().close()
                raise OSError("close IO")

        with patch.object(s.sqlite3, "connect", side_effect=lambda *a, **k: connect(*a, **k, factory=BrokenClose)):
            with self.assertRaises(s.StorageError) as error:
                s.save_measurement(record, self.path)
        self.assertEqual(error.exception.outcome, "saved")
        self.assertEqual(len(self.rows()), 1)

    def test_default_path_independent_of_working_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.path.parent)
            self.assertEqual(s.DEFAULT_DB_PATH, Path(s.__file__).resolve().parents[1] / "measurements.db")
        finally:
            os.chdir(previous)

    def test_insert_and_rollback_close_errors_preserve_original_cause(self):
        record = s.prepare_measurement(self.measurement)
        connect = sqlite3.connect

        class BrokenInsert(sqlite3.Connection):
            def execute(self, sql, *args):
                cursor = super().execute(sql, *args)
                if sql.startswith("INSERT"):
                    raise RuntimeError("original insert failure")
                return cursor

            def rollback(self):
                super().rollback()
                raise OSError("secondary rollback failure")

            def close(self):
                super().close()
                raise OSError("secondary close failure")

        with patch.object(s.sqlite3, "connect", side_effect=lambda *a, **k: connect(*a, **k, factory=BrokenInsert)):
            with self.assertRaisesRegex(s.StorageError, "original insert failure") as error:
                s.save_measurement(record, self.path)
        self.assertIsInstance(error.exception.__cause__, RuntimeError)
        self.assertEqual(error.exception.outcome, "unknown")
        self.assertEqual(self.rows(), [])
        s.save_measurement(record, self.path)
        self.assertEqual(len(self.rows()), 1)

    def test_initialization_ddl_is_rolled_back_on_interruption(self):
        path = self.path.with_name("interrupted.db")
        connect = sqlite3.connect

        class InterruptedDDL(sqlite3.Connection):
            def execute(self, sql, *args):
                cursor = super().execute(sql, *args)
                if sql.startswith("PRAGMA user_version=1"):
                    raise KeyboardInterrupt()
                return cursor

        with patch.object(s.sqlite3, "connect", side_effect=lambda *a, **k: connect(*a, **k, factory=InterruptedDDL)):
            with self.assertRaises(KeyboardInterrupt):
                s.initialize_database(path)
        connection = connect(path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (0,))
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        finally:
            connection.close()
        s.initialize_database(path)
