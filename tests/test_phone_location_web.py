import http.client
import io
import json
import shutil
import subprocess
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import cloudflare_tunnel as tunnel
from scripts import measurement_probe as measurement
from scripts import phone_location_probe as phone

METADATA = {"location_type": "inside", "location_name": "Example Hall", "location_detail": "2nd floor"}


class PageTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), phone.Handler)
        self.server.token = "session-secret"
        self.server.on_location = Mock(return_value=True)
        self.server.on_location_responded = Mock()
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, path, method="GET", body=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_page_headers_body_and_no_measurement_callbacks(self):
        with redirect_stderr(io.StringIO()) as errors:
            status, headers, body = self.request("/phone-location?token=session-secret")
        self.assertEqual(status, 200)
        self.assertEqual(body, phone.PAGE_PATH.read_bytes())
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertNotIn(b"session-secret", body)
        self.assertNotIn("session-secret", errors.getvalue())
        self.server.on_location.assert_not_called()
        self.server.on_location_responded.assert_not_called()

    def test_invalid_tokens_and_unknown_routes(self):
        for query in (
            "",
            "?token=",
            "?token=wrong",
            "?token=%E3%81%82",
            "?token=session-secret&token=session-secret",
            "?token=session-secret&token=",
            "?token=&token=session-secret",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.request("/phone-location" + query)[0], 403)
        for path in ("/", "/web/phone-location.html", "/../README.md", "/phone-location/extra"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(json.loads(self.request("/location")[2])["status"], "ready")

    def test_page_read_failure_is_generic_500(self):
        with patch.object(Path, "read_bytes", side_effect=OSError("private path")), redirect_stderr(io.StringIO()):
            status, headers, body = self.request("/phone-location?token=session-secret")
        self.assertEqual(status, 500)
        self.assertNotIn(b"private path", body)
        self.assertNotIn(b"session-secret", body)

    def test_fixed_css_and_javascript_resources_are_served(self):
        for suffix, content_type in (("css", "text/css; charset=utf-8"), ("js", "text/javascript; charset=utf-8")):
            with self.subTest(suffix=suffix):
                status, headers, body = self.request(f"/phone-location.{suffix}")
                self.assertEqual(status, 200)
                self.assertEqual(body, phone.PAGE_PATH.with_suffix(f".{suffix}").read_bytes())
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertEqual(int(headers["Content-Length"]), len(body))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["Referrer-Policy"], "no-referrer")
                self.assertNotIn(b"session-secret", body)
        self.server.on_location.assert_not_called()
        self.server.on_location_responded.assert_not_called()

    def test_resource_failures_do_not_expose_paths_or_other_files(self):
        with patch.object(Path, "read_bytes", side_effect=OSError("private path")), redirect_stderr(io.StringIO()):
            for suffix in ("css", "js"):
                status, headers, body = self.request(f"/phone-location.{suffix}")
                self.assertEqual(status, 500)
                self.assertNotIn(b"private path", body)
        for path in ("/web/phone-location.js", "/phone-location.js/../README.md", "/phone-location.json"):
            self.assertEqual(self.request(path)[0], 404)

    def test_browser_json_is_accepted_and_post_responses_are_not_cached(self):
        body = json.dumps({"latitude": 40.42, "longitude": -86.91, "sent_at": "2026-11-07T12:00:00.000Z", **METADATA})
        status, headers, result = self.request("/location?token=session-secret", "POST", body)
        self.assertEqual((status, json.loads(result)), (200, {"status": "ok"}))
        self.assertEqual(headers["Cache-Control"], "no-store")
        location = self.server.on_location.call_args.args[0]
        self.assertEqual(set(location), {"latitude", "longitude", "sent_at", "received_at", *METADATA})
        status, headers, body = self.request("/location?token=session-secret", "POST", "{}")
        self.assertEqual(status, 400)
        self.assertEqual(headers["Cache-Control"], "no-store")


class WebStartupTests(unittest.TestCase):
    def test_url_builder(self):
        self.assertEqual(
            tunnel.build_web_location_url("https://test.trycloudflare.com", "a+b"),
            "https://test.trycloudflare.com/phone-location?token=a%2Bb",
        )
        for url, token in (("https://example.com", "test"), ("https://test.trycloudflare.com", "")):
            with self.assertRaises(ValueError):
                tunnel.build_web_location_url(url, token)

    def test_removed_arguments_are_rejected_before_resources_start(self):
        for module in (phone, measurement):
            for value in ("shortcut", "web", "invalid"):
                with (
                    self.subTest(module=module.__name__, value=value),
                    patch.object(module, "ThreadingHTTPServer") as server,
                    patch.object(module, "run_tunnel") as run,
                    redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as error:
                        module.main(["--location-method", value])
                    self.assertEqual(error.exception.code, 2)
                    server.assert_not_called()
                    run.assert_not_called()

    def test_phone_starts_tunnel_without_arguments(self):
        with (
            patch.object(phone, "ThreadingHTTPServer") as server,
            patch.object(phone.threading, "Thread"),
            patch.object(phone, "run_tunnel", return_value=0) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(phone.main(), 0)
        run.assert_called_once_with(server.return_value.token)

    def test_tunnel_cli_starts_without_method_argument(self):
        with (
            patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test"]),
            patch.object(tunnel, "run_tunnel", return_value=0) as run,
        ):
            self.assertEqual(tunnel.main(), 0)
        run.assert_called_once_with("test")

    def test_tunnel_cli_rejects_removed_arguments_before_start(self):
        for value in ("shortcut", "web"):
            with (
                self.subTest(value=value),
                patch("sys.argv", ["cloudflare_tunnel.py", "--token", "test", "--location-method", value]),
                patch.object(tunnel, "run_tunnel") as run,
                patch.object(tunnel.subprocess, "Popen") as popen,
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as error:
                    tunnel.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
                popen.assert_not_called()

    def test_web_qr_and_ready_callback(self):
        stop = threading.Event()
        process = Mock()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        with (
            patch.object(tunnel.subprocess, "Popen", return_value=process),
            patch.object(tunnel.queue, "Queue") as queue_type,
            patch.object(tunnel, "print_qr_code") as qr,
            redirect_stdout(io.StringIO()) as output,
        ):
            queue_type.return_value.get.return_value = "https://test.trycloudflare.com"
            self.assertEqual(tunnel.run_tunnel("test", stop_event=stop, on_ready=stop.set), 0)
        qr.assert_called_once_with("https://test.trycloudflare.com/phone-location?token=test")
        self.assertIn("phone camera", output.getvalue())
        process.terminate.assert_called_once()


@unittest.skipUnless(shutil.which("node"), "Node.js is unavailable; browser-script simulation skipped")
class PageScriptTests(unittest.TestCase):
    def test_permission_recovery_does_not_post_or_retry_without_click(self):
        script = phone.PAGE_PATH.with_suffix(".js").read_text(encoding="utf-8")
        harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = SCRIPT;
async function scenario(failure, synchronous = false) {
  const elements = Object.fromEntries(['send', 'status', 'coordinates', 'latitude', 'longitude', 'permission-help',
      'location-form', 'location-type', 'location-name', 'location-detail'].map(
    id => [id, { disabled: false, hidden: true, textContent: '', value: '',
      reportValidity() { throw Error('Validation must not open a popup'); },
      focus() { throw Error('Validation must not move focus automatically'); },
      addEventListener(event, fn) { this.click = fn; } }]));
  elements.send.disabled = true;
  const submit = () => elements['location-form'].click({preventDefault() {}});
  let attempts = 0, posts = 0, succeed = false;
  let pending;
  const context = {
    document: { getElementById: id => elements[id] },
    window: { location: { href: 'https://test.trycloudflare.com/phone-location?token=secret',
                          origin: 'https://test.trycloudflare.com' }, isSecureContext: true },
    navigator: {
      permissions: { query() { throw Error('Do not gate Safari using Permissions API'); } },
      geolocation: { getCurrentPosition(ok, bad, options) {
        attempts++;
        assert.equal(options.enableHighAccuracy, true);
        submit();
        if (succeed) {
          assert.equal(elements['location-name'].disabled, true);
          elements['location-name'].value = 'changed during acquisition';
          pending = ok({ coords: { latitude: 40, longitude: -86 } });
        }
        else if (synchronous) { throw failure; }
        else { bad(failure); }
      } }
    },
    fetch: async (url, options) => {
      posts++;
      const data = JSON.parse(options.body);
      assert.equal(data.location_type, 'inside');
      assert.equal(data.location_name, 'Example Hall');
      assert.equal(data.location_detail, '2nd floor');
      assert.equal(typeof data.latitude, 'number');
      return { status: 200, json: async () => ({status:'ok'}) };
    },
    URL, AbortController, setTimeout, clearTimeout
  };
  vm.runInNewContext(script, context);
  assert.equal(attempts, 0);
  submit();
  assert.equal(attempts, 0);
  elements['location-type'].value = 'inside';
  elements['location-detail'].value = '2nd floor';
  for (const value of ['', '   ', 'x'.repeat(201), 'bad\0name']) {
    elements['location-name'].value = value;
    submit();
    assert.equal(attempts, 0);
    assert.equal(posts, 0);
  }
  elements['location-name'].value = ' Example Hall ';
  for (const value of ['', '   ', 'x'.repeat(501), 'bad\0detail']) {
    elements['location-detail'].value = value;
    submit();
    assert.equal(attempts, 0);
    assert.equal(posts, 0);
  }
  elements['location-detail'].value = '2nd floor';
  submit();
  assert.equal(attempts, 1);
  assert.equal(posts, 0);
  assert.equal(elements.send.disabled, false);
  assert.equal(elements['permission-help'].hidden, false);
  assert.match(elements.status.textContent, /blocked location access/);
  succeed = true;
  assert.equal(elements['location-name'].disabled, false);
  assert.equal(elements['location-name'].value, ' Example Hall ');
  submit();
  await pending;
  assert.equal(attempts, 2);
  assert.equal(posts, 1);
  assert.equal(elements.send.disabled, true);
  assert.equal(elements['permission-help'].hidden, true);
  submit();
  assert.equal(posts, 1);
  assert.match(elements.status.textContent, /PC received/);
}
(async () => {
  await scenario({code:1});
  await scenario({name:'SecurityError'}, true);
  await scenario({name:'NotAllowedError'}, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        harness = harness.replace("SCRIPT", json.dumps(script))
        result = subprocess.run(
            [shutil.which("node")], input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
