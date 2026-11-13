"""Receive a phone location, read Windows Wi-Fi, run Ookla and print the combined result."""

import argparse
import json
import queue
import re
import secrets
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cloudflare_tunnel import TunnelCleanupError, run_tunnel
from scripts.phone_location_probe import Handler
from src.speedtest import run_speedtest
from src.storage import DEFAULT_DB_PATH, StorageError, initialize_database, prepare_measurement, save_measurement
from src.wifi import get_wifi_info

LOCATION_TIMEOUT = 180
POLL_INTERVAL = 0.05


class LocationInbox:
    """Atomically accept one location before the deadline; notify after its HTTP response."""

    def __init__(self):
        self.lock = threading.Lock()
        self.responded = threading.Event()
        self.location = None
        self.deadline = None
        self.accepting = True

    def on_ready(self):
        with self.lock:
            if self.deadline is None:
                self.deadline = time.monotonic() + LOCATION_TIMEOUT

    def accept(self, data):
        with self.lock:
            if not self.accepting or self.location is not None:
                return False
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.accepting = False
                return False
            self.location = data.copy()
            return True

    def check(self):
        with self.lock:
            if self.location is not None:
                return self.location
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.accepting = False
                raise RuntimeError("Timed out waiting for phone location (180 seconds after QR display).")
            return None

    def close(self):
        with self.lock:
            self.accepting = False

    def tunnel_ended(self):
        with self.lock:
            if self.location is None:
                self.accepting = False


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        initialize_database(DEFAULT_DB_PATH)
    except KeyboardInterrupt:
        print("Database preparation interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Database preparation failed ({DEFAULT_DB_PATH}): {exc}", file=sys.stderr)
        return 1
    inbox = LocationInbox()
    stop = threading.Event()
    tunnel_results = queue.Queue()
    server_errors = queue.Queue()
    server = None
    receiver = None
    tunnel = None
    result_json = None
    exit_code = 1
    stage = "Location reception"
    record = None
    token = None

    def error_detail(error):
        detail = str(error)
        if token:
            detail = detail.replace(token, "[redacted token]")
        return re.sub(r"https://[^\s]*\.trycloudflare\.com[^\s]*", "[redacted public URL]", detail)

    def tunnel_worker():
        try:
            code = run_tunnel(token, stop_event=stop, on_ready=inbox.on_ready)
            error = None
        except BaseException as exc:
            code, error = 1, exc
        inbox.tunnel_ended()
        tunnel_results.put((code, error, stop.is_set()))

    def serve():
        try:
            server.serve_forever(poll_interval=POLL_INTERVAL)
        except BaseException as exc:
            server_errors.put(exc)

    def check_workers():
        try:
            error = server_errors.get_nowait()
        except queue.Empty:
            pass
        else:
            raise RuntimeError(f"Location server failed: {error}")
        try:
            code, error, requested = tunnel_results.get_nowait()
        except queue.Empty:
            return
        if requested and code == 0 and error is None:
            return
        detail = error_detail(error) if error is not None else f"exit code {code}"
        if isinstance(error, TunnelCleanupError):
            raise RuntimeError(f"Tunnel cleanup failed: {detail}")
        if inbox.check() is None:
            raise RuntimeError(f"Tunnel stopped before location was accepted: {detail}")
        print(f"Warning: tunnel stopped after location was accepted ({detail}); continuing.", file=sys.stderr)

    try:
        token = secrets.token_urlsafe(24)
        server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
        # server_close joins active handlers instead of abandoning daemon threads.
        server.daemon_threads = False
        server.token = token
        server.on_location = inbox.accept
        server.on_location_responded = inbox.responded.set
        receiver = threading.Thread(target=serve, name="measurement-http")
        receiver.start()
        print("Waiting for phone location. Stay in the same place until Speedtest finishes.", flush=True)
        tunnel = threading.Thread(target=tunnel_worker, name="measurement-tunnel")
        tunnel.start()
        while True:
            check_workers()
            location = inbox.check()
            if location is not None and inbox.responded.is_set():
                break
            inbox.responded.wait(POLL_INTERVAL)
        print("Location accepted. Acquiring Wi-Fi information...", flush=True)
        stage = "Wi-Fi acquisition"
        wifi = get_wifi_info()
        print("Wi-Fi acquired. Starting one Speedtest...", flush=True)
        stage = "Speedtest"
        speedtest = run_speedtest()
        check_workers()
        stage = "Measurement validation"
        measurement = {"location": location, "wifi": wifi, "speedtest": speedtest}
        record = prepare_measurement(measurement)
        stage = "Measurement JSON serialization"
        result_json = json.dumps({**measurement, "attempt_count": 1}, indent=2, allow_nan=False)
        exit_code = 0
    except KeyboardInterrupt:
        print("Measurement interrupted.", file=sys.stderr)
        exit_code = 130
    except Exception as exc:
        print(f"{stage} failed: {error_detail(exc)}", file=sys.stderr)
    finally:
        inbox.close()
        stop.set()

        # Attempt every cleanup step even if a different resource fails to close.
        def cleanup(action):
            nonlocal exit_code
            try:
                action()
            except KeyboardInterrupt:
                exit_code = 130
                print("Cleanup interrupted.", file=sys.stderr)
            except Exception as exc:
                exit_code = 1
                print(f"Cleanup failed: {error_detail(exc)}", file=sys.stderr)

        if server is not None:
            if receiver is not None and receiver.is_alive():
                cleanup(server.shutdown)
            cleanup(server.server_close)
        for worker in (receiver, tunnel):
            if worker is not None and worker.ident is not None:

                def join_worker(worker=worker):
                    deadline = time.monotonic() + 20
                    while worker.is_alive():
                        worker.join(timeout=0.2)
                        if worker.is_alive() and time.monotonic() >= deadline:
                            raise RuntimeError(f"{worker.name} did not stop.")

                cleanup(join_worker)
        cleanup(check_workers)
    if exit_code == 0 and result_json is not None:
        try:
            row_id = save_measurement(record, DEFAULT_DB_PATH)
        except KeyboardInterrupt as exc:
            outcome = getattr(exc, "outcome", "not saved")
            print(
                f"Database save interrupted; persistence outcome: {outcome}. Check database before retrying.",
                file=sys.stderr,
            )
            return 130
        except Exception as exc:
            outcome = exc.outcome if isinstance(exc, StorageError) else "not saved"
            if outcome == "not saved":
                message = "Measurement acquired; DB not saved"
            elif outcome == "saved":
                message = "DB saved; post-commit failure. Check database"
            else:
                message = "DB persistence outcome unknown. Check database before retrying"
            print(f"Database save failed ({DEFAULT_DB_PATH}): {message}: {error_detail(exc)}", file=sys.stderr)
            try:
                print(result_json, flush=True)
            except KeyboardInterrupt:
                print("Rescue display interrupted; check database persistence outcome above.", file=sys.stderr)
                return 130
            except Exception:
                print("Rescue display failed; check database persistence outcome above.", file=sys.stderr)
            return 1
        try:
            print(f"Database saved: {DEFAULT_DB_PATH.resolve()} (row ID {row_id}).", file=sys.stderr)
            print(result_json, flush=True)
        except KeyboardInterrupt:
            print("Display interrupted after DB commit; data saved. Check database.", file=sys.stderr)
            return 130
        except Exception:
            print("Display failed after DB commit; data saved. Check database.", file=sys.stderr)
            return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
