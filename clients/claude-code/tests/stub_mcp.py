#!/usr/bin/env python3
"""A stub Atlas MCP endpoint for testing the client pack (no Atlas, no network).

It speaks just enough of MCP's streamable HTTP transport for a client to connect and list
tools, and it enforces what the real door enforces first: `POST /mcp/` without
`Authorization: Bearer <the expected token>` is a 401 with `WWW-Authenticate: Bearer`,
before any method runs.

    stub_mcp.py --token-file FILE --port N [--tls-cert C --tls-key K] [--log FILE]

The expected token is read from --token-file (never argv). --log gets one line per request,
`<method> <status>`, and never the header value. Standard library only.
"""
from __future__ import annotations

import argparse
import hmac
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION = "stub-session-1"
TOOLS = [{"name": "atlas_get", "description": "stub", "inputSchema": {"type": "object"}},
         {"name": "atlas_file_work", "description": "stub", "inputSchema": {"type": "object"}}]


def make_handler(expected: str, log_path: str | None):
    want = ("Bearer " + expected).encode()

    def log(line: str) -> None:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):  # keep stderr quiet; --log is the record
            pass

        def _send(self, status: int, body: dict | None = None, extra: dict | None = None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            got = (self.headers.get("Authorization") or "").encode()
            return hmac.compare_digest(got, want)

        def do_GET(self):  # no server-initiated stream in this stub
            log(f"GET {self.path} 405")
            self._send(405, extra={"Allow": "POST"})

        def do_DELETE(self):
            log("DELETE 200")
            self._send(200, {})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if self.path.rstrip("/") != "/mcp":
                log(f"POST {self.path} 404")
                return self._send(404, {"error": "not_found"})
            if not self._authorised():
                log("POST 401")
                return self._send(401, {"error": "unauthorized"},
                                  {"WWW-Authenticate": 'Bearer realm="atlas"'})
            try:
                msg = json.loads(raw or b"{}")
            except ValueError:
                log("POST 400")
                return self._send(400, {"error": "bad_json"})
            method = msg.get("method", "?")
            if "id" not in msg:  # a notification
                log(f"{method} 202")
                return self._send(202)
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "atlas-stub", "version": "0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "ping":
                result = {}
            else:
                log(f"{method} 200-error")
                return self._send(200, {"jsonrpc": "2.0", "id": msg["id"],
                                        "error": {"code": -32601, "message": "not in the stub"}})
            log(f"{method} 200")
            self._send(200, {"jsonrpc": "2.0", "id": msg["id"], "result": result},
                       {"Mcp-Session-Id": SESSION})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--tls-cert")
    ap.add_argument("--tls-key")
    ap.add_argument("--log")
    a = ap.parse_args()
    with open(a.token_file, encoding="utf-8") as f:
        expected = f.readline().strip()
    if not expected:
        print("stub_mcp: empty token file", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(expected, a.log))
    if a.tls_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.tls_cert, a.tls_key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"stub_mcp: listening on {a.host}:{a.port}", file=sys.stderr, flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
