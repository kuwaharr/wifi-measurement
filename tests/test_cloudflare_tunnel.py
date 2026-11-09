import io
import subprocess
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from scripts.cloudflare_tunnel import build_web_location_url, extract_public_url, main, print_qr_code, run_tunnel


class TunnelTests(unittest.TestCase):
    def test_stop_before_start_does_not_spawn(self):
        stop = threading.Event()
        stop.set()
        ready = Mock()
        with patch("scripts.cloudflare_tunnel.subprocess.Popen") as popen:
            self.assertEqual(run_tunnel("test", stop_event=stop, on_ready=ready), 0)
        popen.assert_not_called()
        ready.assert_not_called()

    def test_stop_after_qr_calls_ready_once_and_cleans_up(self):
        stop = threading.Event()
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        order = []

        def ready():
            order.append("ready")
            stop.set()

        with (
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            patch("scripts.cloudflare_tunnel.print_qr_code", side_effect=lambda url: order.append("qr")),
            redirect_stdout(io.StringIO()),
        ):
            queue_type.return_value.get.return_value = "INF | https://quiet-otter.trycloudflare.com |"
            self.assertEqual(run_tunnel("test", stop_event=stop, on_ready=ready), 0)
        self.assertEqual(order, ["qr", "ready"])
        process.terminate.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_stop_during_url_wait_cleans_up_without_ready(self):
        stop = threading.Event()
        ready = Mock()
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None

        def line(**kwargs):
            stop.set()
            return "INF still connecting"

        with (
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            redirect_stdout(io.StringIO()),
        ):
            queue_type.return_value.get.side_effect = line
            self.assertEqual(run_tunnel("test", stop_event=stop, on_ready=ready), 0)
        ready.assert_not_called()
        process.terminate.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_ready_exception_is_reported_and_cleaned_up(self):
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        with (
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            patch("scripts.cloudflare_tunnel.print_qr_code"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            queue_type.return_value.get.return_value = "INF | https://quiet-otter.trycloudflare.com |"
            self.assertEqual(run_tunnel("test", on_ready=Mock(side_effect=RuntimeError("ready failed"))), 1)
        process.terminate.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_cli_forwards_token_to_callable(self):
        with (
            patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test-session"]),
            patch("scripts.cloudflare_tunnel.run_tunnel", return_value=1) as tunnel,
        ):
            self.assertEqual(main(), 1)
        tunnel.assert_called_once_with("test-session")

    def test_url_timeout_terminates_process(self):
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        with (
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.time.monotonic", side_effect=[0, 61]),
            patch("sys.stderr", new_callable=io.StringIO) as errors,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_tunnel("test-session"), 1)
        self.assertIn("within 60 seconds", errors.getvalue())
        process.terminate.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_interrupt_displays_matching_token_and_kills_unresponsive_process(self):
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("cloudflared", 5), 0]
        with (
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            patch("scripts.cloudflare_tunnel.print_qr_code") as qr,
            redirect_stdout(io.StringIO()),
        ):
            queue_type.return_value.get.side_effect = [
                "INF | https://quiet-otter.trycloudflare.com |",
                KeyboardInterrupt,
            ]
            self.assertEqual(run_tunnel("test-session"), 0)
        qr.assert_called_once_with("https://quiet-otter.trycloudflare.com/phone-location?token=test-session")
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_extract_url_from_cloudflared_logs(self):
        self.assertEqual(
            extract_public_url("INF | https://quiet-otter.trycloudflare.com |"), "https://quiet-otter.trycloudflare.com"
        )
        for line in (
            "not ready",
            "http://quiet-otter.trycloudflare.com",
            "https://quiet-otter.trycloudflare.com.evil.example",
        ):
            self.assertIsNone(extract_public_url(line))

    def test_build_url_preserves_token(self):
        url = urlsplit(build_web_location_url("https://quiet-otter.trycloudflare.com", "a+b /?"))
        self.assertEqual(url.path, "/phone-location")
        self.assertEqual(parse_qs(url.query), {"token": ["a+b /?"]})

    def test_reject_invalid_base_url_or_empty_token(self):
        for url, token in (
            ("https://example.com", "token"),
            ("https://a.trycloudflare.com/location", "token"),
            ("https://a.trycloudflare.com", ""),
        ):
            with self.subTest(url=url, token=token), self.assertRaises(ValueError):
                build_web_location_url(url, token)

    def test_real_qr_can_encode_full_destination(self):
        url = build_web_location_url("https://quiet-otter.trycloudflare.com", "a" * 32)
        output = io.StringIO()
        with redirect_stdout(output):
            print_qr_code(url)
        self.assertGreater(len(output.getvalue().splitlines()), 10)
        self.assertNotIn("\x1b", output.getvalue())

    def test_missing_executable_reports_failure(self):
        with (
            patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test-session"]),
            patch("scripts.cloudflare_tunnel.subprocess.Popen", side_effect=FileNotFoundError),
            patch("sys.stderr", new_callable=io.StringIO) as errors,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 1)
        self.assertIn("Could not start cloudflared", errors.getvalue())

    def test_interrupt_stops_tunnel_process(self):
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        with (
            patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test-session"]),
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            redirect_stdout(io.StringIO()),
        ):
            queue_type.return_value.get.side_effect = [
                "INF | https://quiet-otter.trycloudflare.com |",
                KeyboardInterrupt,
            ]
            self.assertEqual(main(), 0)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        self.assertTrue(process.stdout.closed)

    def test_tunnel_exit_reports_failure(self):
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = 1
        process.wait.return_value = 1
        with (
            patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test-session"]),
            patch("scripts.cloudflare_tunnel.subprocess.Popen", return_value=process),
            patch("scripts.cloudflare_tunnel.queue.Queue") as queue_type,
            patch("sys.stderr", new_callable=io.StringIO) as errors,
            redirect_stdout(io.StringIO()),
        ):
            queue_type.return_value.get.return_value = None
            self.assertEqual(main(), 1)
        self.assertIn("stopped unexpectedly", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
