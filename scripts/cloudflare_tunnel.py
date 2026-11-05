"""Launch a temporary Cloudflare tunnel for the phone location probe."""

import argparse
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from urllib.parse import urlencode

PUBLIC_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com(?=[\s/|]|$)")


class TunnelCleanupError(RuntimeError):
    """The tunnel resources could not be fully released."""


def extract_public_url(line):
    match = PUBLIC_URL_PATTERN.search(line)
    return match.group(0) if match else None


def build_location_url(public_url, token):
    if PUBLIC_URL_PATTERN.fullmatch(public_url) is None:
        raise ValueError("Expected a Cloudflare Quick Tunnel HTTPS URL.")
    if not token:
        raise ValueError("A receiver token is required.")
    return f"{public_url}/location?{urlencode({'token': token})}"


def print_qr_code(url):
    import qrcode

    qr = qrcode.QRCode(border=4)
    qr.add_data(url)
    qr.make(fit=True)
    # Force text output; avoid terminal-specific ANSI color handling on Windows.
    qr.print_ascii(tty=False, invert=True)


def _read_output(stream, messages):
    try:
        for line in stream:
            messages.put(line.rstrip())
    finally:
        messages.put(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="Session token of an already running receiver")
    args = parser.parse_args()
    if not args.token or not args.token.isascii() or not re.fullmatch(r"[A-Za-z0-9_-]+", args.token):
        parser.error("Use the URL-safe session token printed by the receiver.")
    return run_tunnel(args.token)


def run_tunnel(token, *, stop_event=None, on_ready=None):
    """Display the destination QR and monitor cloudflared until interrupted or failed."""
    if stop_event is not None and stop_event.is_set():
        return 0
    try:
        import qrcode  # noqa: F401
    except ImportError:
        print("Missing qrcode. Run: .\\.venv\\Scripts\\python.exe -m pip install qrcode==8.2", file=sys.stderr)
        return 1
    print("Starting Cloudflare Tunnel to http://127.0.0.1:8765 ...", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        process = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8765", "--no-autoupdate"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        print(f"Could not start cloudflared. Check that it is installed and on PATH: {exc}", file=sys.stderr)
        return 1

    messages = queue.Queue()
    reader = threading.Thread(target=_read_output, args=(process.stdout, messages), daemon=True)
    recent_logs = deque(maxlen=20)
    public_url = None
    deadline = time.monotonic() + 60
    try:
        reader.start()
        while True:
            if stop_event is not None and stop_event.is_set():
                return 0
            if public_url is None and time.monotonic() >= deadline:
                raise RuntimeError("Cloudflare did not generate a public URL within 60 seconds.")
            try:
                line = messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"cloudflared stopped unexpectedly (exit code {process.wait(timeout=5)}).")
            recent_logs.append(line)
            found_url = extract_public_url(line)
            if found_url and public_url is None:
                public_url = found_url
                location_url = build_location_url(public_url, token)
                print("\n" + "=" * 72, flush=True)
                print("IPHONE SHORTCUT JSON POST URL (contains session token)", flush=True)
                print(f"\n{location_url}\n", flush=True)
                print_qr_code(location_url)
                print("=" * 72, flush=True)
                print("Scan inside your Shortcut. URL assigned; connection may take a moment.", flush=True)
                print("This temporary URL can change each time you restart the tunnel.", flush=True)
                if on_ready is not None:
                    on_ready()
            elif public_url and re.search(r"\b(?:ERR|WRN)\b", line):
                print(f"cloudflared: {line}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        print("\nStopping the tunnel...", flush=True)
        return 0
    except (RuntimeError, subprocess.TimeoutExpired, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        print("Recent cloudflared output:", file=sys.stderr)
        for line in recent_logs:
            print(line, file=sys.stderr)
        return 1
    finally:
        try:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            finally:
                if reader.ident is not None:
                    reader.join(timeout=2)
                process.stdout.close()
            if reader.is_alive():
                raise RuntimeError("Cloudflare output reader did not stop.")
        except Exception as exc:
            raise TunnelCleanupError(f"Could not clean up cloudflared: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
