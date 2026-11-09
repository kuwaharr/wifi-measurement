import http.client
import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from scripts import measurement_probe as probe

LOCATION = {
    "latitude": 40.42,
    "longitude": -86.91,
    "sent_at": "2026-11-04T12:00:00Z",
    "received_at": "2026-11-04T12:00:01Z",
}
SPEEDTEST = {"timestamp": "2026-11-04T12:00:05Z", "download": {"bandwidth": 12345}, "packetLoss": None}


class InboxTests(unittest.TestCase):
    def test_deadline_starts_at_qr_and_expires_at_boundary(self):
        inbox = probe.LocationInbox()
        with patch.object(probe.time, "monotonic", return_value=100):
            self.assertIsNone(inbox.check())
            self.assertIsNone(inbox.deadline)
            inbox.on_ready()
            self.assertEqual(inbox.deadline, 280)
        with patch.object(probe.time, "monotonic", return_value=279.999):
            self.assertIsNone(inbox.check())
        with patch.object(probe.time, "monotonic", return_value=280):
            self.assertFalse(inbox.accept(LOCATION))
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                inbox.check()

    def test_before_boundary_and_early_acceptance_are_retained(self):
        for early in (False, True):
            with self.subTest(early=early):
                inbox = probe.LocationInbox()
                with patch.object(probe.time, "monotonic", return_value=100):
                    if not early:
                        inbox.on_ready()
                    self.assertTrue(inbox.accept(LOCATION))
                    inbox.on_ready()
                with patch.object(probe.time, "monotonic", return_value=1000):
                    self.assertEqual(inbox.check(), LOCATION)
                self.assertFalse(inbox.accept(LOCATION | {"latitude": 0}))

    def test_concurrent_accepts_choose_exactly_one(self):
        inbox = probe.LocationInbox()
        barrier = threading.Barrier(3)
        accepted = []

        def send(latitude):
            barrier.wait(timeout=2)
            accepted.append(inbox.accept(LOCATION | {"latitude": latitude}))

        threads = [threading.Thread(target=send, args=(value,)) for value in (1, 2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(sorted(accepted), [False, True])
        self.assertIn(inbox.check()["latitude"], (1, 2))


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.wifi_patch = patch.object(probe, "get_wifi_info", return_value={"ssid": "test", "rssi_dbm": -51})
        self.wifi = self.wifi_patch.start()
        self.addCleanup(self.wifi_patch.stop)
        self.servers = []
        self.stops = []
        self.output = io.StringIO()
        self.errors = io.StringIO()

    def make_server(self, address, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.servers.append(server)
        return server

    def post(self, data):
        server = self.servers[0]
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request(
                "POST", f"/location?token={server.token}", json.dumps(data), {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def good_post(self):
        return self.post({"latitude": "40.42", "longitude": "-86.91", "sent_at": "2026-11-04T07:00:00-05:00"})

    def successful_tunnel(self, token, *, stop_event, on_ready):
        self.stops.append(stop_event)
        self.assertEqual(token, self.servers[0].token)
        on_ready()
        self.assertEqual(self.good_post(), (200, {"status": "ok"}))
        if not stop_event.wait(3):
            raise RuntimeError("Test tunnel was not stopped")
        return 0

    def run_main(self, tunnel=None, speedtest=None, argv=()):
        self.servers = []
        self.stops = []
        with (
            patch.object(probe, "ThreadingHTTPServer", side_effect=self.make_server),
            patch.object(probe, "run_tunnel", side_effect=tunnel or self.successful_tunnel),
            patch.object(probe, "run_speedtest", side_effect=speedtest) as speed,
            redirect_stdout(self.output),
            redirect_stderr(self.errors),
        ):
            if speedtest is None:
                speed.return_value = SPEEDTEST
            code = probe.main(argv)
        for server in self.servers:
            self.assertEqual(server.socket.fileno(), -1)
            with ThreadingHTTPServer(server.server_address, probe.Handler):
                pass
        for stop in self.stops:
            self.assertTrue(stop.is_set())
        return code, speed

    def combined(self):
        text = self.output.getvalue()
        return json.loads(text[text.index("{\n") :])

    def test_success_http_response_and_full_result(self):
        def speedtest():
            self.assertTrue(self.servers[0].on_location_responded.__self__.is_set())
            return SPEEDTEST

        code, speed = self.run_main(speedtest=speedtest)
        self.assertEqual(code, 0)
        speed.assert_called_once_with()
        result = self.combined()
        self.assertEqual(result["speedtest"], SPEEDTEST)
        self.assertEqual(result["location"]["sent_at"], LOCATION["sent_at"])
        self.assertEqual(result["location"]["latitude"], 40.42)
        self.assertEqual(set(result), {"location", "wifi", "speedtest"})
        self.assertEqual(result["wifi"], self.wifi.return_value)
        self.assertEqual(self.output.getvalue().count('"location":'), 1)
        self.assertNotIn(self.servers[0].token, json.dumps(result))

    def test_browser_json_follows_existing_measurement_flow(self):
        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            on_ready()
            self.assertEqual(
                self.post({"latitude": 40.42, "longitude": -86.91, "sent_at": "2026-11-07T12:00:00.000Z"}),
                (200, {"status": "ok"}),
            )
            stop_event.wait(3)
            return 0

        code, speed = self.run_main(tunnel=tunnel, argv=())
        self.assertEqual(code, 0)
        self.wifi.assert_called_once()
        speed.assert_called_once()
        result = self.combined()
        self.assertEqual(result["location"]["latitude"], 40.42)
        self.assertEqual(set(result), {"location", "wifi", "speedtest"})

    def test_wifi_runs_after_response_and_before_speedtest(self):
        order = []

        def wifi():
            self.assertTrue(self.servers[0].on_location_responded.__self__.is_set())
            order.append("wifi")
            return {"ssid": "test"}

        def speedtest():
            order.append("speedtest")
            return SPEEDTEST

        self.wifi.side_effect = wifi
        code, speed = self.run_main(speedtest=speedtest)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["wifi", "speedtest"])
        self.wifi.assert_called_once()
        self.assertEqual(self.combined()["wifi"], {"ssid": "test"})

    def test_wifi_failures_and_interrupt_do_not_run_speedtest(self):
        for error, code in (
            (RuntimeError("Multiple adapters"), 1),
            (RuntimeError("Access denied"), 1),
            (KeyboardInterrupt(), 130),
        ):
            with self.subTest(error=error):
                self.output = io.StringIO()
                self.wifi.side_effect = error
                actual, speed = self.run_main()
                self.assertEqual(actual, code)
                speed.assert_not_called()
                self.assertNotIn('"location":', self.output.getvalue())

    def test_invalid_then_valid_and_duplicate_http(self):
        statuses = []

        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            on_ready()
            statuses.append(self.post({})[0])
            statuses.append(self.good_post()[0])
            statuses.append(self.good_post()[0])
            stop_event.wait(3)
            return 0

        def speedtest():
            # Wait for the duplicate request to finish before cleanup.
            for _ in range(100):
                if len(statuses) == 3:
                    return SPEEDTEST
                threading.Event().wait(0.005)
            raise RuntimeError("Duplicate request did not complete")

        code, speed = self.run_main(tunnel, speedtest)
        self.assertEqual(code, 0)
        self.assertEqual(statuses, [400, 200, 409])
        speed.assert_called_once()

    def test_timeout_does_not_run_speedtest(self):
        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            on_ready()
            stop_event.wait(3)
            return 0

        with patch.object(probe, "LOCATION_TIMEOUT", 0):
            code, speed = self.run_main(tunnel)
        self.assertEqual(code, 1)
        speed.assert_not_called()
        self.wifi.assert_not_called()
        self.assertIn("Timed out", self.errors.getvalue())

    def test_tunnel_exit_or_exception_before_acceptance_fails(self):
        for error in (None, RuntimeError("tunnel failed")):
            with self.subTest(error=error):

                def tunnel(token, *, stop_event, on_ready):
                    self.stops.append(stop_event)
                    if error:
                        raise error
                    return 1

                code, speed = self.run_main(tunnel)
                self.assertEqual(code, 1)
                speed.assert_not_called()

    def test_tunnel_exit_after_acceptance_warns_but_succeeds(self):
        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            on_ready()
            self.assertEqual(self.good_post()[0], 200)
            return 1

        code, speed = self.run_main(tunnel)
        self.assertEqual(code, 0)
        speed.assert_called_once()
        self.assertIn("Warning", self.errors.getvalue())

    def test_speedtest_error_and_interrupt_do_not_output_json(self):
        for error, expected in ((RuntimeError("speedtest failed"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=error):
                self.output = io.StringIO()
                code, speed = self.run_main(speedtest=Mock(side_effect=error))
                self.assertEqual(code, expected)
                speed.assert_called_once()
                self.assertNotIn('"location":', self.output.getvalue())

    def test_serialization_failure_is_not_success(self):
        code, speed = self.run_main(speedtest=lambda: {"download": float("nan")})
        self.assertEqual(code, 1)
        self.assertNotIn('"location":', self.output.getvalue())

    def test_bind_failure_does_not_start_tunnel_or_speedtest(self):
        with (
            patch.object(probe, "ThreadingHTTPServer", side_effect=OSError("Port in use")),
            patch.object(probe, "run_tunnel") as tunnel,
            patch.object(probe, "run_speedtest") as speed,
            redirect_stderr(self.errors),
        ):
            self.assertEqual(probe.main(), 1)
        tunnel.assert_not_called()
        speed.assert_not_called()

    def test_interrupt_while_waiting_stops_tunnel_and_skips_speedtest(self):
        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            stop_event.wait(3)
            return 0

        with patch.object(probe.LocationInbox, "check", side_effect=KeyboardInterrupt):
            code, speed = self.run_main(tunnel)
        self.assertEqual(code, 130)
        speed.assert_not_called()
        self.assertNotIn('"location":', self.output.getvalue())

    def test_partial_thread_start_failure_releases_resources(self):
        original_start = threading.Thread.start
        for failed_name in ("measurement-http", "measurement-tunnel"):

            def start(thread):
                if thread.name == failed_name:
                    raise RuntimeError("Thread start failed")
                return original_start(thread)

            with self.subTest(failed_name=failed_name), patch.object(threading.Thread, "start", start):
                code, speed = self.run_main()
                self.assertEqual(code, 1)
                speed.assert_not_called()

    def test_cleanup_error_suppresses_success_json(self):
        original_close = ThreadingHTTPServer.server_close

        def close(server):
            original_close(server)
            raise OSError("close failed")

        with patch.object(ThreadingHTTPServer, "server_close", close):
            # run_main additionally checks port reuse with a context manager,
            # so call main directly here and check its server independently.
            with (
                patch.object(probe, "ThreadingHTTPServer", side_effect=self.make_server),
                patch.object(probe, "run_tunnel", side_effect=self.successful_tunnel),
                patch.object(probe, "run_speedtest", return_value=SPEEDTEST),
                redirect_stdout(self.output),
                redirect_stderr(self.errors),
            ):
                self.assertEqual(probe.main(), 1)
        self.assertNotIn('"location":', self.output.getvalue())
        self.assertIn("Cleanup failed", self.errors.getvalue())
        self.assertTrue(self.stops[0].is_set())

    def test_two_simultaneous_posts_accept_only_one(self):
        completed = threading.Event()
        statuses = []

        def tunnel(token, *, stop_event, on_ready):
            self.stops.append(stop_event)
            on_ready()
            barrier = threading.Barrier(3)

            def send(latitude):
                barrier.wait(timeout=2)
                statuses.append(
                    self.post({"latitude": latitude, "longitude": "-86.91", "sent_at": LOCATION["sent_at"]})[0]
                )

            workers = [threading.Thread(target=send, args=(value,)) for value in ("1", "2")]
            try:
                for worker in workers:
                    worker.start()
                barrier.wait(timeout=2)
                for worker in workers:
                    worker.join(timeout=2)
                completed.set()
                stop_event.wait(3)
                return 0
            finally:
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(timeout=2)

        def speedtest():
            if not completed.wait(2):
                raise RuntimeError("Concurrent posts did not finish")
            return SPEEDTEST

        code, speed = self.run_main(tunnel, speedtest)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(statuses), [200, 409])
        speed.assert_called_once()
        self.assertIn(self.combined()["location"]["latitude"], (1, 2))


if __name__ == "__main__":
    unittest.main()
