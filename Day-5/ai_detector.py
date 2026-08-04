#!/usr/bin/env python3
"""AI Detector — decides whether an HTTP/WebSocket endpoint is an AI (LLM) API.

Pipeline (run once per endpoint):

    Endpoint
      |-- [1] Request Analysis  : path hints, body schema, auth headers
      |-- [2] Response Analysis : JSON shape, OpenAI markers, SSE events
      |-- [3] Behavior Analysis : determinism, echo check, streaming, latency
      v
    [4] AI Confidence Score (0-100) + Type + Streaming flag

Probes sent to every endpoint:
    GET
    POST chat-style      {"model", "messages", "temperature", "stream": false}  x2
    POST completion-style{"prompt", "max_tokens", "temperature"}
    POST stream-style    chat body with "stream": true
    WebSocket handshake  (only for ws-looking paths)

Usage:
    python ai_detector.py <base_url> [report.json] [endpoint ...]
"""

import base64
import difflib
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.parse

CHAT_PROBE_MESSAGE = "hello there, what do you think about the weather today?"

CANDIDATES = [
    "/api/health",
    "/api/chat",
    "/api/completions",
    "/api/chat/stream",
    "/v1/chat/completions",
    "/api/generate",
    "/api/ask",
    "/api/search",
    "/ws",
]

# ── Signal weights ──────────────────────────────────────────────────────────

SIGNAL_WEIGHTS = {
    # request analysis
    "path_hint": 12,          # /chat, /completions, /generate, /ai/, /v1/...
    "body_messages": 14,      # messages[] with role/content
    "body_llm_fields": 12,    # prompt / max_tokens / temperature / stream / model
    "auth_bearer": 3,         # Authorization: Bearer ...
    # response analysis
    "openai_chat_shape": 18,  # choices[0].message.content
    "openai_text_shape": 14,  # choices[0].text
    "openai_stream_shape": 10,  # choices[0].delta.content (SSE chunk)
    "generic_content": 4,     # top-level content/output/completion field
    "usage_tokens": 8,        # usage.prompt_tokens / completion_tokens
    "finish_reason": 6,
    "id_marker": 5,           # chatcmpl- / cmpl- ids
    "model_field": 4,
    "object_field": 4,        # object: chat.completion / text_completion
    "llm_phrases": 8,         # typical LLM phrasings ("As an AI", ...)
    "verbose_text": 12,       # long multi-sentence generated text
    # behavior analysis
    "sse": 15,                # text/event-stream content type
    "stream_chunks": 10,      # incremental delivery observed
    "stream_ignored": -6,     # asked for stream, got plain JSON
    "variance": 8,            # identical calls produce different replies
    "deterministic": -10,     # identical calls, byte-identical replies
    "echo": -25,              # reply repeats the input back verbatim
    "latency": 5,             # slow generation-like responses
    # websocket
    "ws_upgraded": 14,
    "ws_reply": 10,
}

AI_PATH_RE = re.compile(
    r"/(chat|completions?|generate|ask|ai/|llm|infer|assistant|v\d+/|embed)", re.I
)
LLM_FIELD_RE = re.compile(
    r"^(prompt|max_tokens|temperature|top_p|stream|model|stop|messages|logprobs)$"
)
LLM_PHRASES = (
    "as an ai", "i'm sorry", "i cannot", "i'd be happy", "of course",
    "great question", "let me know if", "hope that helps", "happy to help",
    "would you like me to", "here's what i think", "in summary",
)
ID_MARKERS = ("chatcmpl-", "cmpl-")
OBJECT_MARKERS = ("chat.completion", "text_completion")


def http_probe(base: str, path: str, method: str = "GET", body: dict | None = None,
               stream_detect: bool = False, timeout: float = 10.0) -> dict:
    """Send one HTTP probe, capturing status, headers, body and chunk timing."""
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    headers = {"User-Agent": "AIDetector/1.0", "Accept": "application/json"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    t_start = time.time()
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        chunks: list[bytes] = []
        t_first = None
        while True:
            chunk = resp.read1(4096) if stream_detect else resp.read(4096)
            if not chunk:
                break
            if t_first is None:
                t_first = time.time()
            chunks.append(chunk)
        t_end = time.time()
        body_bytes = b"".join(chunks)
        conn.close()
        return {
            "ok": True,
            "status": status,
            "headers": resp_headers,
            "body": body_bytes.decode("utf-8", errors="replace"),
            "chunks": len(chunks),
            "total_ms": round((t_end - t_start) * 1000),
            "first_ms": round((t_first - t_start) * 1000) if t_first else None,
            "streaming": t_first is not None and (t_end - t_first) > 0.05 and len(chunks) >= 3,
        }
    except Exception as e:
        conn.close()
        return {"ok": False, "status": None, "headers": {}, "body": "",
                "chunks": 0, "total_ms": None, "first_ms": None,
                "streaming": False, "error": str(e)}


def ws_probe(base: str, path: str, timeout: float = 5.0) -> dict:
    """Minimal WebSocket client: handshake, send a masked text frame, read replies."""
    parsed = urllib.parse.urlparse(base)
    host, port = parsed.hostname, parsed.port or 80
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(request.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            data = s.recv(4096)
            if not data:
                break
            buf += data
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("utf-8", "replace").split("\r\n")
        if "101" not in lines[0]:
            s.close()
            return {"ok": True, "upgraded": False, "status": lines[0],
                    "replies": []}
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

        # send masked text frame: {"role": "user", "message": "..."}
        message = {"role": "user", "message": CHAT_PROBE_MESSAGE}
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = bytes([0x81, 0x80 | n])
        elif n < 65536:
            header = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big")
        s.sendall(header + mask + masked)
        s.sendall(header + mask + masked)  # same message twice (determinism)

        replies = []
        while len(replies) < 2:
            try:
                h = s.recv(2)
            except socket.timeout:
                break
            if len(h) < 2:
                break
            opcode = h[0] & 0x0F
            length = h[1] & 0x7F
            if length == 126:
                length = int.from_bytes(s.recv(2), "big")
            elif length == 127:
                length = int.from_bytes(s.recv(8), "big")
            data = b""
            while len(data) < length:
                c = s.recv(length - len(data))
                if not c:
                    break
                data += c
            if opcode == 0x8:
                break
            if opcode == 0x1:
                replies.append(data.decode("utf-8", "replace"))
        s.close()
        return {"ok": True, "upgraded": True, "status": "101",
                "accept": headers.get("sec-websocket-accept", ""),
                "replies": replies}
    except Exception as e:
        return {"ok": False, "upgraded": False, "status": None,
                "replies": [], "error": str(e)}


# ── [1] Request Analysis ────────────────────────────────────────────────────

def request_analysis(path: str, chat_body: dict) -> list[dict]:
    signals = []

    if AI_PATH_RE.search(path.lower()):
        signals.append({
            "signal": "path_hint", "weight": SIGNAL_WEIGHTS["path_hint"],
            "detail": f"path looks AI-ish: {path}"})

    keys = set(chat_body.keys())
    messages = chat_body.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        if "role" in messages[0] and "content" in messages[0]:
            signals.append({
                "signal": "body_messages", "weight": SIGNAL_WEIGHTS["body_messages"],
                "detail": "body has messages[] with role/content"})

    llm_fields = sorted(k for k in keys if LLM_FIELD_RE.match(k))
    if len(llm_fields) >= 2:
        signals.append({
            "signal": "body_llm_fields", "weight": SIGNAL_WEIGHTS["body_llm_fields"],
            "detail": f"body has LLM params: {', '.join(llm_fields)}"})

    return signals


# ── [2] Response Analysis ───────────────────────────────────────────────────

def extract_content(data) -> tuple[str | None, str | None]:
    """Return (content, shape) where shape is chat/text/delta/generic."""
    if not isinstance(data, dict):
        return None, None
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            msg = c0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"], "chat"
            delta = c0.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                return delta["content"], "delta"
            if isinstance(c0.get("text"), str):
                return c0["text"], "text"
    for key in ("content", "output", "completion", "answer"):
        value = data.get(key)
        if isinstance(value, str):
            return value, "generic"
    if isinstance(data.get("reply"), str):
        return data["reply"], "generic"
    return None, None


def parse_sse(body: str) -> tuple[list[dict], bool]:
    """Parse SSE lines: returns (json events, saw [DONE])."""
    events, done = [], False
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    return events, done


def response_analysis(resp: dict) -> list[dict]:
    signals = []
    content_type = resp["headers"].get("content-type", "")

    if content_type.startswith("text/event-stream"):
        signals.append({
            "signal": "sse", "weight": SIGNAL_WEIGHTS["sse"],
            "detail": f"content-type: {content_type}"})

    data = None
    try:
        data = json.loads(resp["body"]) if resp["body"] else None
    except json.JSONDecodeError:
        data = None

    if data is not None:
        content, shape = extract_content(data)
        if shape == "chat":
            signals.append({
                "signal": "openai_chat_shape", "weight": SIGNAL_WEIGHTS["openai_chat_shape"],
                "detail": "choices[0].message.content present"})
        elif shape == "text":
            signals.append({
                "signal": "openai_text_shape", "weight": SIGNAL_WEIGHTS["openai_text_shape"],
                "detail": "choices[0].text present"})
        elif shape == "delta":
            signals.append({
                "signal": "openai_stream_shape", "weight": SIGNAL_WEIGHTS["openai_stream_shape"],
                "detail": "choices[0].delta.content present"})
        elif shape == "generic":
            signals.append({
                "signal": "generic_content", "weight": SIGNAL_WEIGHTS["generic_content"],
                "detail": "top-level text field present"})

        if isinstance(data.get("id"), str) and data["id"].startswith(ID_MARKERS):
            signals.append({
                "signal": "id_marker", "weight": SIGNAL_WEIGHTS["id_marker"],
                "detail": f"id={data['id'][:20]}..."})
        if isinstance(data.get("model"), str):
            signals.append({
                "signal": "model_field", "weight": SIGNAL_WEIGHTS["model_field"],
                "detail": f"model={data['model']}"})
        if isinstance(data.get("object"), str) and data["object"] in OBJECT_MARKERS:
            signals.append({
                "signal": "object_field", "weight": SIGNAL_WEIGHTS["object_field"],
                "detail": f"object={data['object']}"})
        usage = data.get("usage")
        if isinstance(usage, dict) and any(k in usage for k in
                                           ("prompt_tokens", "completion_tokens", "total_tokens")):
            signals.append({
                "signal": "usage_tokens", "weight": SIGNAL_WEIGHTS["usage_tokens"],
                "detail": "usage token counts present"})

        choices = data.get("choices")
        if (isinstance(choices, list) and choices
                and isinstance(choices[0], dict)
                and "finish_reason" in choices[0]):
            signals.append({
                "signal": "finish_reason", "weight": SIGNAL_WEIGHTS["finish_reason"],
                "detail": f"finish_reason={choices[0]['finish_reason']}"})

    # SSE events carry their own OpenAI markers
    if content_type.startswith("text/event-stream"):
        events, done = parse_sse(resp["body"])
        stream_content = ""
        for ev in events:
            c, shape = extract_content(ev)
            if shape == "delta" and c:
                stream_content += c
            if shape is not None and shape != "generic":
                signals.append({
                    "signal": "openai_stream_shape",
                    "weight": SIGNAL_WEIGHTS["openai_stream_shape"],
                    "detail": f"SSE event shape={shape}"})
                break
        if done:
            signals.append({
                "signal": "finish_reason", "weight": SIGNAL_WEIGHTS["finish_reason"],
                "detail": "SSE stream terminated with [DONE]"})
        if stream_content:
            text_signals, _ = text_analysis(stream_content)
            signals.extend(text_signals)
    elif data is not None:
        content, _ = extract_content(data)
        if content:
            text_signals, _ = text_analysis(content)
            signals.extend(text_signals)

    return signals


def text_analysis(text: str) -> tuple[list[dict], str]:
    """Detect LLM-style phrasing and verbosity in a plain-text reply."""
    signals = []
    lowered = text.lower()
    hits = [p for p in LLM_PHRASES if p in lowered]
    if hits:
        signals.append({
            "signal": "llm_phrases", "weight": SIGNAL_WEIGHTS["llm_phrases"],
            "detail": f"LLM phrasing: {', '.join(hits)}"})
    if len(text.split()) >= 15 and text.count(".") >= 2:
        signals.append({
            "signal": "verbose_text", "weight": SIGNAL_WEIGHTS["verbose_text"],
            "detail": f"{len(text.split())} words, multi-sentence reply"})
    return signals, text


# ── [3] Behavior Analysis ───────────────────────────────────────────────────

def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def behavior_analysis(base: str, path: str, chat_a: dict, chat_b: dict,
                      stream_resp: dict, ws_result: dict) -> list[dict]:
    signals = []
    content_a = ""
    content_b = ""

    for probe, out in ((chat_a, "_a"), (chat_b, "_b")):
        try:
            data = json.loads(probe["body"]) if probe["body"] else None
            c, _ = extract_content(data) if data is not None else (None, None)
            if out == "_a" and c:
                content_a = c
            elif out == "_b" and c:
                content_b = c
        except Exception:
            pass

    input_text = normalize(CHAT_PROBE_MESSAGE)

    if content_a:
        if len(input_text) > 8 and input_text in normalize(content_a):
            signals.append({
                "signal": "echo", "weight": SIGNAL_WEIGHTS["echo"],
                "detail": "reply repeats the input back verbatim"})
        if len(content_a.split()) >= 15 and content_a.count(".") >= 2:
            signals.append({
                "signal": "verbose_text", "weight": SIGNAL_WEIGHTS["verbose_text"],
                "detail": f"{len(content_a.split())} words, multi-sentence reply"})

    if content_a and content_b:
        ratio = difflib.SequenceMatcher(None, content_a, content_b).ratio()
        if ratio >= 0.95:
            signals.append({
                "signal": "deterministic", "weight": SIGNAL_WEIGHTS["deterministic"],
                "detail": f"identical calls → identical reply (ratio {ratio:.2f})"})
        elif ratio <= 0.85:
            signals.append({
                "signal": "variance", "weight": SIGNAL_WEIGHTS["variance"],
                "detail": f"identical calls → different replies (ratio {ratio:.2f})"})

    if chat_a.get("total_ms") is not None and chat_a["total_ms"] >= 500:
        signals.append({
            "signal": "latency", "weight": SIGNAL_WEIGHTS["latency"],
            "detail": f"generation-like latency: {chat_a['total_ms']} ms"})

    if stream_resp.get("ok") and stream_resp.get("status") is not None \
            and stream_resp["status"] < 400:
        streamed = (stream_resp["headers"].get("content-type", "")
                    .startswith("text/event-stream"))
        if stream_resp["streaming"]:
            signals.append({
                "signal": "stream_chunks", "weight": SIGNAL_WEIGHTS["stream_chunks"],
                "detail": f"{stream_resp['chunks']} chunks over "
                          f"{stream_resp['total_ms']} ms"})
        elif streamed and stream_resp["chunks"] >= 3:
            signals.append({
                "signal": "stream_chunks", "weight": SIGNAL_WEIGHTS["stream_chunks"],
                "detail": "SSE delivered in multiple data events"})
        elif not streamed:
            signals.append({
                "signal": "stream_ignored", "weight": SIGNAL_WEIGHTS["stream_ignored"],
                "detail": "asked for stream:true but got plain JSON"})

    # websocket behavior
    if ws_result.get("upgraded"):
        signals.append({
            "signal": "ws_upgraded", "weight": SIGNAL_WEIGHTS["ws_upgraded"],
            "detail": "WebSocket handshake succeeded (101)"})
        if ws_result.get("replies"):
            signals.append({
                "signal": "ws_reply", "weight": SIGNAL_WEIGHTS["ws_reply"],
                "detail": f"got {len(ws_result['replies'])} text replies over WS"})
            replies = ws_result["replies"]
            joined = " ".join(replies)
            if len(input_text) > 8 and input_text in normalize(joined):
                signals.append({
                    "signal": "echo", "weight": SIGNAL_WEIGHTS["echo"],
                    "detail": "WS reply repeats the input back verbatim"})
            ws_text_signals, _ = text_analysis(joined)
            signals.extend(ws_text_signals)
            if len(replies) >= 2:
                ratio = difflib.SequenceMatcher(None, replies[0], replies[1]).ratio()
                if ratio >= 0.95:
                    signals.append({
                        "signal": "deterministic", "weight": SIGNAL_WEIGHTS["deterministic"],
                        "detail": f"WS replies identical (ratio {ratio:.2f})"})
                elif ratio <= 0.85:
                    signals.append({
                        "signal": "variance", "weight": SIGNAL_WEIGHTS["variance"],
                        "detail": f"WS replies differ (ratio {ratio:.2f})"})

    return signals


# ── [4] Scoring + classification ────────────────────────────────────────────

def classify_endpoint(path: str, request_signals: list[dict],
                      response_signals: list[dict],
                      behavior_signals: list[dict],
                      ws_result: dict) -> tuple[str, bool]:
    names = [s["signal"] for s in request_signals + response_signals + behavior_signals]
    streaming = "sse" in names or "stream_chunks" in names
    is_ws = "ws_upgraded" in names and "ws_reply" in names

    if is_ws or "openai_chat_shape" in names or "openai_stream_shape" in names:
        kind = "Chat"
    elif "openai_text_shape" in names:
        kind = "Completion"
    elif "body_messages" in names:
        kind = "Chat"
    elif "body_llm_fields" in names:
        kind = "Completion"
    else:
        kind = "Other"

    if streaming:
        kind = f"Streaming {kind}" if kind != "Other" else "Streaming"

    return kind, streaming


def score_endpoint(base: str, path: str) -> dict:
    chat_body = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": CHAT_PROBE_MESSAGE}],
        "temperature": 0.7,
        "stream": False,
    }
    completion_body = {
        "prompt": "Once upon a time, in a world",
        "max_tokens": 64,
        "temperature": 0.7,
    }
    stream_body = {**chat_body, "stream": True}

    get_resp = http_probe(base, path, "GET")
    chat_a = http_probe(base, path, "POST", chat_body)
    chat_b = http_probe(base, path, "POST", chat_body)
    comp_resp = http_probe(base, path, "POST", completion_body)
    stream_resp = http_probe(base, path, "POST", stream_body, stream_detect=True)

    ws_result = {"upgraded": False, "replies": [], "status": None}
    if path.lower().endswith("/ws") or "websocket" in path.lower():
        ws_result = ws_probe(base, path)

    request_signals = request_analysis(path, chat_body)

    statuses = [r["status"] for r in (get_resp, chat_a, comp_resp)
                if r.get("ok") and r["status"] is not None]
    live_statuses = [s for s in statuses if s not in (404, 405)]
    ws_upgraded = bool(ws_result.get("upgraded"))
    alive = bool(live_statuses) or ws_upgraded
    status = (max(live_statuses) if live_statuses
              else (101 if ws_upgraded else (max(statuses) if statuses else None)))

    if not alive:
        return {
            "endpoint": path,
            "status": status,
            "alive": False,
            "ai_confidence": 0,
            "type": "Other",
            "streaming": False,
            "verdict": "Not AI",
            "signals": [{"signal": "not_found", "weight": 0,
                         "detail": "endpoint does not respond to any probe"}],
            "evidence": {
                "get": {"status": get_resp.get("status")},
                "chat": {"status": chat_a.get("status"), "total_ms": chat_a.get("total_ms")},
                "stream": {"status": stream_resp.get("status"),
                           "chunks": stream_resp.get("chunks"),
                           "content_type": stream_resp["headers"].get("content-type", "")},
                "ws": {"upgraded": ws_result.get("upgraded"),
                       "replies": ws_result.get("replies", [])},
            },
        }

    response_signals = response_analysis(chat_a) + response_analysis(comp_resp) \
        + response_analysis(stream_resp)
    behavior_signals = behavior_analysis(base, path, chat_a, chat_b,
                                         stream_resp, ws_result)

    all_signals = []
    seen = set()
    for s in request_signals + response_signals + behavior_signals:
        key = (s["signal"], s["detail"])
        if key in seen:
            continue
        seen.add(key)
        all_signals.append(s)

    raw = sum(s["weight"] for s in all_signals)
    score = max(0, min(100, raw))

    kind, streaming = classify_endpoint(path, request_signals, response_signals,
                                        behavior_signals, ws_result)

    return {
        "endpoint": path,
        "status": status,
        "alive": True,
        "ai_confidence": score,
        "type": kind,
        "streaming": streaming,
        "verdict": "AI" if score >= 50 else "Not AI",
        "signals": all_signals,
        "evidence": {
            "get": {"status": get_resp.get("status")},
            "chat": {"status": chat_a.get("status"), "total_ms": chat_a.get("total_ms")},
            "stream": {"status": stream_resp.get("status"),
                       "chunks": stream_resp.get("chunks"),
                       "content_type": stream_resp["headers"].get("content-type", "")},
            "ws": {"upgraded": ws_result.get("upgraded"),
                   "replies": ws_result.get("replies", [])},
        },
    }


def print_endpoint(result: dict):
    r = result
    status = r["status"] if r["alive"] else f"{r['status'] or '?'} (not found)"
    print(f"  Endpoint: {r['endpoint']}    [HTTP {status}]")
    print(f"  AI Confidence: {r['ai_confidence']}%")
    print(f"  Type: {r['type']}")
    print(f"  Streaming: {'Yes' if r['streaming'] else 'No'}")
    print(f"  Verdict: {r['verdict']}")
    for s in sorted(r["signals"], key=lambda x: -x["weight"]):
        sign = "+" if s["weight"] > 0 else "-"
        print(f"    {sign} {s['signal']:<18} ({s['weight']:+d})  {s['detail']}")
    print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python ai_detector.py <base_url> [report.json] [endpoint ...]")
        print("Example: python ai_detector.py http://127.0.0.1:8001 ai_mock_report.json")
        sys.exit(1)

    base = sys.argv[1].rstrip("/")
    report_path = sys.argv[2] if len(sys.argv) > 2 and ".json" in sys.argv[2] else "ai_detector_report.json"
    endpoints = [a for a in sys.argv[2:] if not a.endswith(".json")] or CANDIDATES

    print(f"[*] AI Detector target: {base}")
    print(f"[*] Endpoints to analyze: {', '.join(endpoints)}\n")

    results = []
    for i, path in enumerate(endpoints, 1):
        print(f"[{i}] Analyzing {path}")
        result = score_endpoint(base, path)
        results.append(result)
        print_endpoint(result)

    report = {
        "target": base,
        "method": "request+response+behavior analysis",
        "endpoints": [
            {k: r[k] for k in ("endpoint", "status", "alive", "ai_confidence",
                               "type", "streaming", "verdict", "signals", "evidence")}
            for r in results
        ],
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print("[+] Summary")
    print(f"    {'Endpoint':<24} {'Score':>6}  {'Type':<18} {'Stream':<8} Verdict")
    for r in results:
        print(f"    {r['endpoint']:<24} {r['ai_confidence']:>5}%  "
              f"{r['type']:<18} {'Yes' if r['streaming'] else 'No':<8} {r['verdict']}")
    ai_count = sum(1 for r in results if r["verdict"] == "AI")
    print(f"\n[+] {ai_count}/{len(results)} endpoints classified as AI")
    print(f"[*] Report saved to {report_path}")


if __name__ == "__main__":
    main()
