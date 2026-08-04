#!/usr/bin/env python3
"""Mock AI API — mimics an OpenAI-style LLM service for testing the AI Detector.

Endpoints (port 8001):
  POST /api/chat           OpenAI-style chat completion (JSON)
  POST /api/completions    OpenAI-style text completion (JSON)
  POST /api/chat/stream    SSE streaming chat completion (text/event-stream)
  GET  /ws                 WebSocket chat (text frames)
  GET  /api/health         plain health check

The mock never echoes the input back: replies are freshly generated text
with slight randomness (like a real model running at temperature > 0).
"""

import base64
import hashlib
import json
import random
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

MODEL = "mock-llm-7b"

OPENERS = [
    "Great question! ",
    "Here's what I think: ",
    "That's an interesting one. ",
    "As an AI, I can tell you that ",
    "Sure thing! ",
    "Happy to help. ",
    "Let me break that down: ",
    "From my analysis, ",
]

MIDDLES = [
    "the key idea is to focus on the core concepts first",
    "you'd want to approach this step by step",
    "context matters a lot here",
    "there are a few angles worth considering",
    "it depends on your goals and constraints",
    "consistency and clear structure go a long way",
    "a balanced approach usually works best",
    "understanding the fundamentals helps the most",
]

ENDINGS = [
    "Hope that helps!",
    "Let me know if you'd like more detail.",
    "Would you like a deeper dive?",
    "That should get you started.",
    "Feel free to ask a follow-up.",
]

TOPICS = [
    "programming", "design systems", "machine learning", "web development",
    "data pipelines", "system architecture", "product strategy",
    "user experience", "security", "performance tuning",
]


def generate_reply(message: str) -> str:
    """Produce a varied, non-echoing AI-style reply for a user message."""
    topic = TOPICS[hash(message or "") % len(TOPICS)]
    opener = random.choice(OPENERS)
    middle = random.choice(MIDDLES)
    ending = random.choice(ENDINGS)
    return f"{opener}Regarding {topic}: {middle}. {ending}"


def count_tokens(text: str) -> int:
    return max(1, len(text.split()))


def chat_completion(message: str, model: str = MODEL) -> dict:
    content = generate_reply(message)
    return {
        "id": f"chatcmpl-mock-{random.randint(1000, 9999)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": count_tokens(message),
            "completion_tokens": count_tokens(content),
            "total_tokens": count_tokens(message) + count_tokens(content),
        },
    }


def text_completion(prompt: str, model: str = MODEL) -> dict:
    content = generate_reply(prompt)
    return {
        "id": f"cmpl-mock-{random.randint(1000, 9999)}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "text": content,
            "finish_reason": "length",
        }],
        "usage": {
            "prompt_tokens": count_tokens(prompt),
            "completion_tokens": count_tokens(content),
            "total_tokens": count_tokens(prompt) + count_tokens(content),
        },
    }


def ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_read_frame(f):
    """Read one WebSocket frame from a stream. Returns (opcode, payload)."""
    header = f.read(2)
    if len(header) < 2:
        return None, None
    opcode = header[0] & 0x0F
    masked = header[1] & 0x80
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(f.read(2), "big")
    elif length == 127:
        length = int.from_bytes(f.read(8), "big")
    mask = f.read(4) if masked else b""
    payload = b""
    while len(payload) < length:
        chunk = f.read(length - len(payload))
        if not chunk:
            break
        payload += chunk
    if masked:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def ws_send_frame(f, payload: bytes, opcode: int = 0x1):
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += n.to_bytes(2, "big")
    else:
        header.append(127)
        header += n.to_bytes(8, "big")
    f.write(bytes(header) + payload)
    f.flush()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"    [mock-ai] {self.command} {self.path}")

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

    # ── GET ──
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/health":
            self.send_json({"status": "ok", "service": "mock-llm"})
        elif path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self.handle_websocket()
        else:
            self.send_json({"error": "not found"}, 404)

    # ── POST ──
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self.read_json_body()

        if path == "/api/chat":
            if data.get("stream"):
                self.stream_chat_completion(data)
            else:
                message = self.extract_last_message(data)
                time.sleep(random.uniform(0.15, 0.4))  # simulated inference
                self.send_json(chat_completion(message, data.get("model", MODEL)))
        elif path == "/api/completions":
            prompt = data.get("prompt", "")
            time.sleep(random.uniform(0.1, 0.3))
            self.send_json(text_completion(prompt, data.get("model", MODEL)))
        elif path == "/api/chat/stream":
            self.stream_chat_completion(data)
        else:
            self.send_json({"error": "not found"}, 404)

    def extract_last_message(self, data: dict) -> str:
        messages = data.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                return str(last.get("content", ""))
        return ""

    def stream_chat_completion(self, data: dict):
        message = self.extract_last_message(data)
        model = data.get("model", MODEL)
        content = generate_reply(message)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def sse(obj):
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
            self.wfile.flush()

        sse({"id": f"chatcmpl-mock-{random.randint(1000, 9999)}",
             "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant"},
                          "finish_reason": None}]})
        for word in content.split():
            sse({"id": "chatcmpl-mock-stream", "object": "chat.completion.chunk",
                 "model": model,
                 "choices": [{"index": 0, "delta": {"content": word + " "},
                              "finish_reason": None}]})
            time.sleep(random.uniform(0.03, 0.06))
        sse({"id": "chatcmpl-mock-stream", "object": "chat.completion.chunk",
             "model": model,
             "choices": [{"index": 0, "delta": {"content": ""},
                          "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # ── WebSocket ──
    def handle_websocket(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        self.send_response_only(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        print("    [mock-ai] WebSocket upgraded")

        try:
            while True:
                opcode, payload = ws_read_frame(self.rfile)
                if opcode is None or opcode == 0x8:  # closed / close frame
                    break
                text = payload.decode("utf-8", errors="replace")
                print(f"    [mock-ai] ws recv: {text[:60]!r}")
                reply = generate_reply(text)
                ws_send_frame(self.wfile, reply.encode())
        except (ConnectionError, OSError, TimeoutError):
            pass


if __name__ == "__main__":
    port = 8001
    print(f"[*] Mock AI API running at http://127.0.0.1:{port}")
    print(f"    POST /api/chat          chat completion (JSON)")
    print(f"    POST /api/completions   text completion (JSON)")
    print(f"    POST /api/chat/stream   streaming chat (SSE)")
    print(f"    GET  /ws                websocket chat")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
