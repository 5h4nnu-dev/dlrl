"""
Day 1 — HTTP Fundamentals: Client
Demonstrates: GET, POST, headers, status codes, JSON parsing
Run: python client.py (start server.py first in another terminal)
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:8000"


def request(method, path, data=None, headers=None):
    """Low-level HTTP request showing request/response fundamentals."""
    url = f"{BASE}{path}"
    headers = headers or {}
    body = None

    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    print(f"\n{'='*50}")
    print(f"REQUEST: {method} {path}")
    print(f"  Headers: {dict(req.headers)}")
    if body:
        print(f"  Body: {body.decode()}")

    try:
        with urllib.request.urlopen(req) as resp:
            print(f"RESPONSE: {resp.status} {resp.reason}")
            print(f"  Headers: {dict(resp.getheaders())}")
            response_body = resp.read().decode()
            print(f"  Body: {response_body}")
            print(f"{'='*50}")
            return resp.status, json.loads(response_body)
    except urllib.error.HTTPError as e:
        print(f"RESPONSE: {e.code} {e.reason}")
        print(f"  Headers: {dict(e.headers)}")
        response_body = e.read().decode()
        print(f"  Body: {response_body}")
        print(f"{'='*50}")
        return e.code, json.loads(response_body)


def demo_get():
    """GET requests — retrieve data, no body."""
    print("\n>>> GET: Retrieve data from the server")
    print(">>> The server returns JSON with a message.")

    # GET / — basic request
    status, data = request("GET", "/")
    assert status == 200
    assert data["method"] == "GET"
    assert "HTTP" in data["message"]
    print(f">>> Status code {status} means OK")

    # GET /users — list resource
    status, data = request("GET", "/users")
    assert status == 200
    assert len(data["users"]) == 3
    print(f">>> Got {data['count']} users")

    # GET /notfound — 404
    status, data = request("GET", "/notfound")
    assert status == 404
    print(f">>> Status code {status} means Not Found")


def demo_post():
    """POST requests — send data in the body as JSON."""
    print("\n>>> POST: Send data to the server (JSON body)")
    print(">>> The server creates a resource and returns it.")

    # POST /users — create a resource
    status, data = request("POST", "/users", {"name": "Diana"})
    assert status == 201
    assert data["user"]["name"] == "Diana"
    print(f">>> Status code {status} means Created")

    # POST /echo — echo back
    payload = {"message": "Hello HTTP!", "count": 42}
    status, data = request("POST", "/echo", payload)
    assert status == 200
    assert data == payload
    print(f">>> Server echoed back our data exactly")


def demo_headers():
    """Request/Response headers — metadata sent with every HTTP exchange."""
    print("\n>>> HEADERS: Metadata sent with requests and responses")
    print(">>> Request headers tell server about the client.")
    print(">>> Response headers tell client about the server.")

    status, data = request(
        "GET",
        "/",
        headers={"User-Agent": "HTTP-Learner/1.0", "Accept": "application/json"},
    )
    assert status == 200
    print(f">>> Sent custom User-Agent and Accept headers")


def demo_status_codes():
    """HTTP status codes — server tells client what happened."""
    print("\n>>> STATUS CODES: Server signals the outcome of a request")
    print(">>> 1xx: Informational | 2xx: Success | 3xx: Redirection")
    print(">>> 4xx: Client Error  | 5xx: Server Error")

    # 200 OK
    status, _ = request("GET", "/")
    print(f"   200 OK — Standard success response")

    # 201 Created
    status, _ = request("POST", "/users", {"name": "Eve"})
    print(f"   201 Created — Resource was created")

    # 301 Moved Permanently
    print("   301 Moved Permanently — Resource has moved (check redirect)")
    url = f"{BASE}/redirect"
    req = urllib.request.Request(url, method="GET")
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler()
        )
        resp = opener.open(req)
        print(f"     -> Followed redirect to {resp.url}")
    except urllib.error.HTTPError as e:
        print(f"     -> Status: {e.code}")

    # 400 Bad Request
    status, data = request("POST", "/users", "not-json")
    assert status == 400
    print(f"   400 Bad Request — Malformed request (invalid JSON)")

    # 404 Not Found
    status, _ = request("GET", "/notfound")
    print(f"   404 Not Found — Resource does not exist")

    # 500 Internal Server Error
    status, _ = request("GET", "/error")
    print(f"   500 Internal Server Error — Something broke on the server")


def demo_json():
    """JSON — lightweight data format used in HTTP APIs."""
    print("\n>>> JSON: JavaScript Object Notation")
    print(">>> The lingua franca of modern HTTP APIs.")

    python_data = {
        "string": "hello",
        "number": 42,
        "float": 3.14,
        "boolean": True,
        "null_value": None,
        "array": [1, 2, 3],
        "object": {"nested": "value"},
    }

    json_string = json.dumps(python_data, indent=2)
    print(f"Python -> JSON:")
    print(json_string)

    parsed_back = json.loads(json_string)
    print(f"\nJSON -> Python (type: {type(parsed_back).__name__}):")
    print(f"  {parsed_back}")

    # Send JSON to server and get it back
    status, data = request("POST", "/echo", python_data)
    assert status == 200
    assert data["string"] == "hello"
    assert data["number"] == 42
    assert data["boolean"] is True
    assert data["null_value"] is None
    print(f">>> JSON round-trip successful!")


def demo_request_response():
    """Anatomy of an HTTP request and response."""
    print("\n>>> HTTP REQUEST ANATOMY:")
    print("    POST /users HTTP/1.1")
    print("    Host: localhost:8000")
    print("    Content-Type: application/json")
    print("    User-Agent: HTTP-Learner/1.0")
    print("    {")
    print('      "name": "Diana"')
    print("    }")
    print("    ^- Request Line (Method + Path + Version)")
    print("    ^- Headers (key: value)")
    print("    ^- Body (optional, for POST/PUT)")

    print("\n>>> HTTP RESPONSE ANATOMY:")
    print("    HTTP/1.1 201 Created")
    print("    Content-Type: application/json")
    print("    X-Custom-Header: HelloFromServer")
    print("    {")
    print('      "message": "User Diana created",')
    print('      "user": {"id": 4, "name": "Diana"}')
    print("    }")
    print("    ^- Status Line (Version + Status Code + Reason)")
    print("    ^- Headers (key: value)")
    print("    ^- Body (JSON data)")


if __name__ == "__main__":
    print("=" * 50)
    print("HTTP FUNDAMENTALS — DAY 1")
    print("=" * 50)
    print("\nMake sure server.py is running in another terminal!")
    print("  $ python server.py")
    input("\nPress Enter to start demos...")

    demo_request_response()
    demo_get()
    demo_post()
    demo_headers()
    demo_status_codes()
    demo_json()

    print("\n" + "=" * 50)
    print("ALL DEMOS COMPLETE 🎉" if False else "\nAll demos complete. Review the output above.")
    print("=" * 50)