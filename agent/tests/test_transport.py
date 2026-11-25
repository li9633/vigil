"""上报协议：鉴权头、注册、重试、ETag 与 boot_id 测试。"""

import json
import os
import tempfile
import unittest

from vigil_agent.config import default_config
from vigil_agent.transport import Transport, TransportError, read_boot_id


def make_config(client_id="cl_1", secret="s3cret", base_url="https://vigil.example.com"):
    cfg = default_config()
    cfg["server"].update(
        {"client_id": client_id, "secret": secret, "base_url": base_url}
    )
    return cfg


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.saved = []

    def _transport(self, responses, config=None, boot_id="boot-1"):
        client = FakeClient(responses)
        transport = Transport(
            config or make_config(),
            client=client,
            now=lambda: 1700000000,
            sleep=self.sleeps.append,
            boot_id=boot_id,
            save_config=lambda cfg: self.saved.append(cfg),
        )
        return transport, client

    def test_beacon_headers_and_body(self):
        transport, client = self._transport([(200, {}, b"{}")])
        result = transport.beacon(7, 100)
        self.assertTrue(result["ok"])

        request = client.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertTrue(request["url"].endswith("/api/v1/beacon"))
        self.assertEqual(request["body"]["client_id"], "cl_1")
        self.assertEqual(request["body"]["seq"], 7)
        self.assertEqual(request["body"]["boot_id"], "boot-1")
        self.assertEqual(request["body"]["uptime"], 100)
        self.assertEqual(set(request["body"].keys()), {"client_id", "seq", "boot_id", "uptime"})

        headers = request["headers"]
        self.assertEqual(headers["X-Vigil-Client"], "cl_1")
        self.assertEqual(headers["Authorization"], "Bearer s3cret")
        self.assertEqual(headers["X-Vigil-Timestamp"], "1700000000")
        self.assertEqual(len(headers["X-Vigil-Nonce"]), 32)

    def test_register_updates_credentials_and_persists(self):
        config = make_config(client_id="", secret="")
        config["server"]["bootstrap_token"] = "boot-token"
        payload = {
            "client_id": "cl_new",
            "secret": "new-secret",
            "server_time": 1700000001,
            "heartbeat_interval": 45,
        }
        transport, client = self._transport(
            [(200, {}, json.dumps(payload).encode("utf-8"))], config=config
        )
        result = transport.register("router-a", model="M1", fw_version="OpenWrt 23.05")
        self.assertTrue(result["ok"])
        self.assertTrue(transport.is_registered)
        self.assertEqual(transport.client_id, "cl_new")
        self.assertEqual(transport.secret, "new-secret")
        self.assertEqual(config["server"]["heartbeat_interval"], 45)

        request = client.requests[0]
        self.assertEqual(request["headers"]["X-Vigil-Bootstrap"], "boot-token")
        self.assertNotIn("Authorization", request["headers"])
        self.assertEqual(request["body"]["name"], "router-a")
        self.assertEqual(request["body"]["model"], "M1")
        self.assertEqual(request["body"]["fw_version"], "OpenWrt 23.05")
        self.assertEqual(request["body"]["protocol"], 1)
        self.assertEqual(len(self.saved), 1)

    def test_register_rejects_incomplete_response(self):
        config = make_config(client_id="", secret="")
        transport, _client = self._transport(
            [(200, {}, b'{"client_id": "cl_new"}')], config=config
        )
        result = transport.register("router-a")
        self.assertFalse(result["ok"])
        self.assertIn("secret", result["error"])
        self.assertFalse(transport.is_registered)

    def test_retry_on_server_error(self):
        transport, client = self._transport(
            [(500, {}, b"{}"), (200, {}, b'{"ok": true}')]
        )
        result = transport.heartbeat(1, 10, modules={"net.carrier": {"status": "ok"}})
        self.assertTrue(result["ok"])
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(self.sleeps, [1.0])

    def test_no_retry_on_client_error(self):
        transport, client = self._transport(
            [(401, {}, b'{"detail": "bad signature"}')]
        )
        result = transport.heartbeat(1, 10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)
        self.assertIn("bad signature", result["error"])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_network_error_retries_then_fails(self):
        transport, client = self._transport(
            [TransportError("boom"), TransportError("boom"), TransportError("boom")]
        )
        result = transport.heartbeat(1, 10)
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(self.sleeps, [1.0, 3.0])

    def test_events_payload_is_minimal(self):
        transport, client = self._transport([(200, {}, b'{"acked": [1]}')])
        result = transport.events(
            [
                {
                    "event_id": "e-1",
                    "seq": 1,
                    "type": "module_status",
                    "domain": "net.dns",
                    "payload": {"status": "critical"},
                    "ts": 123,
                }
            ]
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)
        body = client.requests[0]["body"]
        self.assertEqual(body["client_id"], "cl_1")
        self.assertEqual(
            set(body["events"][0].keys()),
            {"event_id", "seq", "type", "domain", "payload"},
        )
        self.assertEqual(body["events"][0]["event_id"], "e-1")

    def test_events_generates_missing_event_id(self):
        transport, client = self._transport([(200, {}, b"{}")])
        transport.events([{"seq": 2, "type": "t", "domain": "d", "payload": {}}])
        event = client.requests[0]["body"]["events"][0]
        self.assertTrue(event["event_id"])

    def test_fetch_config_etag_and_304(self):
        transport, client = self._transport(
            [(304, {"ETag": "abc"}, b"")]
        )
        result = transport.fetch_config("etag-old")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 304)
        self.assertIsNone(result["body"])
        self.assertEqual(result["etag"], "abc")
        request = client.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertTrue(request["url"].endswith("/api/v1/config"))
        self.assertEqual(request["headers"]["If-None-Match"], "etag-old")

    def test_unregistered_skips_request(self):
        config = make_config(client_id="", secret="")
        transport, client = self._transport([], config=config)
        result = transport.heartbeat(1, 10)
        self.assertFalse(result["ok"])
        self.assertIn("未注册", result["error"])
        self.assertEqual(client.requests, [])

    def test_missing_base_url_skips_request(self):
        config = make_config(base_url="")
        transport, client = self._transport([], config=config)
        result = transport.beacon(1, 10)
        self.assertFalse(result["ok"])
        self.assertIn("未配置", result["error"])
        self.assertEqual(client.requests, [])

    def test_heartbeat_payload_fields(self):
        transport, client = self._transport([(200, {}, b"{}")])
        transport.heartbeat(
            3,
            500,
            modules={"net.carrier": {"status": "ok", "latency_ms": 0}},
            spool={"pending": 2, "dropped": 1},
            wan_ip="203.0.113.5",
        )
        body = client.requests[0]["body"]
        self.assertEqual(body["seq"], 3)
        self.assertEqual(body["uptime"], 500)
        self.assertEqual(body["wan_ip"], "203.0.113.5")
        self.assertEqual(body["spool"], {"pending": 2, "dropped": 1})
        self.assertEqual(body["agent_version"], "1.0.0")
        self.assertIn("net.carrier", body["modules"])
        self.assertEqual(body["boot_id"], "boot-1")


class BootIdTest(unittest.TestCase):
    def test_fallback_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = os.path.join(tmp, "boot_id")
            first = read_boot_id(
                proc_path=os.path.join(tmp, "no-proc"),
                fallback_path=fallback,
            )
            second = read_boot_id(
                proc_path=os.path.join(tmp, "no-proc"),
                fallback_path=fallback,
            )
            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertTrue(os.path.isfile(fallback))

    def test_proc_value_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_path = os.path.join(tmp, "boot_id")
            with open(proc_path, "w", encoding="utf-8") as handle:
                handle.write("kernel-boot-id\n")
            value = read_boot_id(
                proc_path=proc_path,
                fallback_path=os.path.join(tmp, "fallback"),
            )
        self.assertEqual(value, "kernel-boot-id")


if __name__ == "__main__":
    unittest.main()
