"""
Day 1 — HTTP Fundamentals: Server
Demonstrates: HTTP request/response, GET, POST, headers, status codes, JSON
Run: python server.py
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json


class HTTPHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests and demonstrates core HTTP concepts."""

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Custom-Header", "HelloFromServer")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode() if length else ""

    def do_GET(self):
        print("\n--- INCOMING REQUEST ---")
        print(f"Method: GET")
        print(f"Path: {self.path}")
        print(f"Headers: {dict(self.headers)}")

        if self.path == "/":
            data = {"message": "Welcome to HTTP Fundamentals!", "method": "GET"}
            self._set_headers(200)
            self.wfile.write(json.dumps(data).encode())

        elif self.path == "/users":
            data = {"users": ["Alice", "Bob", "Charlie"], "count": 3}
            self._set_headers(200)
            self.wfile.write(json.dumps(data).encode())

        elif self.path == "/notfound":
            data = {"error": "Resource not found"}
            self._set_headers(404)
            self.wfile.write(json.dumps(data).encode())

        elif self.path == "/error":
            data = {"error": "Internal server error"}
            self._set_headers(500)
            self.wfile.write(json.dumps(data).encode())

        elif self.path == "/redirect":
            self.send_response(301)
            self.send_header("Location", "/")
            self.end_headers()

        else:
            data = {"error": "Not found", "path": self.path}
            self._set_headers(404)
            self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        print("\n--- INCOMING REQUEST ---")
        print(f"Method: POST")
        print(f"Path: {self.path}")
        print(f"Headers: {dict(self.headers)}")
        body = self._read_body()
        print(f"Body: {body}")

        if self.path == "/users":
            try:
                payload = json.loads(body)
                name = payload.get("name", "Unknown")
                data = {
                    "message": f"User {name} created",
                    "user": {"id": 4, "name": name},
                }
                self._set_headers(201)
                self.wfile.write(json.dumps(data).encode())
            except json.JSONDecodeError:
                data = {"error": "Invalid JSON"}
                self._set_headers(400)
                self.wfile.write(json.dumps(data).encode())

        elif self.path == "/echo":
            try:
                payload = json.loads(body)
                self._set_headers(200)
                self.wfile.write(json.dumps(payload).encode())
            except json.JSONDecodeError:
                data = {"error": "Invalid JSON"}
                self._set_headers(400)
                self.wfile.write(json.dumps(data).encode())

        else:
            data = {"error": "Not found", "path": self.path}
            self._set_headers(404)
            self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8000), HTTPHandler)
    print("Server running on http://localhost:8000")
    print("Endpoints:")
    print("  GET  /          -> 200 OK")
    print("  GET  /users     -> 200 OK (list users)")
    print("  GET  /notfound  -> 404 Not Found")
    print("  GET  /error     -> 500 Internal Server Error")
    print("  GET  /redirect  -> 301 Redirect to /")
    print("  POST /users     -> 201 Created (send JSON: {\"name\": \"...\"})")
    print("  POST /echo      -> 200 OK (echoes your JSON back)")
    print()
    server.serve_forever()