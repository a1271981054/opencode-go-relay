#!/usr/bin/env python3
"""relay.py 的端到端测试：假上游 + 真中转，验证补头、路径映射、流式逐块到达。"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import relay  # noqa: E402


class FakeUpstream(BaseHTTPRequestHandler):
    """记录收到的请求；stream=true 时按 0.25s 间隔吐 3 个 SSE 块。"""

    protocol_version = "HTTP/1.1"
    seen: list[dict] = []

    def log_message(self, *args):
        return

    def do_GET(self):
        FakeUpstream.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": b""})
        body = json.dumps({"object": "list", "data": [{"id": "glm-5.3"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        FakeUpstream.seen.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": raw}
        )
        payload = json.loads(raw or b"{}")

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for token in ("流", "式", "ok"):
                chunk = ("data: " + json.dumps({"delta": token}, ensure_ascii=False) + "\n\n").encode()
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
                time.sleep(0.25)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        out = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class RelayTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        relay.RelayHandler.upstream_scheme = "http"
        relay.RelayHandler.upstream_host = "127.0.0.1"
        relay.RelayHandler.upstream_port = cls.upstream.server_address[1]
        relay._log_path = None
        relay.log = lambda *a, **k: None  # 测试里不刷屏

        cls.server = relay.RelayServer(("127.0.0.1", 0), relay.RelayHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        FakeUpstream.seen.clear()

    # ---------- 工具 ----------

    def post(self, path: str, payload: dict, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode()
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request("POST", path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    # ---------- 用例 ----------

    def test_injects_session_header_and_strips_model_prefix(self):
        status, _ = self.post(
            "/v1/chat/completions",
            {"model": "opencode-go/glm-5.3", "messages": [{"role": "user", "content": "你好"}]},
            {"x-api-key": "oc_sk_test"},
        )
        self.assertEqual(status, 200)

        seen = FakeUpstream.seen[-1]
        self.assertEqual(seen["path"], "/inference/go/openai/v1/chat/completions")
        self.assertTrue(seen["headers"]["x-opencode-session"].startswith("ses_"))
        self.assertEqual(seen["headers"]["authorization"], "Bearer oc_sk_test")
        self.assertEqual(json.loads(seen["body"])["model"], "glm-5.3")

    def test_session_id_is_stable_per_conversation(self):
        payload = {"model": "glm-5.3", "messages": [{"role": "user", "content": "同一个开头"}]}
        self.post("/v1/chat/completions", payload)
        first = FakeUpstream.seen[-1]["headers"]["x-opencode-session"]

        self.post("/v1/chat/completions", payload)
        second = FakeUpstream.seen[-1]["headers"]["x-opencode-session"]

        other = {"model": "glm-5.3", "messages": [{"role": "user", "content": "另一段对话"}]}
        self.post("/v1/chat/completions", other)
        third = FakeUpstream.seen[-1]["headers"]["x-opencode-session"]

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_preserves_client_session_header(self):
        self.post(
            "/v1/chat/completions",
            {"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]},
            {"x-opencode-session": "ses_from_client"},
        )
        self.assertEqual(FakeUpstream.seen[-1]["headers"]["x-opencode-session"], "ses_from_client")

    def test_path_mapping(self):
        cases = {
            "/v1/models": "/inference/go/v1/models",
            "/v1/messages": "/inference/go/anthropic/v1/messages",
            "/v1/responses": "/inference/go/openai/v1/responses",
        }
        for given, expected in cases.items():
            if given == "/v1/models":
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
                conn.request("GET", given)
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
            else:
                self.post(given, {"model": "glm-5.3", "messages": []})
            self.assertEqual(FakeUpstream.seen[-1]["path"], expected, given)

    def test_health_endpoint(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/__relay/health")
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload["ok"])
        self.assertNotIn("Authorization", json.dumps(payload))

    def test_streaming_arrives_incrementally(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(
            {"model": "glm-5.3", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        started = time.monotonic()
        conn.request(
            "POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        first_piece_at = None
        pieces = 0
        while True:
            chunk = resp.read1(8192)
            if not chunk:
                break
            if first_piece_at is None:
                first_piece_at = time.monotonic() - started
            pieces += len(chunk)
        total = time.monotonic() - started
        conn.close()

        self.assertGreater(pieces, 0)
        self.assertIsNotNone(first_piece_at)
        self.assertLess(first_piece_at, 0.6, "第一块来得太晚，说明被整体缓冲了")
        self.assertGreater(total, 0.6, "总时长过短，说明没有逐块转发")
        self.assertIn("text/event-stream", resp.getheader("Content-Type") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
