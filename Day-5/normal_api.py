#!/usr/bin/env python3
"""Normal API — plain REST service with NO AI behavior.

Endpoints (port 8002):
  GET  /api/health     status check
  GET  /api/users      static user list
  GET  /api/search     search (returns fixed results)
  POST /api/chat       echo-style chat (returns your message back)
  POST /api/echo       echoes the request body

Classic non-AI traits: fixed/static data, echoes input verbatim,
deterministic responses, no streaming, no OpenAI-style fields.
"""

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

USERS = [
    {"id": 1, "name": "Alice"},
    {"id": 2, "name": "Bob"},
    {"id": 3, "name": "Carol"},
]

ITEMS = ["laptop", "keyboard", "mouse", "monitor"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"    [normal] {self.command} {self.path}")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path == "/api/health":
            self.send_json({"status": "ok"})
        elif path == "/api/users":
            self.send_json({"users": USERS})
        elif path == "/api/search":
            q = query.get("q", [""])[0]
            matches = [i for i in ITEMS if q.lower() in i.lower()]
            self.send_json({"query": q, "results": matches})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self.read_json_body()

        if path == "/api/chat":
            message = data.get("message", "")
            if not message:
                messages = data.get("messages")
                if isinstance(messages, list) and messages:
                    message = str(messages[-1].get("content", "")) \
                        if isinstance(messages[-1], dict) else ""
            self.send_json({"reply": f"You said: {message}"})
        elif path == "/api/echo":
            self.send_json({"echo": data})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = 8002
    print(f"[*] Normal API running at http://127.0.0.1:{port}")
    print(f"    GET  /api/health    status check")
    print(f"    GET  /api/users     user list")
    print(f"    GET  /api/search    fixed search results")
    print(f"    POST /api/chat      echo chat (not AI)")
    print(f"    POST /api/echo      body echo")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
