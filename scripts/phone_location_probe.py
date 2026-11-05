"""Receive iPhone location JSON through a separately managed tunnel until Ctrl+C."""

import json
import math
import secrets
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class LocationError(ValueError):
    def __init__(self, message, field=None, stage="validation"):
        super().__init__(message)
        self.field = field
        self.stage = stage


def validate_location(data):
    """Select valid location fields without trusting client-provided receive times."""
    if not isinstance(data, dict):
        raise LocationError("Location must be a JSON object.")
    for field in ("latitude", "longitude", "sent_at"):
        if field not in data:
            raise LocationError(f"Missing required field: {field}.", field)
    result = {}
    for field, minimum, maximum in (
        ("latitude", -90, 90),
        ("longitude", -180, 180),
    ):
        value = data[field]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise LocationError(f"{field} must be a numeric string or a JSON number.", field, "conversion")
        try:
            value = float(value.strip() if isinstance(value, str) else value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise LocationError(f"{field} could not be converted to float.", field, "conversion") from exc
        if not math.isfinite(value):
            raise LocationError(f"{field} must be finite.", field)
        if not minimum <= value <= maximum:
            raise LocationError(f"{field} must be between {minimum} and {maximum}.", field)
        result[field] = value
    value = data["sent_at"]
    if not isinstance(value, str):
        raise LocationError("sent_at must be a JSON string containing an ISO 8601 datetime with a timezone.", "sent_at")
    try:
        if "T" not in value:
            raise ValueError
        sent_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocationError(
            "sent_at has an invalid ISO 8601 datetime format. Example: 2026-11-04T12:00:00Z.", "sent_at"
        ) from exc
    if sent_at.utcoffset() is None:
        raise LocationError("sent_at is missing a timezone. Use Z or an offset such as -05:00.", "sent_at")
    try:
        result["sent_at"] = sent_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise LocationError(
            "sent_at is outside the supported datetime range after UTC conversion.", "sent_at"
        ) from exc
    return result


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not allowed.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Never log request lines: they may contain the session token.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path != "/location":
            self.send_json(404, {"error": "Use /location."})
            return
        self.send_json(200, {"status": "ready", "message": "POST location JSON to this URL."})

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path != "/location":
            self.send_json(404, {"error": "Use POST /location."})
            return

        tokens = parse_qs(url.query).get("token", [])
        if len(tokens) != 1 or not secrets.compare_digest(tokens[0].encode("utf-8"), self.server.token.encode("utf-8")):
            self.send_json(403, {"error": "Missing or invalid token."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Content-Type must be application/json."})
            return

        if self.headers.get("Transfer-Encoding") is not None:
            self.send_json(400, {"error": "Send JSON with Content-Length instead of Transfer-Encoding."})
            return
        if self.headers.get("Content-Length") is None:
            self.send_json(411, {"error": "Content-Length is required."})
            return
        try:
            length = int(self.headers["Content-Length"])
            if length < 0:
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Invalid Content-Length."})
            return
        if length > 65536:
            self.send_json(413, {"error": "JSON payload must not exceed 64 KiB."})
            return

        try:
            body = self.rfile.read(length)
        except TimeoutError:
            self.send_json(408, {"error": "Timed out reading the request body."})
            return
        if len(body) != length:
            self.send_json(400, {"error": "Incomplete request body."})
            return
        try:
            data = json.loads(body, parse_constant=reject_constant)
        except UnicodeError:
            self.send_json(400, {"stage": "json_decode", "error": "Request body has an invalid JSON text encoding."})
            return
        except json.JSONDecodeError as exc:
            self.send_json(
                400,
                {
                    "stage": "json_decode",
                    "error": f"Invalid JSON syntax at line {exc.lineno}, column {exc.colno}: {exc.msg}.",
                },
            )
            return
        except RecursionError:
            self.send_json(400, {"stage": "json_decode", "error": "JSON nesting is too deep."})
            return
        except ValueError:
            self.send_json(
                400,
                {
                    "stage": "json_decode",
                    "error": "Invalid JSON number. NaN, Infinity and excessively long integers are not accepted.",
                },
            )
            return
        try:
            data = validate_location(data)
        except LocationError as exc:
            response = {"stage": exc.stage, "error": str(exc)}
            if exc.field is not None:
                response["field"] = exc.field
            self.send_json(400, response)
            return

        data["received_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(json.dumps(data, indent=2, allow_nan=False), flush=True)
        self.send_json(200, {"status": "ok"})


def main():
    token = secrets.token_urlsafe(24)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    except OSError as exc:
        print(f"Could not start receiver on port 8765. Check whether it is already in use: {exc}", file=sys.stderr)
        return 1
    with server:
        server.token = token
        print("Local server: http://127.0.0.1:8765/location (accessible on this laptop only).", flush=True)
        print("From the project directory in another terminal, run:", flush=True)
        print(f".\\.venv\\Scripts\\python.exe scripts\\cloudflare_tunnel.py --token {token}", flush=True)
        print("Scan the launcher's QR code inside your iPhone Shortcut, then POST JSON to that exact URL.", flush=True)
        print("Required JSON: latitude and longitude (numeric strings or numbers), sent_at (ISO 8601 with timezone).",
              flush=True)
        print("Content-Type: application/json. Coordinates are converted to float and checked before display.", flush=True)
        print("Listening until Ctrl+C; received data is printed without being saved.", flush=True)
        try:
            # Poll regularly so Windows can process Ctrl+C even without incoming requests.
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print("\nStopped listening.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
