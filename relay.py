#!/usr/bin/env python3
"""
OpenCode Go -> WorkBuddy 本地中转 (relay)

WorkBuddy 的「添加模型」不能自定义请求头，而 OpenCode Go 网关要求每个请求带
`x-opencode-session`，否则直接 400 MissingSessionID（在鉴权之前就拒掉）。

这个脚本在本机 127.0.0.1 上开一个 OpenAI 兼容接口，把请求原样转发到
https://opencode.ai/inference/go，并补上 x-opencode-session。
API Key 不落地：WorkBuddy 发来的 Authorization 原样透传。

用法:
    python3 relay.py                 # 监听 127.0.0.1:8787
    python3 relay.py --port 8790
    python3 relay.py --upstream http://127.0.0.1:9000   # 仅用于本地联调
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
GO_PREFIX = "/inference/go"
LOG_MAX_BYTES = 5 * 1024 * 1024

# 逐跳头，不能原样转发
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}

# 响应里可以透传给客户端的头
PASS_RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "x-request-id",
    "openai-version",
    "openai-processing-ms",
    "opencode-allowance",
    "opencode-model",
    "retry-after",
    "content-encoding",
}

_log_lock = threading.Lock()
_log_path: str | None = None


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{stamp}] {line}"
    print(text, flush=True)
    if not _log_path:
        return
    try:
        with _log_lock:
            if os.path.exists(_log_path) and os.path.getsize(_log_path) > LOG_MAX_BYTES:
                os.replace(_log_path, _log_path + ".1")
            with open(_log_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
    except OSError:
        pass


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif part.get("type") == "input_text" and isinstance(part.get("input_text"), str):
                    out.append(part["input_text"])
        return " ".join(out)
    return ""


def session_id_for(body: bytes) -> str:
    """按「同一段对话 -> 同一个 session」生成稳定 ID，让网关能命中 prompt 缓存。"""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        payload = None

    seed_parts: list[str] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("instructions"), str):
            seed_parts.append(payload["instructions"])
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages[:4]:
                if isinstance(msg, dict):
                    seed_parts.append(text_of(msg.get("content")))
    seed = "\n".join(seed_parts).strip()

    if not seed:
        return "ses_relay_" + hashlib.sha1(str(time.time()).encode()).hexdigest()[:24]
    digest = hashlib.sha1(seed[:20000].encode("utf-8", "replace")).hexdigest()
    return "ses_" + digest[:32]


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"opencode-go-relay/{VERSION}"
    upstream_scheme = "https"
    upstream_host = "opencode.ai"
    upstream_port = 443
    timeout_seconds = 900

    # ---------- 基础工具 ----------

    def log_message(self, fmt, *args):  # 压掉默认的 stderr 访问日志
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = []
            while True:
                line = self.rfile.readline(65536).strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline(65536)
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _upstream_path(self, path: str) -> str:
        path = urlsplit(path).path or "/"
        if path.startswith("/__relay"):
            return path
        if path in ("/v1/models", "/models"):
            return f"{GO_PREFIX}/v1/models"
        if path.startswith("/v1/messages"):
            return f"{GO_PREFIX}/anthropic{path}"
        if path.startswith("/v1beta/"):
            return f"{GO_PREFIX}/google{path}"
        if path.startswith("/v1/"):
            return f"{GO_PREFIX}/openai{path}"
        if path.startswith("/inference/"):
            return path
        return f"{GO_PREFIX}{path}"

    def _build_headers(self, body: bytes, upstream_path: str) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in HOP_BY_HOP:
                continue
            if lower.startswith("sec-") or lower.startswith("x-forwarded"):
                continue
            headers.append((key, value))

        # 有的客户端用 api-key / x-api-key 而不是 Authorization，统一补一个
        if not any(k.lower() == "authorization" for k, _ in headers):
            for key, value in list(headers):
                if key.lower() in ("api-key", "x-api-key") and value:
                    headers.append(("Authorization", f"Bearer {value}"))
                    break

        if not any(k.lower() == "x-opencode-session" for k, _ in headers):
            headers.append(("x-opencode-session", session_id_for(body)))
        if not any(k.lower() == "user-agent" for k, _ in headers):
            headers.append(("User-Agent", f"workbuddy-opencode-go-relay/{VERSION}"))
        if body:
            headers.append(("Content-Length", str(len(body))))
        headers.append(("Accept-Encoding", "identity"))
        return headers

    # ---------- 路由 ----------

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if urlsplit(self.path).path.rstrip("/") in ("/__relay/health", "/health"):
            self._send_json(
                200,
                {
                    "ok": True,
                    "relay": VERSION,
                    "upstream": f"{self.upstream_scheme}://{self.upstream_host}{GO_PREFIX}",
                    "hint": "在 WorkBuddy 里填 http://127.0.0.1:%d/v1/chat/completions" % self.server.server_address[1],
                },
            )
            return
        self._forward("GET", b"")

    def do_POST(self):  # noqa: N802
        self._forward("POST", self._read_body())

    def do_HEAD(self):  # noqa: N802
        self._forward("HEAD", b"")

    # ---------- 转发 ----------

    def _normalize_model(self, body: bytes) -> bytes:
        """把 opencode-go/glm-5.3、opencode/glm-5.3 这类前缀去掉，网关只认裸模型 ID。"""
        if not body:
            return body
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return body
        if not isinstance(payload, dict):
            return body
        model = payload.get("model")
        if not isinstance(model, str):
            return body
        for prefix in ("opencode-go/", "opencode/"):
            if model.startswith(prefix):
                payload["model"] = model[len(prefix):]
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return body

    def _forward(self, method: str, body: bytes) -> None:
        body = self._normalize_model(body)
        upstream_path = self._upstream_path(self.path)
        if upstream_path.startswith("/__relay"):
            self._send_json(404, {"error": {"type": "not_found", "message": "unknown relay path"}})
            return

        headers = self._build_headers(body, upstream_path)
        started = time.time()
        model = ""
        if body:
            try:
                model = str(json.loads(body.decode("utf-8", "replace")).get("model") or "")
            except Exception:
                model = ""
        is_stream = '"stream":true' in body.decode("utf-8", "replace").replace(" ", "") if body else False

        conn_cls = http.client.HTTPSConnection if self.upstream_scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self.upstream_host, self.upstream_port, timeout=self.timeout_seconds)
        try:
            conn.putrequest(method, upstream_path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", self.upstream_host)
            for key, value in headers:
                conn.putheader(key, value)
            conn.endheaders(body if body else None)
            resp = conn.getresponse()
        except socket.timeout:
            conn.close()
            log(f"{method} {upstream_path} 504 upstream timeout")
            self._send_json(504, {"error": {"type": "upstream_timeout", "message": "opencode.ai 超时，请检查网络或代理"}})
            return
        except Exception as exc:  # 连不上
            conn.close()
            log(f"{method} {upstream_path} 502 {exc}")
            self._send_json(
                502,
                {
                    "error": {
                        "type": "relay_upstream_unreachable",
                        "message": f"连接 opencode.ai 失败: {exc}. 如果你开了代理，确认终端/本脚本也走代理。",
                    }
                },
            )
            return

        status = resp.status
        out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() in PASS_RESPONSE_HEADERS]

        try:
            self.send_response(status)
            for key, value in out_headers:
                self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()

            total = 0
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                total += len(chunk)
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log(f"{method} {upstream_path} client disconnected after {int(time.time() - started)}s")
            return
        finally:
            resp.close()
            conn.close()

        elapsed = int((time.time() - started) * 1000)
        log(
            f"{method} {upstream_path} -> {status} model={model or '-'} "
            f"stream={'yes' if is_stream else 'no'} {elapsed}ms {total}B"
        )


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    global _log_path

    parser = argparse.ArgumentParser(description="OpenCode Go -> WorkBuddy 本地中转")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8787")
    parser.add_argument("--upstream", default="https://opencode.ai", help="上游地址，默认 https://opencode.ai")
    parser.add_argument("--timeout", type=int, default=900, help="上游超时秒数，默认 900")
    parser.add_argument("--no-log", action="store_true", help="不写 relay.log")
    args = parser.parse_args()

    parts = urlsplit(args.upstream)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        print(f"上游地址不合法: {args.upstream}", file=sys.stderr)
        return 2

    RelayHandler.upstream_scheme = parts.scheme
    RelayHandler.upstream_host = parts.hostname
    RelayHandler.upstream_port = parts.port or (443 if parts.scheme == "https" else 80)
    RelayHandler.timeout_seconds = args.timeout

    if not args.no_log:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.log")

    try:
        server = RelayServer((args.host, args.port), RelayHandler)
    except OSError as exc:
        print(f"端口 {args.port} 起不来: {exc}", file=sys.stderr)
        print(f"换一个端口: python3 {sys.argv[0]} --port {args.port + 1}", file=sys.stderr)
        return 1

    log(f"relay {VERSION} listening on http://{args.host}:{args.port}")
    log(f"upstream = {args.upstream}{GO_PREFIX}")
    log(f"WorkBuddy endpoint = http://{args.host}:{args.port}/v1/chat/completions")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
