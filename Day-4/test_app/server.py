#!/usr/bin/env python3
"""Test application with API endpoints the browser calls via fetch/XHR."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import urllib.parse

BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Test App — Day 4</title>
  <style>
    body { font-family: sans-serif; max-width: 640px; margin: 40px auto; }
    button { display: block; margin: 8px 0; padding: 8px 14px; font-size: 1rem; }
    pre { background: #f4f4f4; padding: 10px; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>Test App</h1>
  <p id="status">Loading&hellip;</p>
  <button id="btn-login">Login (XMLHttpRequest)</button>
  <button id="btn-search">Search (fetch)</button>
  <button id="btn-chat">Chat (fetch POST)</button>
  <button id="btn-admin">Hidden admin (fetch)</button>
  <pre id="output"></pre>
  <script src="/static/app.js"></script>
</body>
</html>
"""

APP_JS = """
const output = document.getElementById("output");
const statusEl = document.getElementById("status");
function show(label, data) {
  output.textContent = `[${label}] ${JSON.stringify(data, null, 2)}`;
}

function fetchJson(url, opts) {
  return fetch(url, opts).then(r => r.json());
}

window.addEventListener("load", () => {
  fetchJson("/api/health").then(d => {
    statusEl.textContent = `Server: ${d.status} (v${d.version})`;
  });
});

document.getElementById("btn-login").addEventListener("click", () => {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/login");
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.onload = () => show("login", JSON.parse(xhr.responseText));
  xhr.send(JSON.stringify({ username: "admin", password: "hunter2" }));
});

document.getElementById("btn-search").addEventListener("click", () => {
  fetchJson(`/api/search?q=${encodeURIComponent("api scanner")}`)
    .then(d => show("search", d));
});

document.getElementById("btn-chat").addEventListener("click", () => {
  fetchJson("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "hello bot" }),
  }).then(d => show("chat", d));
});

document.getElementById("btn-admin").addEventListener("click", () => {
  fetchJson("/api/admin/users", { method: "GET" })
    .then(d => show("admin", d))
    .catch(e => show("admin", { error: String(e) }));
});
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"    [server] {self.command} {self.path}")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            body = BASE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/static/app.js":
            body = APP_JS.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/health":
            self.send_json({"status": "ok", "version": "1.4.2"})
        elif path == "/api/search":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            self.send_json({"query": q, "results": ["playwright", "fetch", "xhr"]})
        elif path == "/api/admin/users":
            self.send_json({"error": "forbidden"}, 403)
        elif path == "/api/logout":
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        if path == "/api/login":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            if data.get("username") == "admin" and data.get("password") == "hunter2":
                self.send_json({"token": "fake-jwt-123", "user": data.get("username")})
            else:
                self.send_json({"error": "invalid credentials"}, 401)
        elif path == "/api/chat":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            self.send_json({"reply": f"You said: {data.get('message', '')}"})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    port = 8000
    print(f"[*] Test app running at http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
