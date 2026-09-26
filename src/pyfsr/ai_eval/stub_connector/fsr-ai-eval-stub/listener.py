"""Scenario-driven stub MCP servers for AI-investigation evaluation.

A dependency-free (stdlib only) Model Context Protocol server speaking the
*streamable HTTP* transport with plain JSON responses. One process serves every
backend in the fixtures file, each at its own path::

    POST http://127.0.0.1:<port>/mcp/<server_key>

Each backend is registered in FortiSOAR as its own external MCP server, so the
agents see several independent tool providers (a SIEM, an EDR, an identity
store, a CMDB, ... plus distractors) whose answers are fixed per scenario. That
fixed ground truth is what makes an investigation scoreable.

Fixtures (JSON, reloaded whenever the file changes)::

    {"servers": {"<key>": {
        "name": "...", "instructions": "...", "envelope": "fsr",
        "tools": [{
            "name": "siem_search_events",
            "description": "...",
            "params": {"query": {"type": "string", "description": "..."}},
            "required": ["query"],
            "rules": [{"match": {"query": "198.51.100.33|ws-fin-0417"}, "result": {...}}],
            "default": {"results": []}}]}}}

A rule matches when every ``match`` entry hits: the argument's value (stringified,
lower-cased) contains one of the ``|``-separated alternatives. The key ``*``
searches all arguments at once. The first matching rule wins; otherwise
``default`` is returned. Every call is appended to a JSONL call log (server,
tool, args, which rule answered) -- ground truth independent of FortiSOAR's
own traces.

Run standalone: ``python listener.py --port 18900 --fixtures fixtures.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_VERSION = "1.0.0"


# ------------------------------------------------------------------ matching
def _norm(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, sort_keys=True, default=str).lower()


def rule_hits(match: dict[str, str], args: dict[str, Any]) -> bool:
    """True when every ``match`` entry hits its argument (see module docstring)."""
    for key, pattern in (match or {}).items():
        haystack = _norm(args) if key == "*" else _norm(args.get(key, ""))
        alternatives = [p.strip().lower() for p in str(pattern).split("|") if p.strip()]
        if not any(alt in haystack for alt in alternatives):
            return False
    return True


def answer(tool: dict[str, Any], args: dict[str, Any]) -> tuple[Any, Any]:
    """Return ``(result, rule_index_or_"default")`` for one tool call."""
    for index, rule in enumerate(tool.get("rules") or []):
        if rule_hits(rule.get("match") or {}, args):
            return rule.get("result"), index
    return tool.get("default", {"results": []}), "default"


def envelope(result: Any) -> dict[str, Any]:
    """Wrap a result in fsr-ai's standard tool response, as the built-in servers do.

    fsr-ai parses every MCP result as ``{"status", "result", "error"}``. Anything
    else comes back to the agent as the *parsed* object instead of the text, and
    agents that ``json.loads`` it again (e.g. Query Endpoint on 8.0.1) crash with
    ``TypeError``. A ``dict`` result is re-serialised by fsr-ai; anything else is
    handed over as-is, so non-dicts are sent pre-serialised.
    """
    return {
        "status": "success",
        "result": result if isinstance(result, dict) else json.dumps(result, default=str),
        "error": None,
    }


def input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    props = {}
    for name, spec in (tool.get("params") or {}).items():
        spec = spec if isinstance(spec, dict) else {"description": str(spec)}
        props[name] = {"type": spec.get("type", "string"), "description": spec.get("description", "")}
    return {"type": "object", "properties": props, "required": list(tool.get("required") or [])}


# ------------------------------------------------------------------ state
class Fixtures:
    """Fixture file, re-read when its mtime changes (so load_fixtures needs no restart)."""

    def __init__(self, path: str):
        self.path = path
        self._mtime = -1.0
        self._data: dict[str, Any] = {"servers": {}}
        self._lock = threading.Lock()

    def servers(self) -> dict[str, Any]:
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path)
            except OSError:
                return self._data.get("servers", {})
            if mtime != self._mtime:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
                self._mtime = mtime
            return self._data.get("servers", {})


class CallLog:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def write(self, row: dict[str, Any]) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


# ------------------------------------------------------------------ JSON-RPC
def _result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_rpc(server: dict[str, Any], key: str, msg: dict[str, Any], log: CallLog | None) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; ``None`` for notifications."""
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if msg_id is None:
        return None  # notifications/initialized etc.
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(
            msg_id,
            {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": server.get("name", key), "version": SERVER_VERSION},
                "instructions": server.get("instructions", ""),
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        tools = [
            {"name": t["name"], "description": t.get("description", ""), "inputSchema": input_schema(t)}
            for t in server.get("tools") or []
        ]
        return _result(msg_id, {"tools": tools})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tool = next((t for t in server.get("tools") or [] if t["name"] == name), None)
        if tool is None:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        result, rule = answer(tool, args)
        if log:
            log.write({"ts": time.time(), "server": key, "tool": name, "args": args, "rule": rule})
        text = json.dumps(envelope(result) if server.get("envelope", "fsr") == "fsr" else result, default=str)
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
    if method in ("resources/list", "prompts/list"):
        return _result(msg_id, {method.split("/")[0]: []})
    return _error(msg_id, -32601, f"Method not found: {method}")


def make_handler(fixtures: Fixtures, log: CallLog | None, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:  # quiet
            pass

        def _send(self, status: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _server(self) -> tuple[str, dict[str, Any]] | None:
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if len(parts) != 2 or parts[0] != "mcp":
                return None
            srv = fixtures.servers().get(parts[1])
            return (parts[1], srv) if srv is not None else None

        def _authorized(self) -> bool:
            return not token or self.headers.get("Authorization", "") == f"Bearer {token}"

        def do_GET(self) -> None:  # noqa: N802 - no server-initiated SSE stream
            if self.path.rstrip("/") == "/health":
                self._send(200, {"status": "ok", "pid": os.getpid(), "servers": sorted(fixtures.servers())})
                return
            self._send(405, {"error": "SSE stream not supported; POST JSON-RPC"})

        def do_DELETE(self) -> None:  # noqa: N802 - session end: stateless, nothing to do
            self._send(200, {})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            found = self._server()
            if found is None:
                self._send(404, {"error": f"no MCP server at {self.path}"})
                return
            key, srv = found
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                self._send(400, _error(None, -32700, "Parse error"))
                return
            batch = payload if isinstance(payload, list) else [payload]
            replies = [r for r in (handle_rpc(srv, key, m, log) for m in batch if isinstance(m, dict)) if r]
            if not replies:
                self._send(202)
            else:
                self._send(200, replies if isinstance(payload, list) else replies[0])

    return Handler


def serve(port: int, fixtures_path: str, log_path: str | None, host: str = "127.0.0.1", token: str | None = None):
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(Fixtures(fixtures_path), CallLog(log_path) if log_path else None, token)
    )
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=18900)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--call-log")
    ap.add_argument("--pid-file")
    args = ap.parse_args(argv)
    token = os.environ.get("EVAL_STUB_TOKEN") or None
    httpd = serve(args.port, args.fixtures, args.call_log, args.host, token)
    if args.pid_file:
        with open(args.pid_file, "w") as f:
            f.write(str(os.getpid()))
    print(f"eval stub MCP listening on {args.host}:{args.port}", file=sys.stderr, flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
