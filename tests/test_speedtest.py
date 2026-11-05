import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from src.speedtest import run_speedtest


class SpeedtestTests(unittest.TestCase):
    def test_preserves_selected_raw_values_and_omits_optional_metadata(self):
        latency = {"iqm": 12.34, "jitter": 1.23, "low": 10.2, "high": 20.3}
        transfer = {"bandwidth": 12345678, "bytes": 987654321, "elapsed": 1234, "latency": latency}
        raw = {
            "type": "result",
            "timestamp": "2026-11-04T12:00:00Z",
            "ping": {"latency": 5.67, "jitter": 0.12, "low": 4.5, "high": 7.8},
            "download": transfer,
            "upload": transfer | {"bandwidth": 7654321},
            "packetLoss": 0.2,
            "server": {"id": 123, "name": "Test", "location": "City", "host": "discard"},
            "isp": "discard",
            "interface": {"externalIp": "discard"},
            "result": {"id": "discard", "url": "discard"},
        }
        expected = {key: raw[key] for key in ("timestamp", "ping", "download", "upload", "packetLoss")}
        expected["server"] = {"id": 123, "name": "Test", "location": "City"}
        with patch("src.speedtest.subprocess.run", return_value=Mock(stdout=json.dumps(raw))) as run:
            self.assertEqual(run_speedtest(), expected)
        self.assertEqual(run.call_args.args[0], ["speedtest", "--format=json", "--progress=no"])
        self.assertEqual(run.call_args.kwargs["timeout"], 180)

    def test_missing_values_remain_none(self):
        with patch("src.speedtest.subprocess.run", return_value=Mock(stdout='{"type":"result"}')):
            result = run_speedtest()
        self.assertIsNone(result["timestamp"])
        self.assertEqual(result["ping"], dict.fromkeys(("latency", "jitter", "low", "high")))
        for direction in ("download", "upload"):
            self.assertEqual(
                result[direction],
                {
                    "bandwidth": None,
                    "bytes": None,
                    "elapsed": None,
                    "latency": dict.fromkeys(("iqm", "jitter", "low", "high")),
                },
            )
        self.assertEqual(result["server"], dict.fromkeys(("id", "name", "location")))
        self.assertIsNone(result["packetLoss"])

    def test_external_failures(self):
        cases = [
            (FileNotFoundError(), "not found"),
            (subprocess.TimeoutExpired("speedtest", 180), "180 seconds"),
            (subprocess.CalledProcessError(2, "speedtest"), "exit code 2"),
        ]
        for error, message in cases:
            with self.subTest(error=error), patch("src.speedtest.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, message):
                    run_speedtest()

    def test_bad_and_incomplete_results(self):
        for body in ("{", "[]", '{"type":"progress"}', "null"):
            with self.subTest(body=body), patch("src.speedtest.subprocess.run", return_value=Mock(stdout=body)):
                with self.assertRaisesRegex(RuntimeError, "valid completed-result JSON"):
                    run_speedtest()

    def test_standard_subprocess_run_kills_and_reaps_process_on_interrupt(self):
        # Exercise the real subprocess.run implementation used by run_speedtest.
        # Only its OS process boundary is mocked; no Ookla process is launched.
        process = Mock()
        process.__enter__ = Mock(return_value=process)

        def exit_process(*args):
            process.wait()

        process.__exit__ = Mock(side_effect=exit_process)
        process.communicate.side_effect = KeyboardInterrupt
        with patch("subprocess.Popen", return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                run_speedtest()
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        process.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
