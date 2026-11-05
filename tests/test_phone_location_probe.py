import http.client
import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from scripts.phone_location_probe import Handler, main, validate_location


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.token = "test-session"
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.02})
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method="POST", path="/location?token=test-session", body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body, headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def location(self):
        return {"latitude": 40.42, "longitude": -86.91, "sent_at": "2026-11-04T12:00:00Z"}

    def test_success_prints_only_selected_fields_and_server_timestamp(self):
        data = self.location() | {"received_at": "fake", "unwanted": "discard"}
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status, result = self.request(body=json.dumps(data))
        self.assertEqual((status, result), (200, {"status": "ok"}))
        printed = json.loads(output.getvalue())
        self.assertEqual(printed["latitude"], data["latitude"])
        self.assertEqual(set(printed), {"latitude", "longitude", "sent_at", "received_at"})
        self.assertNotIn("unwanted", printed)
        self.assertEqual(datetime.fromisoformat(printed["received_at"]).utcoffset(), timedelta(0))
        self.assertNotIn("test-session", errors.getvalue())

    def test_invalid_location_values_do_not_print_success(self):
        invalid = [
            [],
            None,
            {},
            self.location() | {"latitude": True},
            self.location() | {"latitude": 91},
            self.location() | {"longitude": -181},
            self.location() | {"latitude": float("nan")},
            self.location() | {"longitude": float("inf")},
            self.location() | {"sent_at": None},
            self.location() | {"sent_at": "2026-11-04T12:00:00"},
        ]
        for data in invalid:
            with self.subTest(data=data), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.request(body=json.dumps(data))[0], 400)
                self.assertEqual(output.getvalue(), "")

    def test_bad_json_and_size_limit(self):
        for body in (b"{", b"\xff", b"[" * 2000):
            with self.subTest(body=body[:10]):
                self.assertEqual(self.request(body=body)[0], 400)
        # Send only the oversized length: the server rejects it before reading a body.
        self.assertEqual(
            self.request(
                body=b"",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "65537",
                },
            )[0],
            413,
        )

    def test_validation_response_identifies_field_and_reason(self):
        cases = [
            ([], None, "Location must be a JSON object."),
            ({}, "latitude", "Missing required field: latitude."),
            ({"latitude": 40.42}, "longitude", "Missing required field: longitude."),
            ({"latitude": 40.42, "longitude": -86.91}, "sent_at", "Missing required field: sent_at."),
            (self.location() | {"latitude": 91}, "latitude", "latitude must be between -90 and 90."),
            (self.location() | {"longitude": -181}, "longitude", "longitude must be between -180 and 180."),
            (
                self.location() | {"sent_at": None},
                "sent_at",
                "sent_at must be a JSON string containing an ISO 8601 datetime with a timezone.",
            ),
            (
                self.location() | {"sent_at": "not-a-date"},
                "sent_at",
                "sent_at has an invalid ISO 8601 datetime format. Example: 2026-11-04T12:00:00Z.",
            ),
            (
                self.location() | {"sent_at": "2026-11-04T12:00:00"},
                "sent_at",
                "sent_at is missing a timezone. Use Z or an offset such as -05:00.",
            ),
        ]
        for data, field, message in cases:
            with self.subTest(data=data), redirect_stdout(io.StringIO()) as output:
                expected = {"stage": "validation", "error": message}
                if field is not None:
                    expected["field"] = field
                self.assertEqual(self.request(body=json.dumps(data)), (400, expected))
                self.assertEqual(output.getvalue(), "")

    def test_coordinate_strings_are_normalized_to_float(self):
        for latitude, longitude in (
            ("40.42", "-86.91"),
            (" 40.42 ", "\t-86.91\n"),
            ("-90", "-180"),
            ("90", "180"),
            ("0", "0"),
            (0, 0),
            ("4e1", "-8e1"),
        ):
            with self.subTest(latitude=latitude, longitude=longitude), redirect_stdout(io.StringIO()) as output:
                status, result = self.request(
                    body=json.dumps(
                        self.location()
                        | {
                            "latitude": latitude,
                            "longitude": longitude,
                        }
                    )
                )
                self.assertEqual((status, result), (200, {"status": "ok"}))
                printed = json.loads(output.getvalue())
                for field, value in (("latitude", latitude), ("longitude", longitude)):
                    self.assertIsInstance(printed[field], float)
                    self.assertEqual(printed[field], float(value))
                self.assertEqual(set(printed), {"latitude", "longitude", "sent_at", "received_at"})

    def test_coordinate_conversion_failures_report_field(self):
        for field in ("latitude", "longitude"):
            for value in ("", "  ", "abc", "40.42°N", "40,42", True, False, None, [], {}, 10**400):
                with self.subTest(field=field, value=value), redirect_stdout(io.StringIO()) as output:
                    status, response = self.request(body=json.dumps(self.location() | {field: value}))
                    self.assertEqual(status, 400)
                    self.assertEqual(response["stage"], "conversion")
                    self.assertEqual(response["field"], field)
                    self.assertIn(field, response["error"])
                    self.assertEqual(output.getvalue(), "")

    def test_converted_coordinates_are_checked_for_finiteness_and_range(self):
        for field, outside_range in (("latitude", ("-90.01", "90.01")), ("longitude", ("-180.01", "180.01"))):
            for value in ("NaN", "Infinity", "-Infinity", "1e999", *outside_range):
                with self.subTest(field=field, value=value), redirect_stdout(io.StringIO()) as output:
                    status, response = self.request(body=json.dumps(self.location() | {field: value}))
                    self.assertEqual(status, 400)
                    self.assertEqual(response["stage"], "validation")
                    self.assertEqual(response["field"], field)
                    self.assertEqual(output.getvalue(), "")

    def test_conversion_stops_at_first_invalid_field(self):
        status, response = self.request(body=json.dumps(self.location() | {"latitude": "bad", "longitude": "bad"}))
        self.assertEqual(status, 400)
        self.assertEqual(response["stage"], "conversion")
        self.assertEqual(response["field"], "latitude")

    def test_json_decode_response_identifies_failure(self):
        cases = [
            (b"{", "Invalid JSON syntax at line 1, column 2:"),
            (b"\xff", "Request body has an invalid JSON text encoding."),
            (b"[" * 30000, "JSON nesting is too deep."),
            (b"NaN", "Invalid JSON number."),
            (b"Infinity", "Invalid JSON number."),
            (b"-Infinity", "Invalid JSON number."),
        ]
        for body, message in cases:
            with self.subTest(body=body[:10]):
                status, response = self.request(body=body)
                self.assertEqual(status, 400)
                self.assertEqual(response["stage"], "json_decode")
                self.assertTrue(response["error"].startswith(message), response)

    def test_overflowed_json_number_reports_field(self):
        body = b'{"latitude": 1e999, "longitude": 0, "sent_at": "2026-11-04T12:00:00Z"}'
        self.assertEqual(
            self.request(body=body),
            (400, {"stage": "validation", "field": "latitude", "error": "latitude must be finite."}),
        )

    def test_missing_wrong_duplicate_and_non_ascii_tokens(self):
        for path in (
            "/location",
            "/location?token=wrong",
            "/location?token=",
            "/location?token=test-session&token=wrong",
            "/location?token=%E3%81%82",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path, body="")[0], 403)

    def test_ready_and_not_found(self):
        self.assertEqual(self.request("GET", "/location")[1]["status"], "ready")
        self.assertEqual(self.request("GET", "/location?token=test-session")[0], 200)
        self.assertEqual(self.request("GET", "/other")[0], 404)
        self.assertEqual(self.request(path="/other", body="")[0], 404)

    def test_content_type_and_framing(self):
        self.assertEqual(self.request(body="", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(
            self.request(body="", headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"})[0],
            400,
        )
        self.assertEqual(
            self.request(body="", headers={"Content-Type": "application/json", "Content-Length": "-1"})[0], 400
        )

    def test_boundaries_and_utc_format(self):
        for latitude, longitude in ((-90, -180), (90, 180)):
            data = self.location() | {
                "latitude": latitude,
                "longitude": longitude,
                "sent_at": "2026-11-04T12:00:00+00:00",
            }
            self.assertEqual(validate_location(data)["sent_at"], "2026-11-04T12:00:00Z")

    def test_timezone_offsets_are_converted_to_utc_in_received_output(self):
        cases = [
            ("2026-11-04T07:00:00-05:00", "2026-11-04T12:00:00Z"),
            ("2026-11-04T21:00:00+09:00", "2026-11-04T12:00:00Z"),
            ("2026-11-04T17:30:00+05:30", "2026-11-04T12:00:00Z"),
            ("2026-11-04T23:30:00-05:00", "2026-11-05T04:30:00Z"),
            ("2026-11-04T00:30:00+09:00", "2026-11-03T15:30:00Z"),
            ("2026-11-04T07:00:00.123456-05:00", "2026-11-04T12:00:00.123456Z"),
            ("2026-11-04T12:00:00Z", "2026-11-04T12:00:00Z"),
        ]
        for sent_at, expected in cases:
            with self.subTest(sent_at=sent_at), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    self.request(body=json.dumps(self.location() | {"sent_at": sent_at})), (200, {"status": "ok"})
                )
                self.assertEqual(json.loads(output.getvalue())["sent_at"], expected)

    def test_utc_conversion_overflow_returns_validation_error(self):
        for sent_at in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            with self.subTest(sent_at=sent_at), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    self.request(body=json.dumps(self.location() | {"sent_at": sent_at})),
                    (
                        400,
                        {
                            "stage": "validation",
                            "field": "sent_at",
                            "error": "sent_at is outside the supported datetime range after UTC conversion.",
                        },
                    ),
                )
                self.assertEqual(output.getvalue(), "")

    def test_port_in_use_reports_failure(self):
        with (
            patch("scripts.phone_location_probe.ThreadingHTTPServer", side_effect=OSError("Address in use")),
            patch("scripts.phone_location_probe.run_tunnel") as tunnel,
            redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(main(), 1)
        self.assertIn("port 8765", errors.getvalue())
        tunnel.assert_not_called()

    def test_interrupt_closes_receiver(self):
        with (
            patch("scripts.phone_location_probe.ThreadingHTTPServer") as server_type,
            patch("scripts.phone_location_probe.threading.Thread") as thread_type,
            patch("scripts.phone_location_probe.run_tunnel", side_effect=KeyboardInterrupt) as tunnel,
            redirect_stdout(io.StringIO()) as output,
        ):
            server = server_type.return_value
            self.assertEqual(main(), 0)
        server.__exit__.assert_called_once()
        tunnel.assert_called_once_with(server.token)
        server.shutdown.assert_called_once()
        thread_type.return_value.join.assert_called_once()
        self.assertIn("Stopped listening", output.getvalue())


class MainLifecycleTests(unittest.TestCase):
    def test_automatic_start_receives_json_and_releases_port_on_exit(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                servers = []

                def make_server(address, handler):
                    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                    servers.append(server)
                    return server

                def tunnel(token):
                    server = servers[0]
                    self.assertEqual(token, server.token)
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    try:
                        connection.request(
                            "POST",
                            f"/location?token={token}",
                            json.dumps(
                                {
                                    "latitude": "40.42",
                                    "longitude": "-86.91",
                                    "sent_at": "2026-11-04T07:00:00-05:00",
                                }
                            ),
                            {"Content-Type": "application/json"},
                        )
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read()), {"status": "ok"})
                    finally:
                        connection.close()
                    return exit_code

                with (
                    patch("scripts.phone_location_probe.ThreadingHTTPServer", side_effect=make_server),
                    patch("scripts.phone_location_probe.run_tunnel", side_effect=tunnel),
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(main(), exit_code)
                self.assertIn('"sent_at": "2026-11-04T12:00:00Z"', output.getvalue())
                self.assertEqual(servers[0].socket.fileno(), -1)
                with ThreadingHTTPServer(servers[0].server_address, Handler):
                    pass

    def test_unexpected_tunnel_exception_closes_server_and_joins_thread(self):
        with (
            patch("scripts.phone_location_probe.ThreadingHTTPServer") as server_type,
            patch("scripts.phone_location_probe.threading.Thread") as thread_type,
            patch("scripts.phone_location_probe.run_tunnel", side_effect=RuntimeError("failed")),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(RuntimeError):
                main()
        server_type.return_value.shutdown.assert_called_once()
        server_type.return_value.__exit__.assert_called_once()
        thread_type.return_value.join.assert_called_once()


if __name__ == "__main__":
    unittest.main()
